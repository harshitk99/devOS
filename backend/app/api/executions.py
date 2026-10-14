from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.core.db import get_session
from app.models import Execution, RecoveryPlan
from app.schemas.api import ExecutionOut
from app.services import pipeline

router = APIRouter(tags=["execution"])


@router.post("/plans/{plan_id}/execute", response_model=ExecutionOut, status_code=202)
def start_execution(
    plan_id: UUID, background: BackgroundTasks, session: Session = Depends(get_session)
):
    """Validates and starts the plan, then returns immediately. Progress streams over
    /ws/projects/{id}; the outcome is also readable via GET /executions/{id}."""
    plan = get_or_404(session, RecoveryPlan, plan_id)
    execution, approved = pipeline.start_execution(session, plan)
    background.add_task(pipeline.run_execution, execution.id, approved)
    return execution


@router.get("/plans/{plan_id}/executions", response_model=list[ExecutionOut])
def list_executions(plan_id: UUID, session: Session = Depends(get_session)):
    get_or_404(session, RecoveryPlan, plan_id)
    return (
        session.query(Execution)
        .filter(Execution.plan_id == plan_id)
        .order_by(Execution.created_at.desc())
        .all()
    )


@router.get("/executions/{execution_id}", response_model=ExecutionOut)
def get_execution(execution_id: UUID, session: Session = Depends(get_session)):
    return get_or_404(session, Execution, execution_id)
