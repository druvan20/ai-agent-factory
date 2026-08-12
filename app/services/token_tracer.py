"""Native token/cost tracing — wraps OpenAI calls and writes usage rows."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Default USD per 1M tokens (overridable via MODEL_PRICING JSON env)
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-5.4": {"input": 2.50, "output": 10.00},
    "gpt-5.5": {"input": 2.50, "output": 10.00},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
}


def _pricing_table() -> dict[str, dict[str, float]]:
    settings = get_settings()
    raw = settings.model_pricing.strip()
    if not raw:
        return _DEFAULT_PRICING
    try:
        custom = json.loads(raw)
        merged = dict(_DEFAULT_PRICING)
        merged.update(custom)
        return merged
    except json.JSONDecodeError:
        return _DEFAULT_PRICING


def compute_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    table = _pricing_table()
    rates = table.get(model) or table.get("gpt-4o", {"input": 2.5, "output": 10.0})
    return round((tokens_in * rates["input"] + tokens_out * rates["output"]) / 1_000_000, 8)


class TokenTracer:
    """Intercept OpenAI responses and persist usage."""

    def __init__(
        self,
        *,
        run_id: str = "",
        project_id: str = "",
        node: str = "",
        tool: str = "openai",
    ) -> None:
        self.run_id = run_id
        self.project_id = project_id
        self.node = node
        self.tool = tool

    def record_from_response(self, resp: Any, model: str | None = None) -> dict:
        usage = getattr(resp, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        used_model = model or getattr(resp, "model", None) or get_settings().openai_model
        cost = compute_cost_usd(used_model, tokens_in, tokens_out)
        record = {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "node": self.node,
            "tool": self.tool,
            "model": used_model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
        }
        self._persist(record)
        return record

    def _persist(self, record: dict) -> None:
        if not record.get("run_id"):
            return
        try:
            from app.services.usage_service import record_usage_sync
            record_usage_sync(record)
        except Exception:
            logger.debug("usage persist skipped", exc_info=True)


def traced_chat_completion(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    response_format: dict | None = None,
    run_id: str = "",
    project_id: str = "",
    node: str = "",
) -> tuple[str, dict]:
    """OpenAI chat completion with token tracing. Returns (content, usage_record)."""
    from openai import OpenAI  # type: ignore[import-untyped]

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if response_format:
        kwargs["response_format"] = response_format

    resp = client.chat.completions.create(**kwargs)
    tracer = TokenTracer(run_id=run_id, project_id=project_id, node=node)
    usage_rec = tracer.record_from_response(resp, model=settings.openai_model)
    content = resp.choices[0].message.content or ""
    return content, usage_rec
