"""Usage and run-event persistence helpers."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import async_session_factory
from app.models.usage import UsageRecord
from app.models.run_event import RunEvent

logger = logging.getLogger(__name__)

_pending_usage: list[dict] = []
_pending_events: list[dict] = []
_flush_scheduled = False


def record_usage_sync(record: dict) -> None:
    """Queue a usage row from a sync LangGraph node (flushed on the event loop)."""
    _pending_usage.append(record)
    _schedule_flush()


def persist_event_sync(
    run_id: str, project_id: str, event_id: str, event: str, data: dict
) -> None:
    _pending_events.append({
        "id": event_id,
        "run_id": run_id,
        "project_id": project_id or "",
        "event_type": event,
        "payload": json.dumps(data),
    })
    _schedule_flush()


def _schedule_flush() -> None:
    global _flush_scheduled
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _flush_scheduled:
        return
    _flush_scheduled = True
    loop.call_soon_threadsafe(lambda: asyncio.create_task(_flush()))


async def _flush() -> None:
    global _flush_scheduled, _pending_usage, _pending_events
    _flush_scheduled = False
    usage_batch = _pending_usage
    event_batch = _pending_events
    _pending_usage = []
    _pending_events = []
    if not usage_batch and not event_batch:
        return
    try:
        async with async_session_factory() as db:
            for u in usage_batch:
                db.add(UsageRecord(
                    run_id=u.get("run_id") or "",
                    project_id=u.get("project_id") or "",
                    node=u.get("node") or "",
                    tool=u.get("tool") or "openai",
                    model=u.get("model") or "",
                    tokens_in=int(u.get("tokens_in") or 0),
                    tokens_out=int(u.get("tokens_out") or 0),
                    cost_usd=float(u.get("cost_usd") or 0),
                ))
            for e in event_batch:
                db.add(RunEvent(
                    id=e["id"],
                    run_id=e["run_id"],
                    project_id=e["project_id"],
                    event_type=e["event_type"],
                    payload=e["payload"],
                ))
            await db.commit()
    except Exception:
        logger.exception("Failed to flush usage/events")
        _pending_usage = usage_batch + _pending_usage
        _pending_events = event_batch + _pending_events


async def get_run_usage(db: AsyncSession, project_id: str, run_id: str) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(UsageRecord).where(
                UsageRecord.run_id == run_id,
                UsageRecord.project_id == project_id,
            ).order_by(UsageRecord.created_at)
        )
    ).scalars().all()

    by_node: dict[str, dict] = {}
    total_in = total_out = 0
    total_cost = 0.0
    details = []
    for r in rows:
        details.append({
            "id": r.id,
            "node": r.node,
            "tool": r.tool,
            "model": r.model,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "cost_usd": r.cost_usd,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
        total_in += r.tokens_in
        total_out += r.tokens_out
        total_cost += r.cost_usd
        bucket = by_node.setdefault(r.node or "unknown", {
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0,
        })
        bucket["tokens_in"] += r.tokens_in
        bucket["tokens_out"] += r.tokens_out
        bucket["cost_usd"] += r.cost_usd
        bucket["calls"] += 1

    return {
        "run_id": run_id,
        "project_id": project_id,
        "totals": {
            "tokens_in": total_in,
            "tokens_out": total_out,
            "cost_usd": round(total_cost, 8),
            "calls": len(details),
        },
        "by_node": by_node,
        "details": details,
    }


async def list_events_after(
    db: AsyncSession, run_id: str, last_event_id: str | None = None
) -> list[RunEvent]:
    q = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at)
    rows = list((await db.execute(q)).scalars().all())
    if not last_event_id:
        return rows
    idx = next((i for i, r in enumerate(rows) if r.id == last_event_id), None)
    if idx is None:
        return rows
    return rows[idx + 1 :]
