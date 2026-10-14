from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.core.db import get_session
from app.models import Project
from app.schemas.api import AuditEventOut
from app.services import audit

router = APIRouter(tags=["audit"])


@router.get("/projects/{project_id}/audit", response_model=list[AuditEventOut])
def list_audit_events(project_id: UUID, session: Session = Depends(get_session)):
    get_or_404(session, Project, project_id)
    return audit.for_project(session, project_id)
