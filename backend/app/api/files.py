from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.core.db import get_session
from app.models import Project
from app.schemas.api import FileListOut, FileOut, FileWriteIn
from app.services import explorer, pipeline

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


@router.put("/projects/{project_id}/files/content", response_model=FileOut)
def write_project_file(project_id: UUID, body: FileWriteIn, session: Session = Depends(get_session)):
    """Save new content for a file the developer is editing. Not an AI action: no plan, no
    sandbox, no approval gate (see services/explorer.write_file for why that's the right line)."""
    _root(session, project_id)  # 404s if the project or its folder is gone
    project = get_or_404(session, Project, project_id)  # same row; a cheap second lookup, kept simple
    try:
        return pipeline.edit_file(session, project, body.path, body.content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"file not found: {body.path}") from e
    except explorer.ExplorerError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
