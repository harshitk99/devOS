import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import IdMixin


class Execution(IdMixin, Base):
    __tablename__ = "executions"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_plans.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # domain.enums.ExecutionStatus
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recovery_point: Mapped[str | None] = mapped_column(String(200), nullable=True)  # for rollback
    log: Mapped[list] = mapped_column(JSONB, default=list)
