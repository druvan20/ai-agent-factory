from __future__ import annotations

from sqlalchemy import String, Text, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class Pattern(Base, TimestampMixin):
    __tablename__ = "patterns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    intent: Mapped[str] = mapped_column(Text, nullable=False)
    structure: Mapped[str] = mapped_column(Text, nullable=False)
    when_to_use: Mapped[str] = mapped_column(Text, nullable=False)
    anti_patterns: Mapped[str] = mapped_column(Text, nullable=False)
    prerequisites: Mapped[str] = mapped_column(Text, nullable=False)

    references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_canonical: Mapped[bool] = mapped_column(default=False, nullable=False)