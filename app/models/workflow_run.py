from __future__ import annotations

import enum

from sqlalchemy import String, Text, Integer, Float, Enum, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class WorkflowType(str, enum.Enum):
    REQUIREMENTS = "requirements"
    PLANNING = "planning"
    CODEGEN = "codegen"


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_CLARIFICATION = "waiting_clarification"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILED_COST_CEILING = "failed_cost_ceiling"


class WorkflowRun(Base, TimestampMixin):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_type: Mapped[WorkflowType] = mapped_column(Enum(WorkflowType), nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.PENDING, nullable=False)

    thread_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    clarification_round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_clarification_rounds: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    artifact_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    bundle_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    snapshotted_patterns: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)