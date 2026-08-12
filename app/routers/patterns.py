from __future__ import annotations

import json
import logging
import math

import yaml
from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.models.user import User
from app.schemas.common import SuccessResponse, PaginatedResponse
from app.schemas.pattern import (
    PatternCreate,
    PatternOut,
    PatternUpdate,
    PatternBulkImport,
    PatternSearchQuery,
    PatternSearchResult,
)
from app.services import pattern_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patterns", tags=["Patterns KB"])


# ────────────────────── CRUD ──────────────────────

@router.post("", response_model=SuccessResponse[PatternOut], status_code=201)
async def create_pattern(
    body: PatternCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    logger.info("Creating pattern: %s", body.name)
    pattern = await pattern_service.create_pattern(db, body, actor=user.username)
    return SuccessResponse(data=PatternOut.model_validate(pattern))


@router.get("", response_model=PaginatedResponse[PatternOut])
async def list_patterns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None),
    tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    logger.info("Listing patterns: page=%d, category=%s, tag=%s", page, category, tag)
    items, total = await pattern_service.list_patterns(db, page, page_size, category, tag)
    return PaginatedResponse(
        data=[PatternOut.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/{pattern_id}", response_model=SuccessResponse[PatternOut])
async def get_pattern(
    pattern_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    logger.info("Getting pattern: %s", pattern_id)
    pattern = await pattern_service.get_pattern(db, pattern_id)
    return SuccessResponse(data=PatternOut.model_validate(pattern))


@router.patch("/{pattern_id}", response_model=SuccessResponse[PatternOut])
async def update_pattern(
    pattern_id: str,
    body: PatternUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    logger.info("Updating pattern: %s", pattern_id)
    pattern = await pattern_service.update_pattern(db, pattern_id, body, actor=user.username)
    return SuccessResponse(data=PatternOut.model_validate(pattern))


@router.delete("/{pattern_id}", status_code=204)
async def delete_pattern(
    pattern_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    logger.info("Deleting pattern: %s", pattern_id)
    await pattern_service.delete_pattern(db, pattern_id, actor=user.username)


# ────────────────────── BULK IMPORT ──────────────────────

@router.post("/bulk/json", response_model=SuccessResponse[dict])
async def bulk_import_json(
    body: PatternBulkImport,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    logger.info("Bulk importing %d patterns from JSON body", len(body.patterns))
    result = await pattern_service.bulk_import_patterns(db, body.patterns, actor=user.username)
    return SuccessResponse(data=result)


@router.post("/bulk/file", response_model=SuccessResponse[dict])
async def bulk_import_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a JSON or YAML file containing patterns to import."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    filename = (file.filename or "").lower()

    logger.info("Bulk importing patterns from file: %s", file.filename)

    try:
        if filename.endswith((".yaml", ".yml")):
            raw = yaml.safe_load(text)
        else:
            raw = json.loads(text)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse file: {exc}",
        )

    if isinstance(raw, dict) and "patterns" in raw:
        raw = raw["patterns"]
    if not isinstance(raw, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expected a list of patterns or {patterns: [...]}")

    items = [PatternCreate(**p) for p in raw]
    result = await pattern_service.bulk_import_patterns(db, items, actor=user.username)
    return SuccessResponse(data=result)


# ────────────────────── SEMANTIC SEARCH ──────────────────────

@router.post("/search", response_model=SuccessResponse[list[PatternSearchResult]])
async def search_patterns(
    body: PatternSearchQuery,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    logger.info("Searching patterns: query='%s', category=%s, tags=%s, top_k=%d",
                body.query[:80], body.category, body.tags, body.top_k)
    results = await pattern_service.search_patterns(db, body)
    return SuccessResponse(data=results)