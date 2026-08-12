"""Traceability map: docs → requirements → patterns → tasks → files."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.models.user import User
from app.models.document import Document
from app.models.workflow_run import WorkflowRun, WorkflowType, RunStatus
from app.schemas.common import SuccessResponse
from app.services.project_service import get_project

router = APIRouter(prefix="/projects/{project_id}", tags=["Traceability"])


@router.get("/traceability")
async def get_traceability(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await get_project(db, project_id)

    docs = list(
        (await db.execute(select(Document).where(Document.project_id == project_id))).scalars().all()
    )

    runs = list(
        (
            await db.execute(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == project_id)
                .order_by(WorkflowRun.created_at.desc())
            )
        ).scalars().all()
    )

    req_run = next(
        (r for r in runs if r.workflow_type == WorkflowType.REQUIREMENTS and r.status == RunStatus.APPROVED),
        next((r for r in runs if r.workflow_type == WorkflowType.REQUIREMENTS), None),
    )
    plan_run = next(
        (r for r in runs if r.workflow_type == WorkflowType.PLANNING and r.status == RunStatus.APPROVED),
        next((r for r in runs if r.workflow_type == WorkflowType.PLANNING), None),
    )
    code_run = next(
        (r for r in runs if r.workflow_type == WorkflowType.CODEGEN and r.status in (RunStatus.APPROVED, RunStatus.COMPLETED)),
        next((r for r in runs if r.workflow_type == WorkflowType.CODEGEN), None),
    )

    requirements = []
    if req_run and req_run.artifact_json:
        try:
            spec = json.loads(req_run.artifact_json)
            structured = spec.get("structured") or spec
            for fr in structured.get("functional_requirements", []):
                requirements.append({
                    "id": fr.get("id") or fr.get("title"),
                    "title": fr.get("title"),
                    "description": fr.get("description"),
                })
        except json.JSONDecodeError:
            pass

    patterns = []
    tasks = []
    if plan_run:
        if plan_run.snapshotted_patterns:
            try:
                patterns = json.loads(plan_run.snapshotted_patterns)
            except json.JSONDecodeError:
                patterns = []
        if plan_run.artifact_json:
            try:
                plan = json.loads(plan_run.artifact_json)
                patterns = patterns or plan.get("selected_patterns", [])
                tasks = plan.get("task_list", [])
            except json.JSONDecodeError:
                pass

    generated = []
    if code_run and code_run.artifact_json:
        try:
            code = json.loads(code_run.artifact_json)
            for tr in code.get("task_results", []):
                generated.append({
                    "task_id": tr.get("task_id"),
                    "files": tr.get("files", []),
                })
        except json.JSONDecodeError:
            pass

    # Build edges
    doc_to_req = []
    for doc in docs:
        for req in requirements:
            doc_to_req.append({
                "document_id": doc.id,
                "section_id": f"{doc.id}:*",
                "requirement_id": req["id"],
            })

    req_to_pattern = []
    for p in patterns:
        for rid in p.get("addresses_requirements", []) or []:
            req_to_pattern.append({"requirement_id": rid, "pattern": p.get("name")})

    req_to_task = []
    for t in tasks:
        # Heuristic: map via pattern_references / description
        req_to_task.append({
            "task_id": t.get("id"),
            "pattern_references": t.get("pattern_references", []),
            "title": t.get("title"),
        })

    task_to_file = []
    for g in generated:
        for fp in g.get("files", []):
            task_to_file.append({"task_id": g.get("task_id"), "path": fp})

    return SuccessResponse(data={
        "project_id": project_id,
        "documents": [{"id": d.id, "filename": d.filename, "chunk_count": d.chunk_count} for d in docs],
        "requirements": requirements,
        "selected_patterns": patterns,
        "tasks": [{"id": t.get("id"), "title": t.get("title"), "target_files": t.get("target_files", [])} for t in tasks],
        "edges": {
            "document_section_to_requirement": doc_to_req,
            "requirement_to_pattern": req_to_pattern,
            "requirement_to_task": req_to_task,
            "task_to_generated_file": task_to_file,
        },
        "runs": {
            "requirements_run_id": req_run.id if req_run else None,
            "planning_run_id": plan_run.id if plan_run else None,
            "codegen_run_id": code_run.id if code_run else None,
        },
    })
