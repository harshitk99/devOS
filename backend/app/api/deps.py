from collections.abc import Iterable
from typing import TypeVar
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.domain.enums import Severity
from app.models import Diagnostic
from app.schemas.api import DiagnosticOut
from app.services import doctor, planner

T = TypeVar("T")


def get_or_404(session: Session, model: type[T], id: UUID) -> T:
    row = session.get(model, id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {id} not found")
    return row


def ranked_diagnostics(rows: Iterable[Diagnostic]) -> list[DiagnosticOut]:
    """Diagnostics sorted by priority (highest first) with rank numbers 1..n."""
    outs = [DiagnosticOut.model_validate(r) for r in rows]
    for out in outs:
        out.priority = doctor.priority(Severity(out.severity), out.confidence)
        finding = doctor.Finding(check=out.check, severity=Severity(out.severity), confidence=out.confidence,
                                 root_cause=out.root_cause, evidence=out.evidence)
        out.fix_available = bool(planner.rule_proposals([finding]))
    outs.sort(key=lambda o: (-o.priority, o.check, o.root_cause))
    for rank, out in enumerate(outs, start=1):
        out.rank = rank
    return outs
