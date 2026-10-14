import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import IdMixin


class Verification(IdMixin, Base):
    __tablename__ = "verifications"

    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(30))  # domain.enums.Verdict
    results: Mapped[dict] = mapped_column(JSONB, default=dict)  # build/tests/startup/health/logs
