from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.core.db import get_session
from app.models import Project
from app.schemas.api import FileListOut, FileOut
from app.services import explorer

router = APIRouter(tags=["files"])


def _root(session: Session, project_id: UUID) -> Path:
    root = Path(get_or_404(session, Project, project_id).path)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"project folder no longer exists: {root}")
    return root


@router.get("/projects/{project_id}/files", response_model=FileListOut)
def list_project_files(project_id: UUID, session: Session = Depends(get_session)):
    files, truncated = explorer.list_files(_root(session, project_id))
    return {"files": files, "truncated": truncated}


@router.get("/projects/{project_id}/files/content", response_model=FileOut)
def read_project_file(
    project_id: UUID,
    path: str = Query(..., description="path relative to the project root"),
    reveal: bool = Query(False, description="show values in .env files instead of masking them"),
    session: Session = Depends(get_session),
):
    root = _root(session, project_id)
    try:
        return explorer.read_file(root, path, reveal)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"file not found: {path}") from e
