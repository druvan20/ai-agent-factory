"""Shared SQLite checkpointer for all LangGraph workflows.

Uses AsyncSqliteSaver (langgraph-checkpoint-sqlite) backed by CHECKPOINT_DB_PATH.
A sync SqliteSaver is also exposed for graph.compile() used with invoke-in-thread;
both point at the same DB file for crash-resumability.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_sync_checkpointer: Any = None
_async_checkpointer: Any = None
_async_conn: Any = None


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def get_sqlite_checkpointer() -> Any:
    """Return a process-wide sync SqliteSaver (safe for graph.invoke in threads)."""
    global _sync_checkpointer
    if _sync_checkpointer is not None:
        return _sync_checkpointer

    from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore[import-untyped]

    settings = get_settings()
    path = Path(settings.checkpoint_db_path)
    _ensure_parent(path)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    try:
        checkpointer.setup()
    except Exception:
        logger.debug("SqliteSaver.setup skipped or already applied", exc_info=True)
    _sync_checkpointer = checkpointer
    logger.info("Sqlite checkpointer ready: %s", path)
    return _sync_checkpointer


async def init_async_checkpointer() -> Any:
    """Initialize AsyncSqliteSaver at app startup (same DB file as sync saver)."""
    global _async_checkpointer, _async_conn
    if _async_checkpointer is not None:
        return _async_checkpointer

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # type: ignore[import-untyped]

    settings = get_settings()
    path = Path(settings.checkpoint_db_path)
    _ensure_parent(path)
    _async_conn = await aiosqlite.connect(str(path))
    saver = AsyncSqliteSaver(_async_conn)
    await saver.setup()
    _async_checkpointer = saver
    logger.info("AsyncSqliteSaver ready: %s", path)
    # Also warm the sync saver so workflows can use either API
    get_sqlite_checkpointer()
    return _async_checkpointer


def get_async_checkpointer() -> Any:
    if _async_checkpointer is None:
        raise RuntimeError("AsyncSqliteSaver not initialized; call init_async_checkpointer() in lifespan")
    return _async_checkpointer


async def close_async_checkpointer() -> None:
    global _async_checkpointer, _async_conn
    if _async_conn is not None:
        await _async_conn.close()
    _async_checkpointer = None
    _async_conn = None
