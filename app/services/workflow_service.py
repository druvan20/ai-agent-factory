"""Workflow orchestration service — starts, resumes, and manages LangGraph runs."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import async_session_factory
from app.models.workflow_run import WorkflowRun, WorkflowType, RunStatus
from app.models.document import Document, DocumentStatus
from app.models.audit import AuditLog
from app.services.sse_manager import sse_manager
from app.services.ws_manager import ws_manager
from app.services.ingestion.parser import parse_document
from app.store.file_store import file_store

logger = logging.getLogger(__name__)

_graph_cache: dict[str, Any] = {}
_checkpointer_cache: dict[str, Any] = {}

# Approval-gate node names for each workflow (used by generic handler)
_APPROVAL_NODES = {"approval_gate", "approval_gate_heavy", "approval_gate_light"}


def _get_w1_graph():
    """Lazy-init the W1 graph with checkpointer (singleton)."""
    if "w1" not in _graph_cache:
        from app.workflows.w1_requirements import build_w1_graph_with_checkpointer
        graph, checkpointer = build_w1_graph_with_checkpointer()
        _graph_cache["w1"] = graph
        _checkpointer_cache["w1"] = checkpointer
    return _graph_cache["w1"]


def _get_w2_graph():
    """Lazy-init the W2 graph with checkpointer (singleton)."""
    if "w2" not in _graph_cache:
        from app.workflows.w2_planning import build_w2_graph_with_checkpointer
        graph, checkpointer = build_w2_graph_with_checkpointer()
        _graph_cache["w2"] = graph
        _checkpointer_cache["w2"] = checkpointer
    return _graph_cache["w2"]


def _get_w3_graph():
    """Lazy-init the W3 graph with checkpointer (singleton)."""
    if "w3" not in _graph_cache:
        from app.workflows.w3_codegen import build_w3_graph_with_checkpointer
        graph, checkpointer = build_w3_graph_with_checkpointer()
        _graph_cache["w3"] = graph
        _checkpointer_cache["w3"] = checkpointer
    return _graph_cache["w3"]


def _get_graph_for_run(run: WorkflowRun) -> Any:
    """Return the correct graph for a workflow run."""
    if run.workflow_type == WorkflowType.CODEGEN:
        return  _get_w3_graph()
    if run.workflow_type == WorkflowType.PLANNING:
        return  _get_w2_graph()
    return  _get_w1_graph()


# ───────────────── W1: Requirements Workflow ─────────────────

async def start_requirements_workflow(
    db: AsyncSession, project_id: str, max_rounds: int, actor: str
) -> WorkflowRun:
    """Start Workflow 1 for a project. Gathers doc texts and kicks off the graph."""

    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.status == DocumentStatus.READY,
        )
    )
    docs = list(result.scalars().all())
    if not docs:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No ready documents in project. Upload and wait for processing.")

    doc_texts = []
    for doc in docs:
        try:
            content = file_store.read_upload(doc.storage_path)
            parsed = parse_document(content, doc.filename, doc.mime_type)
            doc_texts.append(f"=== {doc.filename} ===\n{parsed.raw_text}")
        except Exception as exc:
            logger.warning("Failed to read doc %s: %s", doc.id, exc)

    if not doc_texts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Could not read any documents")

    thread_id = uuid.uuid4().hex
    run = WorkflowRun(
        project_id=project_id,
        workflow_type=WorkflowType.REQUIREMENTS,
        status=RunStatus.RUNNING,
        thread_id=thread_id,
        max_clarification_rounds=max_rounds,
    )
    db.add(run)
    db.add(AuditLog(
        project_id=project_id, entity_type="workflow_run", entity_id=run.id,
        action="started", detail=f"W1 requirements, thread={thread_id}", actor=actor,
    ))
    await db.commit()
    await db.refresh(run)

    asyncio.create_task(_run_w1_graph(run.id, project_id, thread_id, doc_texts, max_rounds))

    return run


async def _run_w1_graph(
    run_id: str, project_id: str, thread_id: str,
    doc_texts: list[str], max_rounds: int
) -> None:
    """Background task: run the W1 graph, handle interrupts."""
    graph =  _get_w1_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "project_id": project_id,
        "run_id": run_id,
        "doc_texts": doc_texts,
        "extracted": {},
        "gaps": [],
        "clarification_round": 0,
        "max_rounds": max_rounds,
        "clarifications": [],
        "rejection_feedback": "",
        "spec_markdown": "",
        "spec_json": {},
        "status": "running",
        "error": None,
    }

    await sse_manager.emit(run_id, "workflow_started", {"workflow": "requirements"})

    try:
        result = await asyncio.to_thread(
            _invoke_sync, graph, initial_state, config
        )
        await _handle_graph_result(run_id, thread_id, result, graph, config)
    except Exception as exc:
        logger.exception("[W1] Graph execution failed: run_id=%s", run_id)
        await _update_run_status(run_id, RunStatus.FAILED, error=str(exc))
        await sse_manager.emit(run_id, "workflow_failed", {"error": str(exc)})


# ───────────────── W2: Planning Workflow ─────────────────

async def start_planning_workflow(
    db: AsyncSession, project_id: str, requirements_run_id: str, actor: str
) -> WorkflowRun:
    """Start Workflow 2 (Planning). Reads approved W1 spec and kicks off W2 graph."""

    w1_run = await _get_run(db, requirements_run_id)
    if w1_run.workflow_type != WorkflowType.REQUIREMENTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Provided run is not a requirements workflow run")
    if w1_run.status != RunStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Requirements run is not approved (status={w1_run.status.value})")
    if w1_run.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Requirements run belongs to a different project")

    try:
        requirements_json = json.loads(w1_run.artifact_json) if w1_run.artifact_json else {}
    except (json.JSONDecodeError, TypeError):
        requirements_json = {}

    if not requirements_json:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Requirements run has no structured artifact")

    requirements_md = w1_run.artifact_md or ""

    thread_id = uuid.uuid4().hex
    run = WorkflowRun(
        project_id=project_id,
        workflow_type=WorkflowType.PLANNING,
        status=RunStatus.RUNNING,
        thread_id=thread_id,
        max_clarification_rounds=0,
    )
    db.add(run)
    db.add(AuditLog(
        project_id=project_id, entity_type="workflow_run", entity_id=run.id,
        action="started",
        detail=f"W2 planning, thread={thread_id}, w1_run={requirements_run_id}",
        actor=actor,
    ))
    await db.commit()
    await db.refresh(run)

    asyncio.create_task(_run_w2_graph(
        run.id, project_id, thread_id, requirements_json, requirements_md
    ))

    return run


async def _run_w2_graph(
    run_id: str, project_id: str, thread_id: str,
    requirements_json: dict, requirements_md: str,
) -> None:
    """Background task: run the W2 planning graph."""
    settings = get_settings()
    graph =  _get_w2_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "project_id": project_id,
        "run_id": run_id,
        "requirements_json": requirements_json,
        "requirements_md": requirements_md,
        "complexity_score": 0,
        "route": "",
        "selected_patterns": [],
        "rejection_log": [],
        "research_results": [],
        "doc_research": [],
        "kb_research": [],
        "web_research": [],
        "llm_research": [],
        "research_context": [],
        "architecture_md": "",
        "architecture_json": {},
        "task_list": [],
        "critic_passed": False,
        "critic_score": 0.0,
        "critic_issues": [],
        "critic_feedback": "",
        "critic_iteration": 0,
        "rejection_feedback": "",
        "spec_markdown": "",
        "spec_json": {},
        "status": "running",
        "error": None,
        "messages": [],
    }

    await sse_manager.emit(run_id, "workflow_started", {"workflow": "planning"})

    try:
        result = await asyncio.to_thread(
            _invoke_sync, graph, initial_state, config
        )
        await _handle_graph_result(run_id, thread_id, result, graph, config)
    except Exception as exc:
        logger.exception("[W2] Graph execution failed: run_id=%s", run_id)
        await _update_run_status(run_id, RunStatus.FAILED, error=str(exc))
        await sse_manager.emit(run_id, "workflow_failed", {"error": str(exc)})


# ───────────────── W3: Code Generation Workflow ─────────────────

async def start_codegen_workflow(
    db: AsyncSession, project_id: str, planning_run_id: str, actor: str
) -> WorkflowRun:
    """Start Workflow 3 (Code Generation). Reads approved W2 plan and kicks off W3 graph."""

    w2_run = await _get_run(db, planning_run_id)
    if w2_run.workflow_type != WorkflowType.PLANNING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Provided run is not a planning workflow run")
    if w2_run.status != RunStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Planning run is not approved (status={w2_run.status.value})")
    if w2_run.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Planning run belongs to a different project")

    try:
        plan_json = json.loads(w2_run.artifact_json) if w2_run.artifact_json else {}
    except (json.JSONDecodeError, TypeError):
        plan_json = {}

    task_list = plan_json.get("task_list", [])
    if not task_list:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Planning run has no task list")

    architecture_json = plan_json.get("architecture", {})
    selected_patterns = plan_json.get("selected_patterns", [])

    thread_id = uuid.uuid4().hex
    run = WorkflowRun(
        project_id=project_id,
        workflow_type=WorkflowType.CODEGEN,
        status=RunStatus.RUNNING,
        thread_id=thread_id,
        max_clarification_rounds=0,
    )
    db.add(run)
    db.add(AuditLog(
        project_id=project_id, entity_type="workflow_run", entity_id=run.id,
        action="started",
        detail=f"W3 codegen, thread={thread_id}, w2_run={planning_run_id}, tasks={len(task_list)}",
        actor=actor,
    ))
    await db.commit()
    await db.refresh(run)

    asyncio.create_task(_run_w3_graph(
        run.id, project_id, thread_id, task_list, architecture_json, selected_patterns
    ))

    return run


async def _run_w3_graph(
    run_id: str, project_id: str, thread_id: str,
    task_list: list, architecture_json: dict, selected_patterns: list,
) -> None:
    """Background task: run the W3 codegen graph."""
    graph =  _get_w3_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "project_id": project_id,
        "run_id": run_id,
        "task_list": task_list,
        "architecture_json": architecture_json,
        "selected_patterns": selected_patterns,
        "current_task_index": 0,
        "current_task": {},
        "has_next_task": True,
        "completed_tasks": [],
        "generated_files": {},
        "current_files": {},
        "review_passed": False,
        "review_issues": [],
        "current_review_feedback": "",
        "retry_count": 0,
        "task_results": [],
        "bundle_path": "",
        "rejection_feedback": "",
        "spec_markdown": "",
        "spec_json": {},
        "status": "running",
        "error": None,
    }

    await sse_manager.emit(run_id, "workflow_started", {
        "workflow": "codegen", "total_tasks": len(task_list)
    })

    try:
        result = await asyncio.to_thread(
            _invoke_sync, graph, initial_state, config
        )
        await _handle_graph_result(run_id, thread_id, result, graph, config)
    except Exception as exc:
        logger.exception("[W3] Graph execution failed: run_id=%s", run_id)
        await _update_run_status(run_id, RunStatus.FAILED, error=str(exc))
        await sse_manager.emit(run_id, "workflow_failed", {"error": str(exc)})


# ───────────────── Generic Graph Helpers ─────────────────

def _invoke_sync(graph, state, config):
    """Synchronous graph invoke (runs in thread)."""
    return graph.invoke(state, config)


async def _handle_graph_result(
    run_id: str, thread_id: str, result: dict, graph: Any, config: dict
) -> None:
    """Check if graph completed or hit an interrupt (works for W1 and W2)."""
    snapshot = await asyncio.to_thread(graph.get_state, config)
    project_id = ""

    if snapshot.next:
        next_node = snapshot.next[0] if snapshot.next else ""
        logger.info("Graph interrupted at: %s, run_id=%s", next_node, run_id)
        state = snapshot.values
        project_id = state.get("project_id", "")
        await _update_current_node(run_id, next_node)

        if next_node == "clarification_gate":
            gaps = state.get("gaps", [])
            round_num = state.get("clarification_round", 0) + 1
            max_rounds = state.get("max_rounds", 3)
            request_id = f"{thread_id}:clarification:{round_num}"

            await _update_run_status(run_id, RunStatus.WAITING_CLARIFICATION)
            await _update_run_round(run_id, round_num)
            questions = [
                {"id": f"q{i+1}", "question": g, "context": ""}
                for i, g in enumerate(gaps)
            ]
            payload = {"questions": questions, "round": round_num, "max_rounds": max_rounds}
            await sse_manager.emit(run_id, "clarification_requested", payload)
            await ws_manager.send_envelope(
                run_id, "clarification_request", request_id, payload, project_id=project_id
            )

        elif next_node in _APPROVAL_NODES:
            await _update_run_status(run_id, RunStatus.WAITING_APPROVAL)
            await _update_run_artifacts(
                run_id,
                state.get("spec_markdown", ""),
                json.dumps(state.get("spec_json", {}), indent=2),
                bundle_path=state.get("bundle_path"),
            )
            # Snapshot patterns for planning runs
            patterns = (state.get("spec_json") or {}).get("selected_patterns") or state.get("selected_patterns")
            if patterns:
                await _update_snapshotted_patterns(run_id, patterns)

            request_id = f"{thread_id}:approval"
            route_info = state.get("route", "")
            payload = {
                "artifact": {
                    "preview": state.get("spec_markdown", "")[:2000],
                    "route": route_info,
                    "bundle_path": state.get("bundle_path", ""),
                }
            }
            await sse_manager.emit(run_id, "approval_needed", payload)
            await ws_manager.send_envelope(
                run_id, "approval_request", request_id, payload, project_id=project_id
            )
        else:
            logger.warning("Unhandled interrupt node: %s, run_id=%s", next_node, run_id)
    else:
        final_state = snapshot.values
        s = final_state.get("status", "completed")
        await _update_current_node(run_id, None)
        if s == "approved":
            await _update_run_status(run_id, RunStatus.APPROVED)
            await _update_run_artifacts(
                run_id,
                final_state.get("spec_markdown", ""),
                json.dumps(final_state.get("spec_json", {}), indent=2),
                bundle_path=final_state.get("bundle_path"),
            )
            await sse_manager.emit(run_id, "run_completed", {"status": "approved"})
            await ws_manager.send_envelope(
                run_id, "run_completed", f"{thread_id}:done",
                {"status": "approved"}, project_id=final_state.get("project_id", ""),
            )
            ws_manager.clear_pending(run_id, project_id=final_state.get("project_id", ""))
        elif s == "failed":
            await _update_run_status(run_id, RunStatus.FAILED, error=final_state.get("error"))
            await sse_manager.emit(run_id, "error", {"error": final_state.get("error")})
        else:
            await _update_run_status(run_id, RunStatus.COMPLETED)
            await sse_manager.emit(run_id, "run_completed", {"status": s})


async def resume_run(db: AsyncSession, run_id: str, actor: str) -> WorkflowRun:
    """Manually resume a run after transient failure from the last checkpoint."""
    run = await _get_run(db, run_id)
    if run.status in (RunStatus.APPROVED, RunStatus.COMPLETED):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run already finished")
    graph = _get_graph_for_run(run)
    config = {"configurable": {"thread_id": run.thread_id}}
    run.status = RunStatus.RUNNING
    db.add(AuditLog(
        project_id=run.project_id, entity_type="workflow_run", entity_id=run_id,
        action="resumed", detail="manual resume from checkpoint", actor=actor,
    ))
    await db.commit()
    await db.refresh(run)

    async def _continue():
        try:
            # Re-invoke with None / empty update to continue from checkpoint if interrupted;
            # otherwise get_state and if next is empty, just re-handle.
            snapshot = await asyncio.to_thread(graph.get_state, config)
            if snapshot.next:
                # Still waiting on HITL — re-push prompt
                await _handle_graph_result(run_id, run.thread_id, snapshot.values, graph, config)
            else:
                result = await asyncio.to_thread(_invoke_sync, graph, None, config)
                await _handle_graph_result(run_id, run.thread_id, result or {}, graph, config)
        except Exception as exc:
            logger.exception("resume failed: %s", run_id)
            await _update_run_status(run_id, RunStatus.FAILED, error=str(exc))

    asyncio.create_task(_continue())
    return run


async def _update_current_node(run_id: str, node: str | None) -> None:
    async with async_session_factory() as db:
        run = await db.get(WorkflowRun, run_id)
        if run:
            run.current_node = node
            await db.commit()


async def _update_snapshotted_patterns(run_id: str, patterns: list | dict) -> None:
    async with async_session_factory() as db:
        run = await db.get(WorkflowRun, run_id)
        if run:
            run.snapshotted_patterns = json.dumps(patterns)
            await db.commit()


# ───────────────── HITL: Clarification & Approval ─────────────────

async def resume_with_clarification(
    db: AsyncSession, run_id: str, answers: dict[str, str], actor: str
) -> WorkflowRun:
    """Resume a run after clarification — inject answers and continue."""
    run = await _get_run(db, run_id)
    if run.status != RunStatus.WAITING_CLARIFICATION:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Run is not waiting for clarification (status={run.status.value})")

    graph =  _get_graph_for_run(run)
    config = {"configurable": {"thread_id": run.thread_id}}

    run.status = RunStatus.RUNNING
    db.add(AuditLog(
        project_id=run.project_id, entity_type="workflow_run", entity_id=run_id,
        action="clarification_submitted", detail=f"round {run.clarification_round}", actor=actor,
    ))
    await db.commit()
    await db.refresh(run)

    await sse_manager.emit(run_id, "clarification_received", {"round": run.clarification_round})

    asyncio.create_task(_resume_graph(run_id, run.thread_id, answers, graph, config))
    return run


async def resume_with_approval(
    db: AsyncSession, run_id: str, approved: bool, feedback: str, actor: str
) -> WorkflowRun:
    """Resume a run after approval decision (works for W1 and W2)."""
    run = await _get_run(db, run_id)
    if run.status != RunStatus.WAITING_APPROVAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Run is not waiting for approval (status={run.status.value})")

    graph =  _get_graph_for_run(run)
    config = {"configurable": {"thread_id": run.thread_id}}

    action = "approved" if approved else "rejected"
    run.status = RunStatus.RUNNING
    db.add(AuditLog(
        project_id=run.project_id, entity_type="workflow_run", entity_id=run_id,
        action=f"approval_{action}", detail=feedback[:500] if feedback else "", actor=actor,
    ))
    await db.commit()
    await db.refresh(run)

    response = {"approved": approved, "feedback": feedback}
    await sse_manager.emit(run_id, f"approval_{action}", {"feedback": feedback})

    asyncio.create_task(_resume_graph(run_id, run.thread_id, response, graph, config))
    return run


async def _resume_graph(
    run_id: str, thread_id: str, human_response: dict, graph: Any, config: dict
) -> None:
    """Resume graph from interrupt with the human response."""
    try:
        result = await asyncio.to_thread(
            _resume_sync, graph, human_response, config
        )
        await _handle_graph_result(run_id, thread_id, result, graph, config)
    except Exception as exc:
        logger.exception("Graph resume failed: run_id=%s", run_id)
        await _update_run_status(run_id, RunStatus.FAILED, error=str(exc))
        await sse_manager.emit(run_id, "workflow_failed", {"error": str(exc)})


def _resume_sync(graph, human_response, config):
    """Synchronous graph resume via Command(resume=...)."""
    from langgraph.types import Command
    return graph.invoke(Command(resume=human_response), config)


# ───────────────────── DB Helpers ─────────────────────

async def _get_run(db: AsyncSession, run_id: str) -> WorkflowRun:
    result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    return run


async def get_run(db: AsyncSession, run_id: str) -> WorkflowRun:
    return await _get_run(db, run_id)


async def list_runs(
    db: AsyncSession, project_id: str, page: int = 1, page_size: int = 20,
    workflow_type: WorkflowType | None = None,
) -> tuple[list[WorkflowRun], int]:
    base_filter = [WorkflowRun.project_id == project_id]
    if workflow_type:
        base_filter.append(WorkflowRun.workflow_type == workflow_type)

    count_q = select(func.count()).select_from(WorkflowRun).where(*base_filter)
    total = (await db.execute(count_q)).scalar() or 0
    query = (
        select(WorkflowRun)
        .where(*base_filter)
        .order_by(WorkflowRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def _update_run_status(run_id: str, new_status: RunStatus, error: str | None = None) -> None:
    async with async_session_factory() as db:
        run = await db.get(WorkflowRun, run_id)
        if run:
            run.status = new_status
            if error:
                run.error_message = error[:2000]
            await db.commit()


async def _update_run_round(run_id: str, round_num: int) -> None:
    async with async_session_factory() as db:
        run = await db.get(WorkflowRun, run_id)
        if run:
            run.clarification_round = round_num
            await db.commit()


async def _update_run_artifacts(
    run_id: str, md: str, json_str: str, bundle_path: str | None = None
) -> None:
    async with async_session_factory() as db:
        run = await db.get(WorkflowRun, run_id)
        if run:
            run.artifact_md = md
            run.artifact_json = json_str
            if bundle_path:
                run.bundle_path = bundle_path
            await db.commit()