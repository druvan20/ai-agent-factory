from __future__ import annotations

from sqlalchemy import String, Text, Integer, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class UsageRecord(Base, TimestampMixin):
    __tablename__ = "usage"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    tool: Mapped[str] = mapped_column(String(64), default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
