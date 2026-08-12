from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSManager:
    """Per-run WebSocket HITL channel manager (single-process).

    At most one active socket per (project_id, run_id). Second connection
    closes the first with 409. Pending HITL prompts are cached for reconnect
    re-push (idempotent by request_id).
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._pending_prompt: dict[str, dict[str, Any]] = {}
        self._handled_responses: set[str] = set()

    def _key(self, project_id: str, run_id: str) -> str:
        return f"{project_id}:{run_id}" if project_id else run_id

    async def connect(
        self, run_id: str, ws: WebSocket, *, project_id: str = "", force_replace: bool = True
    ) -> None:
        key = self._key(project_id, run_id)
        old = self._connections.get(key)
        if old is not None and force_replace:
            try:
                await old.close(code=4009, reason="replaced by new subscriber")
            except Exception:
                pass
        await ws.accept()
        self._connections[key] = ws
        # also index by run_id alone for legacy callers
        self._connections[run_id] = ws
        logger.info("WS connected: project=%s run_id=%s", project_id, run_id)

        pending = self._pending_prompt.get(key) or self._pending_prompt.get(run_id)
        if pending:
            try:
                await ws.send_json(pending)
            except Exception:
                logger.warning("Failed to re-push pending HITL prompt for %s", run_id)

    def disconnect(self, run_id: str, *, project_id: str = "") -> None:
        key = self._key(project_id, run_id)
        self._connections.pop(key, None)
        self._connections.pop(run_id, None)
        logger.info("WS disconnected: project=%s run_id=%s", project_id, run_id)

    def set_pending_prompt(self, run_id: str, message: dict, *, project_id: str = "") -> None:
        key = self._key(project_id, run_id)
        self._pending_prompt[key] = message
        self._pending_prompt[run_id] = message

    def clear_pending(self, run_id: str, *, project_id: str = "") -> None:
        key = self._key(project_id, run_id)
        self._pending_prompt.pop(key, None)
        self._pending_prompt.pop(run_id, None)

    def get_pending(self, run_id: str, *, project_id: str = "") -> dict | None:
        key = self._key(project_id, run_id)
        return self._pending_prompt.get(key) or self._pending_prompt.get(run_id)

    def mark_response_handled(self, request_id: str) -> bool:
        """Return True if this is the first time we see request_id (accept), else duplicate."""
        if request_id in self._handled_responses:
            return False
        self._handled_responses.add(request_id)
        if len(self._handled_responses) > 5000:
            self._handled_responses = set(list(self._handled_responses)[-2500:])
        return True

    async def send_envelope(
        self, run_id: str, msg_type: str, request_id: str, payload: dict, *, project_id: str = ""
    ) -> bool:
        message = {
            "type": msg_type,
            "request_id": request_id,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if msg_type.endswith("_request"):
            self.set_pending_prompt(run_id, message, project_id=project_id)
        return await self.send_raw(run_id, message, project_id=project_id)

    async def send_raw(self, run_id: str, message: dict, *, project_id: str = "") -> bool:
        key = self._key(project_id, run_id)
        ws = self._connections.get(key) or self._connections.get(run_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            logger.warning("WS send failed for run_id=%s", run_id, exc_info=True)
            self.disconnect(run_id, project_id=project_id)
            return False

    async def send_to_run(self, run_id: str, event: str, data: dict) -> bool:
        """Legacy helper — maps old event names to typed envelopes."""
        import uuid
        request_id = data.get("request_id") or uuid.uuid4().hex
        type_map = {
            "clarification_needed": "clarification_request",
            "approval_needed": "approval_request",
            "workflow_completed": "run_completed",
            "error": "error",
        }
        msg_type = type_map.get(event, event)
        return await self.send_envelope(run_id, msg_type, request_id, data)

    def has_connection(self, run_id: str, *, project_id: str = "") -> bool:
        key = self._key(project_id, run_id)
        return key in self._connections or run_id in self._connections


ws_manager = WSManager()
