from __future__ import annotations

import math

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, ProjectStatus, VALID_TRANSITIONS
from app.models.audit import AuditLog
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectStatusTransition


async def create_project(db: AsyncSession, body: ProjectCreate, actor: str) -> Project:
    project = Project(name=body.name, description=body.description)
    db.add(project)
    db.add(AuditLog(project_id=project.id, entity_type="project", entity_id=project.id, action="created", actor=actor))
    await db.commit()
    await db.refresh(project)
    return project


async def get_project(db: AsyncSession, project_id: str) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project {project_id} not found")
    return project


async def list_projects(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    status_filter: ProjectStatus | None = None,
) -> tuple[list[Project], int]:
    query = select(Project).order_by(Project.created_at.desc())
    count_query = select(func.count()).select_from(Project)

    if status_filter:
        query = query.where(Project.status == status_filter)
        count_query = count_query.where(Project.status == status_filter)

    total = (await db.execute(count_query)).scalar() or 0
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def update_project(db: AsyncSession, project_id: str, body: ProjectUpdate, actor: str) -> Project:
    project = await get_project(db, project_id)

    if project.status == ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot update an archived project")

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description

    db.add(AuditLog(project_id=project_id, entity_type="project", entity_id=project_id, action="updated", actor=actor))
    await db.commit()
    await db.refresh(project)
    return project


async def transition_project(
    db: AsyncSession, project_id: str, body: ProjectStatusTransition, actor: str
) -> Project:
    project = await get_project(db, project_id)
    if not project.can_transition_to(body.target_status):
        allowed = [s.value for s in VALID_TRANSITIONS.get(project.status, set())]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition from {project.status.value} to {body.target_status.value}. Allowed: {allowed}",
        )

    old_status = project.status
    project.status = body.target_status
    db.add(AuditLog(
        project_id=project_id, entity_type="project", entity_id=project_id,
        action="status_transition", detail=f"{old_status.value} -> {body.target_status.value}", actor=actor,
    ))
    await db.commit()
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, project_id: str, actor: str) -> None:
    project = await get_project(db, project_id)
    db.add(AuditLog(project_id=project_id, entity_type="project", entity_id=project_id, action="deleted", actor=actor))
    await db.delete(project)
    await db.commit()