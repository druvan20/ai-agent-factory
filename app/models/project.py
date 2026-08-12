from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid

if TYPE_CHECKING:
    from app.models.document import Document


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    ARCHIVED = "archived"


VALID_TRANSITIONS: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.DRAFT: {ProjectStatus.READY, ProjectStatus.ARCHIVED},
    ProjectStatus.READY: {ProjectStatus.RUNNING, ProjectStatus.DRAFT, ProjectStatus.ARCHIVED},
    ProjectStatus.RUNNING: {ProjectStatus.READY, ProjectStatus.ARCHIVED},
    ProjectStatus.ARCHIVED: set(),
}


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.DRAFT, nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )

    def can_transition_to(self, target: ProjectStatus) -> bool:
        return target in VALID_TRANSITIONS.get(self.status, set())