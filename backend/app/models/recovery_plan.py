import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import IdMixin


class RecoveryPlan(IdMixin, Base):
    __tablename__ = "recovery_plans"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # domain.enums.PlanStatus
    actions: Mapped[list] = mapped_column(JSONB, default=list)  # list[ClassifiedAction]
