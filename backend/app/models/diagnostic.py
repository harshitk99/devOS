import uuid

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import IdMixin


class Diagnostic(IdMixin, Base):
    __tablename__ = "diagnostics"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    check: Mapped[str] = mapped_column(String(100))  # e.g. "port_conflict", "missing_dependency"
    severity: Mapped[str] = mapped_column(String(20))  # domain.enums.Severity
    confidence: Mapped[float] = mapped_column(Float)
    root_cause: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
