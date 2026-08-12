from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.models.user import User
from app.schemas.common import SuccessResponse, PaginatedResponse
from app.schemas.document import DocumentOut
from app.services import document_service

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["Documents"])


@router.post("", response_model=SuccessResponse[DocumentOut], status_code=201)
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.project_service import get_project
    await get_project(db, project_id)

    doc = await document_service.upload_document(db, project_id, file, actor=user.username)
    return SuccessResponse(data=DocumentOut.model_validate(doc))


@router.get("", response_model=PaginatedResponse[DocumentOut])
async def list_documents(
    project_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, total = await document_service.list_documents(db, project_id, page, page_size)
    return PaginatedResponse(
        data=[DocumentOut.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/{doc_id}", response_model=SuccessResponse[DocumentOut])
async def get_document(
    project_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = await document_service.get_document(db, project_id, doc_id)
    return SuccessResponse(data=DocumentOut.model_validate(doc))


@router.get("/{doc_id}/sections")
async def get_document_sections(
    project_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return structured outline (heading-aware sections) for a document."""
    from app.services.ingestion.parser import parse_document
    from app.store.file_store import file_store

    doc = await document_service.get_document(db, project_id, doc_id)
    try:
        content = file_store.read_upload(doc.storage_path)
        parsed = parse_document(content, doc.filename, doc.mime_type)
        sections = [
            {
                "section_id": f"{doc.id}_s{i}",
                "heading": s.heading,
                "level": s.level,
                "page_or_slide": s.page_or_slide,
                "preview": (s.content or "")[:300],
            }
            for i, s in enumerate(parsed.sections)
        ]
    except Exception as exc:
        sections = []
        return SuccessResponse(data={"document_id": doc_id, "sections": sections, "error": str(exc)})
    return SuccessResponse(data={
        "document_id": doc_id,
        "section_count": len(sections),
        "chunk_count": doc.chunk_count,
        "sections": sections,
    })


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    project_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await document_service.delete_document(db, project_id, doc_id, actor=user.username)