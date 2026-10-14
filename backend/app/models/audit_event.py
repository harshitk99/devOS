import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import IdMixin


class AuditEvent(IdMixin, Base):
    """Append-only. The full import -> diagnose -> plan -> approve -> execute -> verify ->
    rollback sequence must be reconstructable from these rows."""

    __tablename__ = "audit_events"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))  # domain.enums.AuditEventType
    actor: Mapped[str] = mapped_column(String(50))  # "user" | "system" | "planner:llm" | "planner:rules"
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
