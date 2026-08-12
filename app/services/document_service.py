from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from fastapi import HTTPException, status, UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.document import Document, DocumentStatus
from app.models.audit import AuditLog
from app.services.ingestion.parser import (
    parse_document,
    SUPPORTED_MIMES,
    EXTENSION_TO_MIME,
)
from app.services.ingestion.chunker import heading_aware_chunk
from app.services.ingestion.embedder import embed_and_store
from app.store.file_store import file_store

logger = logging.getLogger(__name__)


def _resolve_mime(upload: UploadFile) -> str:
    """Resolve MIME type from content_type header or file extension."""
    mime = upload.content_type
    if mime and mime in SUPPORTED_MIMES:
        return mime
    ext = Path(upload.filename or "").suffix.lower()
    resolved = EXTENSION_TO_MIME.get(ext)
    if resolved:
        return resolved
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=f"Unsupported file type. Supported: {', '.join(EXTENSION_TO_MIME.keys())}",
    )


async def upload_document(
    db: AsyncSession, project_id: str, upload: UploadFile, actor: str
) -> Document:
    settings = get_settings()
    mime = _resolve_mime(upload)

    content = await upload.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.max_upload_size_mb} MB limit",
        )
    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    content_hash = hashlib.sha256(content).hexdigest()

    existing = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.content_hash == content_hash,
        )
    )
    dup = existing.scalar_one_or_none()
    if dup is not None:
        return dup

    doc = Document(
        project_id=project_id,
        filename=upload.filename or "unnamed",
        mime_type=mime,
        file_size=len(content),
        content_hash=content_hash,
        storage_path="",
        status=DocumentStatus.UPLOADING,
    )
    db.add(doc)
    await db.flush()

    storage_path, _ = await file_store.save_upload(project_id, doc.id, doc.filename, content)
    doc.storage_path = storage_path
    doc.status = DocumentStatus.PARSING

    db.add(AuditLog(
        project_id=project_id, entity_type="document", entity_id=doc.id,
        action="uploaded", detail=f"{doc.filename} ({len(content)} bytes)", actor=actor,
    ))
    await db.commit()
    await db.refresh(doc)

    asyncio.create_task(_process_document(doc.id, project_id, content, doc.filename, mime))

    return doc


async def _process_document(
    doc_id: str, project_id: str, content: bytes, filename: str, mime_type: str
) -> None:
    """Background task: parse → chunk → embed."""
    from app.db.engine import async_session_factory

    async with async_session_factory() as db:
        doc = await db.get(Document, doc_id)
        if doc is None:
            return

        try:
            doc.status = DocumentStatus.PARSING
            await db.commit()

            parsed = parse_document(content, filename, mime_type)

            doc.status = DocumentStatus.CHUNKING
            await db.commit()

            chunks = heading_aware_chunk(parsed.sections, doc_id, project_id)

            doc.status = DocumentStatus.EMBEDDING
            await db.commit()

            stored = await asyncio.to_thread(embed_and_store, chunks, project_id)

            doc.status = DocumentStatus.READY
            doc.chunk_count = stored
            await db.commit()

            logger.info("Document %s processed: %d chunks", doc_id, stored)

        except Exception as exc:
            logger.exception("Failed to process document %s", doc_id)
            doc.status = DocumentStatus.FAILED
            doc.error_message = str(exc)[:2000]
            await db.commit()


async def get_document(db: AsyncSession, project_id: str, doc_id: str) -> Document:
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {doc_id} not found")
    return doc


async def list_documents(
    db: AsyncSession, project_id: str, page: int = 1, page_size: int = 20
) -> tuple[list[Document], int]:
    count_q = select(func.count()).select_from(Document).where(Document.project_id == project_id)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def delete_document(db: AsyncSession, project_id: str, doc_id: str, actor: str) -> None:
    doc = await get_document(db, project_id, doc_id)

    from app.store.vector_store import get_documents_collection
    collection = get_documents_collection(project_id)
    try:
        existing_ids = collection.get(where={"doc_id": doc_id})["ids"]
        if existing_ids:
            collection.delete(ids=existing_ids)
    except Exception:
        logger.warning("Failed to delete vectors for doc %s", doc_id, exc_info=True)

    db.add(AuditLog(
        project_id=project_id, entity_type="document", entity_id=doc_id,
        action="deleted", detail=doc.filename, actor=actor,
    ))
    await db.delete(doc)
    await db.commit()