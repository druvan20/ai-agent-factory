from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pattern import Pattern
from app.models.audit import AuditLog
from app.schemas.pattern import (
    PatternCreate,
    PatternUpdate,
    PatternOut,
    PatternSearchQuery,
    PatternSearchResult,
)
from app.services.embedding_service import embed_texts, embed_single
from app.store.vector_store import get_patterns_collection

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _build_embedding_text(p: Pattern | PatternCreate, name: str | None = None) -> str:
    """Combine key fields into one string for embedding."""
    n = name or (p.name if isinstance(p, Pattern) else p.name)
    parts = [
        f"Pattern: {n}",
        f"Intent: {p.intent}",
        f"Structure: {p.structure}",
        f"When to use: {p.when_to_use}",
    ]
    if p.anti_patterns:
        parts.append(f"Anti-patterns: {p.anti_patterns}")
    if p.prerequisites:
        parts.append(f"Prerequisites: {p.prerequisites}")
    tags = p.tags if isinstance(p.tags, list) else []
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


# ──────────────────────────── CRUD ────────────────────────────

async def create_pattern(db: AsyncSession, body: PatternCreate, actor: str) -> Pattern:
    slug = _slugify(body.name)
    existing = await db.execute(select(Pattern).where(Pattern.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Pattern '{body.name}' already exists")

    pattern = Pattern(
        name=body.name,
        slug=slug,
        category=body.category,
        intent=body.intent,
        structure=body.structure,
        when_to_use=body.when_to_use,
        anti_patterns=body.anti_patterns,
        prerequisites=body.prerequisites,
        references=body.references,
        tags=body.tags,
    )
    db.add(pattern)
    db.add(AuditLog(entity_type="pattern", entity_id=pattern.id, action="created", detail=body.name, actor=actor))
    await db.flush()

    await _upsert_pattern_embedding(pattern)

    await db.commit()
    await db.refresh(pattern)
    logger.info("Created pattern '%s' (id=%s)", pattern.name, pattern.id)
    return pattern


async def get_pattern(db: AsyncSession, pattern_id: str) -> Pattern:
    result = await db.execute(select(Pattern).where(Pattern.id == pattern_id))
    pattern = result.scalar_one_or_none()
    if pattern is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pattern {pattern_id} not found")
    return pattern


async def get_pattern_by_slug(db: AsyncSession, slug: str) -> Pattern | None:
    result = await db.execute(select(Pattern).where(Pattern.slug == slug))
    return result.scalar_one_or_none()


async def list_patterns(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
    tag: str | None = None,
) -> tuple[list[Pattern], int]:
    query = select(Pattern).order_by(Pattern.name)
    count_query = select(func.count()).select_from(Pattern)

    if category:
        query = query.where(Pattern.category == category)
        count_query = count_query.where(Pattern.category == category)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    patterns = list(result.scalars().all())

    if tag:
        patterns = [p for p in patterns if tag in (p.tags or [])]
        total = len(patterns)

    return patterns, total


async def update_pattern(db: AsyncSession, pattern_id: str, body: PatternUpdate, actor: str) -> Pattern:
    pattern = await get_pattern(db, pattern_id)

    if body.name is not None:
        pattern.name = body.name
        pattern.slug = _slugify(body.name)
    if body.category is not None:
        pattern.category = body.category
    if body.intent is not None:
        pattern.intent = body.intent
    if body.structure is not None:
        pattern.structure = body.structure
    if body.when_to_use is not None:
        pattern.when_to_use = body.when_to_use
    if body.anti_patterns is not None:
        pattern.anti_patterns = body.anti_patterns
    if body.prerequisites is not None:
        pattern.prerequisites = body.prerequisites
    if body.references is not None:
        pattern.references = body.references
    if body.tags is not None:
        pattern.tags = body.tags

    pattern.version += 1

    await _upsert_pattern_embedding(pattern)

    db.add(AuditLog(entity_type="pattern", entity_id=pattern_id, action="updated", detail=pattern.name, actor=actor))
    await db.commit()
    await db.refresh(pattern)
    logger.info("Updated pattern '%s' (id=%s, version=%d)", pattern.name, pattern.id, pattern.version)
    return pattern


async def delete_pattern(db: AsyncSession, pattern_id: str, actor: str) -> None:
    pattern = await get_pattern(db, pattern_id)

    collection = get_patterns_collection()
    try:
        collection.delete(ids=[pattern_id])
    except Exception:
        logger.warning("Failed to delete pattern vector for %s", pattern_id, exc_info=True)

    db.add(AuditLog(entity_type="pattern", entity_id=pattern_id, action="deleted", detail=pattern.name, actor=actor))
    await db.delete(pattern)
    await db.commit()
    logger.info("Deleted pattern '%s' (id=%s)", pattern.name, pattern_id)


# ──────────────────────────── BULK IMPORT ────────────────────────────

async def bulk_import_patterns(
    db: AsyncSession, items: list[PatternCreate], actor: str
) -> dict[str, Any]:
    created = 0
    skipped = 0
    errors: list[str] = []

    for item in items:
        slug = _slugify(item.name)
        existing = await get_pattern_by_slug(db, slug)
        if existing is not None:
            skipped += 1
            logger.debug("Skipping duplicate pattern: %s", item.name)
            continue

        try:
            pattern = Pattern(
                name=item.name,
                slug=slug,
                category=item.category,
                intent=item.intent,
                structure=item.structure,
                when_to_use=item.when_to_use,
                anti_patterns=item.anti_patterns,
                prerequisites=item.prerequisites,
                references=item.references,
                tags=item.tags,
            )
            db.add(pattern)
            db.add(AuditLog(entity_type="pattern", entity_id=pattern.id, action="bulk_imported", detail=item.name, actor=actor))
            await db.flush()
            await _upsert_pattern_embedding(pattern)
            created += 1
        except Exception as exc:
            errors.append(f"{item.name}: {exc}")
            logger.error("Error importing pattern '%s': %s", item.name, exc)

    await db.commit()
    logger.info("Bulk import complete: created=%d, skipped=%d, errors=%d", created, skipped, len(errors))
    return {"created": created, "skipped": skipped, "errors": errors}


# ──────────────────────────── SEMANTIC SEARCH ────────────────────────────

async def search_patterns(
    db: AsyncSession, query: PatternSearchQuery
) -> list[PatternSearchResult]:
    collection = get_patterns_collection()

    query_embedding = await asyncio.to_thread(embed_single, query.query)

    where_filter: dict | None = None
    if query.category:
        where_filter = {"category": query.category}

    results = await asyncio.to_thread(
        collection.query,
        query_embeddings=[query_embedding],
        n_results=query.top_k,
        where=where_filter,
        include=["documents", "distances", "metadatas"],
    )

    if not results["ids"] or not results["ids"][0]:
        return []

    ids = results["ids"][0]
    documents = results["documents"][0] if results["documents"] else [""] * len(ids)
    distances = results["distances"][0] if results["distances"] else [0.0] * len(ids)
    metadatas = results["metadatas"][0] if results["metadatas"] else [{}] * len(ids)

    search_results: list[PatternSearchResult] = []

    for i, pattern_id in enumerate(ids):
        pattern = await db.get(Pattern, pattern_id)
        if pattern is None:
            continue

        if query.tags:
            pattern_tags = set(pattern.tags or [])
            if not pattern_tags.intersection(query.tags):
                continue

        score = 1.0 - distances[i]
        snippet = (documents[i] or "")[:300]

        search_results.append(
            PatternSearchResult(
                pattern=PatternOut.model_validate(pattern),
                score=round(score, 4),
                snippet=snippet,
            )
        )

    logger.info("Semantic search for '%s': %d results", query.query[:80], len(search_results))
    return search_results


# ──────────────────────────── VECTOR HELPERS ────────────────────────────

async def _upsert_pattern_embedding(pattern: Pattern) -> None:
    """Embed pattern text and upsert into ChromaDB patterns collection."""
    text = _build_embedding_text(pattern)
    embedding = await asyncio.to_thread(embed_single, text)
    collection = get_patterns_collection()

    metadata = {
        "name": pattern.name,
        "slug": pattern.slug,
        "category": pattern.category,
        "tags": ",".join(pattern.tags) if pattern.tags else "",
    }

    await asyncio.to_thread(
        collection.upsert,
        ids=[pattern.id],
        documents=[text],
        embeddings=[embedding],
        metadatas=[metadata],
    )
    logger.debug("Upserted pattern embedding for '%s'", pattern.name)