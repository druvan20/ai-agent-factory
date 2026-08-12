from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import select, text

from app.config import get_settings
from app.db.engine import engine, async_session_factory
from app.models.base import Base
from app.models.user import User
from app.models.project import Project  # noqa: F401 - register model
from app.models.document import Document  # noqa: F401
from app.models.audit import AuditLog  # noqa: F401
from app.models.pattern import Pattern  # noqa: F401
from app.models.workflow_run import WorkflowRun  # noqa: F401
from app.models.usage import UsageRecord  # noqa: F401
from app.models.run_event import RunEvent  # noqa: F401
from app.auth.jwt_handler import hash_password
from app.errors import register_error_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")

    try:
        from app.workflows.checkpointer import init_async_checkpointer
        await init_async_checkpointer()
    except Exception:
        logger.exception("AsyncSqliteSaver init failed; sync SqliteSaver will still be used")

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == settings.admin_username))
        if result.scalar_one_or_none() is None:
            admin = User(
                username=settings.admin_username,
                hashed_password=hash_password(settings.admin_password),
            )
            db.add(admin)
            await db.commit()
            logger.info("Seeded admin user: %s", settings.admin_username)

    from app.services.pattern_seed import seed_canonical_patterns
    async with async_session_factory() as db:
        await seed_canonical_patterns(db)

    yield

    try:
        from app.workflows.checkpointer import close_async_checkpointer
        await close_async_checkpointer()
    except Exception:
        pass
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="AI Agent Factory",
    description=(
        "Document-driven, pattern-aware code generator backend. "
        "Turns BRD/PRD/TRD documents into working code through "
        "autonomous agentic workflows. "
        "HITL uses WebSockets at WS /projects/{project_id}/runs/{run_id}/hitl — "
        "see README for the message envelope."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

register_error_handlers(app)

from app.routers.health import router as health_router
from app.auth.routes import router as auth_router
from app.routers.projects import router as projects_router
from app.routers.documents import router as documents_router
from app.routers.patterns import router as patterns_router
from app.routers.workflows import router as workflows_router
from app.routers.workflows import ws_router, runs_router, graph_router
from app.routers.traceability import router as traceability_router

app.include_router(health_router)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(patterns_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")
app.include_router(traceability_router, prefix="/api/v1")
app.include_router(graph_router)
app.include_router(ws_router)
