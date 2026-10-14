"""Audit Trail: append-only record of every state-changing operation."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domain.enums import AuditEventType
from app.models import AuditEvent


def record(
    session: Session,
    project_id: uuid.UUID,
    type: AuditEventType,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(project_id=project_id, type=type.value, actor=actor, payload=payload or {})
    session.add(event)
    session.flush()
    return event


def for_project(session: Session, project_id: uuid.UUID) -> list[AuditEvent]:
    return (
        session.query(AuditEvent)
        .filter(AuditEvent.project_id == project_id)
        .order_by(AuditEvent.created_at)
        .all()
    )
