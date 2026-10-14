from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_or_404, ranked_diagnostics
from app.core.db import get_session
from app.models import Diagnostic, Project
from app.schemas.api import DiagnosticOut

router = APIRouter(tags=["diagnostics"])


@router.get("/projects/{project_id}/diagnostics", response_model=list[DiagnosticOut])
def list_diagnostics(project_id: UUID, session: Session = Depends(get_session)):
    """Findings ranked by priority (severity x confidence), most urgent first."""
    get_or_404(session, Project, project_id)
    return ranked_diagnostics(session.query(Diagnostic).filter(Diagnostic.project_id == project_id))
