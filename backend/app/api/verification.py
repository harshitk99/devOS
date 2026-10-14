from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.core.db import get_session
from app.models import Execution, RecoveryPlan, Verification
from app.schemas.api import VerificationOut
from app.services import pipeline

router = APIRouter(tags=["verification"])


@router.post("/executions/{execution_id}/verify", response_model=VerificationOut)
def verify_execution(execution_id: UUID, session: Session = Depends(get_session)):
    """Re-run verification for an execution. A FAILED verdict rolls the project back."""
    execution = get_or_404(session, Execution, execution_id)
    plan = get_or_404(session, RecoveryPlan, execution.plan_id)
    return pipeline.verify_and_settle(session, plan, execution)


@router.get("/executions/{execution_id}/verifications", response_model=list[VerificationOut])
def list_verifications(execution_id: UUID, session: Session = Depends(get_session)):
    get_or_404(session, Execution, execution_id)
    return (
        session.query(Verification)
        .filter(Verification.execution_id == execution_id)
        .order_by(Verification.created_at)
        .all()
    )
