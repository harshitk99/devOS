from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import IdMixin


class Project(IdMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(1000))
    manifest: Mapped[dict] = mapped_column(JSONB, default=dict)  # Project Importer output
