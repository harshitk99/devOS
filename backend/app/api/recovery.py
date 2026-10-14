from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_or_404
from app.core.db import get_session
from app.models import Execution
from app.schemas.api import ExecutionOut
from app.services import pipeline

router = APIRouter(tags=["recovery"])


@router.post("/executions/{execution_id}/rollback", response_model=ExecutionOut)
def rollback_execution(execution_id: UUID, session: Session = Depends(get_session)):
    return pipeline.rollback_execution(session, get_or_404(session, Execution, execution_id))
