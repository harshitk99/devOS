from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.core.db import get_session
from app.domain.actions import ClassifiedAction
from app.models import Project, RecoveryPlan
from app.schemas.api import PlanOut, PreviewItem
from app.services import executor, explorer, pipeline

router = APIRouter(tags=["plans"])


@router.post("/projects/{project_id}/plans", response_model=PlanOut, status_code=201)
def generate_plan(project_id: UUID, session: Session = Depends(get_session)):
    return pipeline.generate_plan(session, get_or_404(session, Project, project_id))


@router.get("/projects/{project_id}/plans", response_model=list[PlanOut])
def list_plans(project_id: UUID, session: Session = Depends(get_session)):
    get_or_404(session, Project, project_id)
    return (
        session.query(RecoveryPlan)
        .filter(RecoveryPlan.project_id == project_id)
        .order_by(RecoveryPlan.created_at.desc())
        .all()
    )


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: UUID, session: Session = Depends(get_session)):
    return get_or_404(session, RecoveryPlan, plan_id)


@router.get("/plans/{plan_id}/preview", response_model=list[PreviewItem])
def preview_plan(plan_id: UUID, session: Session = Depends(get_session)):
    """What each action would change, as a line diff. Nothing is written."""
    plan = get_or_404(session, RecoveryPlan, plan_id)
    project = get_or_404(session, Project, plan.project_id)
    actions = [ClassifiedAction.model_validate(a) for a in plan.actions]
    items = explorer.preview_plan(Path(project.path), actions)
    for item in items:
        item.update(executor.describe(actions[item["index"]]))
    return items


@router.post("/plans/{plan_id}/approve", response_model=PlanOut)
def approve_plan(plan_id: UUID, session: Session = Depends(get_session)):
    return pipeline.approve_plan(session, get_or_404(session, RecoveryPlan, plan_id))


@router.post("/plans/{plan_id}/reject", response_model=PlanOut)
def reject_plan(plan_id: UUID, session: Session = Depends(get_session)):
    return pipeline.reject_plan(session, get_or_404(session, RecoveryPlan, plan_id))
