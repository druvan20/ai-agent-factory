from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "ai-agent-factory",
        "version": "0.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/healthz")
async def healthz():
    """Deep health: SQLite + ChromaDB + filesystem."""
    settings = get_settings()
    checks: dict[str, str] = {}

    # SQLite
    try:
        from app.db.engine import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["sqlite"] = "ok"
    except Exception as exc:
        checks["sqlite"] = f"error: {exc}"

    # Chroma
    try:
        from app.store.vector_store import get_patterns_collection
        get_patterns_collection()
        checks["chroma"] = "ok"
    except Exception as exc:
        checks["chroma"] = f"error: {exc}"

    # Filesystem
    try:
        root = Path(settings.upload_dir)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".healthz"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["filesystem"] = "ok"
    except Exception as exc:
        checks["filesystem"] = f"error: {exc}"

    # Checkpoints path
    try:
        Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
        checks["checkpoints"] = "ok"
    except Exception as exc:
        checks["checkpoints"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return {
        "status": "healthy" if healthy else "degraded",
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
