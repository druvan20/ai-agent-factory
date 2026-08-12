from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.models.project import ProjectStatus
from app.models.user import User
from app.schemas.common import SuccessResponse, PaginatedResponse
from app.schemas.project import (
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    ProjectStatusTransition,
)
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", response_model=SuccessResponse[ProjectOut], status_code=201)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await project_service.create_project(db, body, actor=user.username)
    return SuccessResponse(data=ProjectOut.model_validate(project))


@router.get("", response_model=PaginatedResponse[ProjectOut])
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: ProjectStatus | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, total = await project_service.list_projects(db, page, page_size, status_filter)
    return PaginatedResponse(
        data=[ProjectOut.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/{project_id}", response_model=SuccessResponse[ProjectOut])
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await project_service.get_project(db, project_id)
    return SuccessResponse(data=ProjectOut.model_validate(project))


@router.patch("/{project_id}", response_model=SuccessResponse[ProjectOut])
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await project_service.update_project(db, project_id, body, actor=user.username)
    return SuccessResponse(data=ProjectOut.model_validate(project))


@router.post("/{project_id}/transition", response_model=SuccessResponse[ProjectOut])
async def transition_project(
    project_id: str,
    body: ProjectStatusTransition,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await project_service.transition_project(db, project_id, body, actor=user.username)
    return SuccessResponse(data=ProjectOut.model_validate(project))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.store.file_store import file_store
    await project_service.delete_project(db, project_id, actor=user.username)
    file_store.delete_project_files(project_id)