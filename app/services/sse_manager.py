from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SSEManager:
    """In-memory per-run SSE fan-out with optional DB persistence for replay."""

    def __init__(self) -> None:
        self._channels: dict[str, list[asyncio.Queue]] = {}
        self._history: dict[str, list[dict]] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._channels.setdefault(run_id, []).append(q)
        try:
            self._loops[run_id] = asyncio.get_running_loop()
        except RuntimeError:
            pass
        logger.debug("SSE subscribe: run_id=%s, total=%d", run_id, len(self._channels[run_id]))
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        if run_id in self._channels:
            self._channels[run_id] = [x for x in self._channels[run_id] if x is not q]
            if not self._channels[run_id]:
                del self._channels[run_id]

    async def emit(self, run_id: str, event: str, data: dict) -> None:
        payload = {
            "id": data.get("event_id") or data.get("id"),
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        hist = self._history.setdefault(run_id, [])
        hist.append(payload)
        if len(hist) > 500:
            self._history[run_id] = hist[-500:]
        for q in self._channels.get(run_id, []):
            await q.put(payload)
        logger.debug("SSE emit: run_id=%s, event=%s", run_id, event)

    def emit_sync(self, run_id: str, event: str, data: dict) -> None:
        """Schedule emit from a sync LangGraph node thread."""
        try:
            loop = self._loops.get(run_id) or asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.emit(run_id, event, data), loop)
            else:
                hist = self._history.setdefault(run_id, [])
                hist.append({
                    "id": data.get("event_id"),
                    "event": event,
                    "data": data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception:
            logger.debug("emit_sync failed", exc_info=True)

    def persist_sync(
        self, run_id: str, project_id: str, event_id: str, event: str, data: dict
    ) -> None:
        try:
            from app.services.usage_service import persist_event_sync
            persist_event_sync(run_id, project_id, event_id, event, data)
        except Exception:
            logger.debug("persist_sync failed", exc_info=True)

    def history_after(self, run_id: str, last_event_id: str | None) -> list[dict]:
        hist = self._history.get(run_id, [])
        if not last_event_id:
            return list(hist)
        idx = next((i for i, p in enumerate(hist) if p.get("id") == last_event_id), None)
        if idx is None:
            return list(hist)
        return hist[idx + 1 :]


sse_manager = SSEManager()
