from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_or_404, ranked_diagnostics
from app.core.db import get_session
from app.models import Project
from app.schemas.api import DiagnosticOut, ProjectIn, ProjectOut
from app.services import pipeline

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
def import_project(body: ProjectIn, session: Session = Depends(get_session)):
    try:
        return pipeline.import_project(session, body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_session)):
    """One entry per project folder (the newest), newest first. Older rows for the same path can
    exist from before re-importing reused projects; they are kept but not listed."""
    newest: dict[str, Project] = {}
    for project in session.query(Project).order_by(Project.created_at.desc()):
        newest.setdefault(project.path, project)
    return list(newest.values())


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: UUID, session: Session = Depends(get_session)):
    return get_or_404(session, Project, project_id)


@router.post("/{project_id}/diagnose", response_model=list[DiagnosticOut])
def diagnose_project(project_id: UUID, session: Session = Depends(get_session)):
    return ranked_diagnostics(pipeline.diagnose(session, get_or_404(session, Project, project_id)))
