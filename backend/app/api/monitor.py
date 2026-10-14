from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models import Project
from app.services import monitor

router = APIRouter(tags=["monitor"])


@router.get("/monitor")
def live_state(session: Session = Depends(get_session)):
    """What is running right now: listening ports (and who holds them), project processes, containers.
    Imported projects are used to attribute ports and containers to the project they belong to."""
    projects: dict[str, str] = {}
    ports: dict[int, str] = {}
    for p in session.query(Project).order_by(Project.created_at):  # newest wins on duplicate paths
        projects[p.path] = p.name
        port = (p.manifest or {}).get("config", {}).get("port")
        if port:
            ports[port] = p.name
    return monitor.snapshot(projects, ports)
