"""Reusable LangGraph node hook layer — observability backbone.

Wraps every node to:
  (a) emit SSE events (node_started / node_completed / error)
  (b) record latency and structured logs with run_id / node / latency_ms
  (c) persist run_events for Last-Event-ID replay
"""
from __future__ import annotations

import functools
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def node_hook(node_name: str) -> Callable[[Callable[..., dict]], Callable[..., dict]]:
    """Decorator factory: wrap a sync LangGraph node with pre/post hooks."""

    def decorator(fn: Callable[..., dict]) -> Callable[..., dict]:
        @functools.wraps(fn)
        def wrapper(state: dict, *args: Any, **kwargs: Any) -> dict:
            run_id = state.get("run_id") or state.get("_run_id") or ""
            project_id = state.get("project_id") or ""
            t0 = time.perf_counter()
            event_id = uuid.uuid4().hex

            logger.info(
                "pre_node run_id=%s project_id=%s node=%s",
                run_id, project_id, node_name,
            )
            _emit_sse(run_id, "node_started", {
                "node": node_name,
                "event_id": event_id,
                "project_id": project_id,
            })
            _persist_event(run_id, project_id, event_id, "node_started", {"node": node_name})

            try:
                result = fn(state, *args, **kwargs)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                logger.info(
                    "post_node run_id=%s node=%s latency_ms=%d",
                    run_id, node_name, latency_ms,
                )
                done_id = uuid.uuid4().hex
                _emit_sse(run_id, "node_completed", {
                    "node": node_name,
                    "event_id": done_id,
                    "latency_ms": latency_ms,
                })
                _persist_event(
                    run_id, project_id, done_id, "node_completed",
                    {"node": node_name, "latency_ms": latency_ms},
                )
                return result
            except Exception as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                logger.exception(
                    "node_error run_id=%s node=%s latency_ms=%d",
                    run_id, node_name, latency_ms,
                )
                err_id = uuid.uuid4().hex
                _emit_sse(run_id, "error", {
                    "node": node_name,
                    "event_id": err_id,
                    "error": str(exc),
                    "latency_ms": latency_ms,
                })
                _persist_event(
                    run_id, project_id, err_id, "error",
                    {"node": node_name, "error": str(exc), "latency_ms": latency_ms},
                )
                raise

        return wrapper

    return decorator


def wrap_nodes(builder: Any, node_map: dict[str, Callable[..., dict]]) -> None:
    """Register nodes on a StateGraph builder with node_hook applied."""
    for name, fn in node_map.items():
        builder.add_node(name, node_hook(name)(fn))


def _emit_sse(run_id: str, event: str, data: dict) -> None:
    if not run_id:
        return
    try:
        from app.services.sse_manager import sse_manager
        # Fire-and-forget from sync context via stored loop or sync emit buffer
        sse_manager.emit_sync(run_id, event, data)
    except Exception:
        logger.debug("SSE emit skipped", exc_info=True)


def _persist_event(run_id: str, project_id: str, event_id: str, event: str, data: dict) -> None:
    if not run_id:
        return
    try:
        from app.services.sse_manager import sse_manager
        sse_manager.persist_sync(run_id, project_id, event_id, event, data)
    except Exception:
        logger.debug("event persist skipped", exc_info=True)
