"""REST + SSE + WebSocket endpoints for Workflows (W1 Requirements, W2 Planning, W3 Codegen)."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, HTTPException, Header, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.auth.dependencies import get_current_user
from app.auth.jwt_handler import decode_access_token
from app.config import get_settings
from app.db.engine import get_db, async_session_factory
from app.models.user import User
from app.models.workflow_run import WorkflowType, WorkflowRun as WRModel
from app.schemas.common import SuccessResponse, PaginatedResponse
from app.schemas.workflow import (
    StartWorkflowRequest,
    StartPlanningRequest,
    StartCodegenRequest,
    WorkflowRunOut,
    ClarificationResponse,
    ApprovalRequest,
    RejectRequest,
)
from app.services import workflow_service
from app.services.sse_manager import sse_manager
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/workflows", tags=["Workflows"])
runs_router = APIRouter(prefix="/projects/{project_id}/runs", tags=["Runs"])
graph_router = APIRouter(tags=["Workflow Graphs"])


# ────────────────────── REST: Start workflows ──────────────────────

@router.post("/requirements", response_model=SuccessResponse[WorkflowRunOut], status_code=202)
@router.post("/requirements/start", response_model=SuccessResponse[WorkflowRunOut], status_code=202)
async def start_requirements_workflow(
    project_id: str,
    body: StartWorkflowRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    max_rounds = body.max_clarification_rounds if body else 3
    run = await workflow_service.start_requirements_workflow(db, project_id, max_rounds, actor=user.username)
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


@router.post("/planning", response_model=SuccessResponse[WorkflowRunOut], status_code=202)
@router.post("/planning/start", response_model=SuccessResponse[WorkflowRunOut], status_code=202)
async def start_planning_workflow(
    project_id: str,
    body: StartPlanningRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.start_planning_workflow(
        db, project_id, body.requirements_run_id, actor=user.username
    )
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


@router.post("/codegen", response_model=SuccessResponse[WorkflowRunOut], status_code=202)
@router.post("/codegen/start", response_model=SuccessResponse[WorkflowRunOut], status_code=202)
async def start_codegen_workflow(
    project_id: str,
    body: StartCodegenRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.start_codegen_workflow(
        db, project_id, body.planning_run_id, actor=user.username
    )
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


# ────────────────────── Bundle / artifacts ──────────────────────

@router.get("/runs/{run_id}/bundle")
@router.get("/runs/{run_id}/artifacts")
@runs_router.get("/{run_id}/artifacts")
async def download_bundle(
    project_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.workflow_type != WorkflowType.CODEGEN:
        raise HTTPException(status_code=400, detail="Not a codegen run")
    if not run.bundle_path:
        raise HTTPException(status_code=404, detail="No bundle available for this run")

    from app.services.workspace_manager import workspace_manager
    abs_path = workspace_manager.get_bundle_absolute(run.bundle_path)
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Bundle file not found on disk")

    return FileResponse(
        path=str(abs_path),
        media_type="application/zip",
        filename=f"codegen_{run_id}.zip",
    )


# ────────────────────── Run listing & detail ──────────────────────

@router.get("/runs", response_model=PaginatedResponse[WorkflowRunOut])
@runs_router.get("", response_model=PaginatedResponse[WorkflowRunOut])
async def list_runs(
    project_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    workflow_type: WorkflowType | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, total = await workflow_service.list_runs(db, project_id, page, page_size, workflow_type)
    return PaginatedResponse(
        data=[WorkflowRunOut.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/runs/{run_id}", response_model=SuccessResponse[WorkflowRunOut])
@runs_router.get("/{run_id}", response_model=SuccessResponse[WorkflowRunOut])
async def get_run(
    project_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


@router.get("/runs/{run_id}/tasks")
@runs_router.get("/{run_id}/tasks")
async def get_tasks(
    project_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        artifact = json.loads(run.artifact_json) if run.artifact_json else {}
    except json.JSONDecodeError:
        artifact = {}
    return SuccessResponse(data=artifact.get("task_list", []))


@router.get("/runs/{run_id}/research")
@runs_router.get("/{run_id}/research")
async def get_research(
    project_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        artifact = json.loads(run.artifact_json) if run.artifact_json else {}
    except json.JSONDecodeError:
        artifact = {}
    return SuccessResponse(data=artifact.get("research_log", artifact.get("research_citations", [])))


@router.get("/runs/{run_id}/usage")
@runs_router.get("/{run_id}/usage")
async def get_usage(
    project_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    from app.services.usage_service import get_run_usage
    return SuccessResponse(data=await get_run_usage(db, project_id, run_id))


# ────────────────────── HITL REST fallbacks ──────────────────────

@router.post("/runs/{run_id}/clarify", response_model=SuccessResponse[WorkflowRunOut])
@router.post("/runs/{run_id}/clarifications", response_model=SuccessResponse[WorkflowRunOut])
@runs_router.post("/{run_id}/clarifications", response_model=SuccessResponse[WorkflowRunOut])
async def submit_clarification(
    project_id: str,
    run_id: str,
    body: ClarificationResponse,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    run = await workflow_service.resume_with_clarification(db, run_id, body.answers, actor=user.username)
    ws_manager.clear_pending(run_id, project_id=project_id)
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


@router.post("/runs/{run_id}/approve", response_model=SuccessResponse[WorkflowRunOut])
@runs_router.post("/{run_id}/approve", response_model=SuccessResponse[WorkflowRunOut])
async def submit_approval(
    project_id: str,
    run_id: str,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    approved = body.approved if body.approved is not None else True
    run = await workflow_service.resume_with_approval(
        db, run_id, approved, body.feedback, actor=user.username
    )
    ws_manager.clear_pending(run_id, project_id=project_id)
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


@router.post("/runs/{run_id}/reject", response_model=SuccessResponse[WorkflowRunOut])
@runs_router.post("/{run_id}/reject", response_model=SuccessResponse[WorkflowRunOut])
async def submit_rejection(
    project_id: str,
    run_id: str,
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    run = await workflow_service.resume_with_approval(
        db, run_id, False, body.feedback, actor=user.username
    )
    ws_manager.clear_pending(run_id, project_id=project_id)
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


@router.post("/runs/{run_id}/resume", response_model=SuccessResponse[WorkflowRunOut])
@runs_router.post("/{run_id}/resume", response_model=SuccessResponse[WorkflowRunOut])
async def resume_run(
    project_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await workflow_service.get_run(db, run_id)
    if run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    run = await workflow_service.resume_run(db, run_id, actor=user.username)
    return SuccessResponse(data=WorkflowRunOut.model_validate(run))


# ────────────────────── SSE ──────────────────────

@router.get("/runs/{run_id}/events")
@runs_router.get("/{run_id}/events")
async def sse_stream(
    project_id: str,
    run_id: str,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    async def event_generator():
        # Replay persisted / in-memory history first
        for past in sse_manager.history_after(run_id, last_event_id):
            eid = past.get("id") or ""
            event = past.get("event", "message")
            data = json.dumps(past)
            yield f"id: {eid}\nevent: {event}\ndata: {data}\n\n"

        q = sse_manager.subscribe(run_id)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30)
                    event = payload.get("event", "message")
                    eid = payload.get("id") or ""
                    data = json.dumps(payload)
                    yield f"id: {eid}\nevent: {event}\ndata: {data}\n\n"
                    if event in ("workflow_completed", "workflow_failed", "run_completed", "error"):
                        if event in ("workflow_completed", "run_completed", "workflow_failed"):
                            break
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            sse_manager.unsubscribe(run_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ────────────────────── WebSocket HITL ──────────────────────

ws_router = APIRouter()


@ws_router.websocket("/projects/{project_id}/runs/{run_id}/hitl")
@ws_router.websocket("/api/v1/projects/{project_id}/runs/{run_id}/hitl")
@ws_router.websocket("/ws/runs/{run_id}")  # legacy alias
async def websocket_hitl(
    websocket: WebSocket,
    run_id: str,
    project_id: str = "",
):
    """Per-run HITL channel. JWT via ?token= or Authorization subprotocol."""
    settings = get_settings()
    token = websocket.query_params.get("token")
    if not token:
        # subprotocol form: bearer.<jwt>
        for proto in websocket.headers.get("sec-websocket-protocol", "").split(","):
            proto = proto.strip()
            if proto.lower().startswith("bearer."):
                token = proto.split(".", 1)[1]
                break
    if not token:
        await websocket.close(code=4401, reason="Missing token")
        return
    try:
        decode_access_token(token)
    except ValueError:
        await websocket.close(code=4401, reason="Invalid token")
        return

    async with async_session_factory() as db:
        run = await db.get(WRModel, run_id)
        if run is None:
            await websocket.close(code=4404, reason="Run not found")
            return
        if project_id and run.project_id != project_id:
            await websocket.close(code=4404, reason="Run not found")
            return
        project_id = project_id or run.project_id

    await ws_manager.connect(run_id, websocket, project_id=project_id)

    async def _ping_loop():
        while True:
            await asyncio.sleep(settings.ws_ping_interval)
            try:
                await websocket.send_json({"type": "ping", "request_id": uuid.uuid4().hex, "payload": {}})
            except Exception:
                return

    ping_task = asyncio.create_task(_ping_loop())
    try:
        while True:
            raw = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=settings.ws_ping_interval + settings.ws_pong_timeout,
            )
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error", "request_id": uuid.uuid4().hex,
                    "payload": {"message": "Invalid JSON"},
                })
                continue

            msg_type = msg.get("type")
            request_id = msg.get("request_id") or ""
            payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else msg

            if msg_type == "pong":
                continue

            if msg_type not in ("clarification_response", "approval_response", "clarification", "approval"):
                await websocket.send_json({
                    "type": "unexpected_message",
                    "request_id": request_id or uuid.uuid4().hex,
                    "payload": {"message": f"Unexpected type: {msg_type}"},
                })
                await websocket.close(code=4400, reason="unexpected_message")
                return

            if request_id and not ws_manager.mark_response_handled(request_id):
                continue  # duplicate

            async with async_session_factory() as db:
                try:
                    if msg_type in ("clarification_response", "clarification"):
                        answers = payload.get("answers", msg.get("answers", {}))
                        if isinstance(answers, list):
                            answers = {
                                (a.get("id") or a.get("question") or str(i)): a.get("answer", "")
                                for i, a in enumerate(answers)
                            }
                        await workflow_service.resume_with_clarification(
                            db, run_id, answers, actor="ws_user"
                        )
                        ws_manager.clear_pending(run_id, project_id=project_id)
                    else:
                        decision = payload.get("decision")
                        if decision is not None:
                            approved = decision == "approve"
                            feedback = payload.get("feedback", "")
                        else:
                            approved = bool(payload.get("approved", msg.get("approved", False)))
                            feedback = payload.get("feedback", msg.get("feedback", ""))
                        await workflow_service.resume_with_approval(
                            db, run_id, approved, feedback, actor="ws_user"
                        )
                        ws_manager.clear_pending(run_id, project_id=project_id)
                except HTTPException as exc:
                    await websocket.send_json({
                        "type": "error",
                        "request_id": request_id or uuid.uuid4().hex,
                        "payload": {"message": str(exc.detail)},
                    })

    except asyncio.TimeoutError:
        await websocket.close(code=4408, reason="pong timeout")
    except WebSocketDisconnect:
        logger.info("WS disconnected: run_id=%s", run_id)
    except Exception:
        logger.exception("WS error: run_id=%s", run_id)
    finally:
        ping_task.cancel()
        ws_manager.disconnect(run_id, project_id=project_id)


# ────────────────────── Graph visualization ──────────────────────

@graph_router.get("/workflows/{name}/graph.png")
async def workflow_graph_png(name: str):
    from pathlib import Path
    cache_dir = Path("data/graph_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{name}.png"

    if not cache_path.exists():
        builders = {
            "requirements": ("app.workflows.w1_requirements", "build_w1_graph"),
            "planning": ("app.workflows.w2_planning", "build_w2_graph"),
            "codegen": ("app.workflows.w3_codegen", "build_w3_graph"),
        }
        if name not in builders:
            raise HTTPException(status_code=404, detail="Unknown workflow name")
        mod_name, fn_name = builders[name]
        import importlib
        mod = importlib.import_module(mod_name)
        graph = getattr(mod, fn_name)()
        try:
            png = graph.get_graph().draw_mermaid_png()
            cache_path.write_bytes(png)
        except Exception as exc:
            # Fallback: return mermaid text as plain response error with 501
            mermaid = graph.get_graph().draw_mermaid()
            raise HTTPException(
                status_code=501,
                detail=f"PNG render unavailable ({exc}). Mermaid:\n{mermaid[:2000]}",
            )

    return FileResponse(str(cache_path), media_type="image/png")
