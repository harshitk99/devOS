"""Request/response models for the REST API."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.actions import ClassifiedAction


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectIn(BaseModel):
    path: str


class ProjectOut(_Out):
    id: UUID
    name: str
    path: str
    manifest: dict[str, Any]
    created_at: datetime


class DiagnosticOut(_Out):
    id: UUID
    project_id: UUID
    check: str
    severity: str
    confidence: float
    root_cause: str
    evidence: dict[str, Any]
    priority: float = 0.0  # severity weight x confidence, filled in by api.deps.ranked_diagnostics
    rank: int = 0  # 1 = fix first
    fix_available: bool = False  # can DevOS propose an automatic fix for this? (False = diagnosis only)


class FileEntry(BaseModel):
    path: str
    size: int


class FileListOut(BaseModel):
    files: list[FileEntry]
    truncated: bool


class FileOut(BaseModel):
    path: str
    size: int
    binary: bool
    truncated: bool
    masked: bool  # True when .env values were hidden (pass reveal=true to see them)
    content: str


class FileWriteIn(BaseModel):
    path: str
    content: str


class DiffLine(BaseModel):
    op: str  # "same" | "add" | "del"
    text: str


class PreviewItem(BaseModel):
    index: int  # position of the action within the plan
    type: str
    path: str | None  # None for action types with no file preview
    created: bool = False  # the file does not exist yet
    diff: list[DiffLine]
    summary: str = ""  # the step in plain words
    needs_network: bool = False  # runs with network access (package install, database migration)
    warning: str | None = None  # what a rollback will NOT undo


class PlanOut(_Out):
    id: UUID
    project_id: UUID
    status: str
    actions: list[ClassifiedAction]
    created_at: datetime


class ExecutionOut(_Out):
    id: UUID
    plan_id: UUID
    status: str
    recovery_point: str | None
    log: list[dict[str, Any]]
    created_at: datetime


class VerificationOut(_Out):
    id: UUID
    execution_id: UUID
    verdict: str
    results: dict[str, Any]
    created_at: datetime


class AuditEventOut(_Out):
    id: UUID
    project_id: UUID
    type: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime
