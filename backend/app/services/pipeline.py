"""Orchestration: chains Understand -> Diagnose -> Protect -> Execute -> Verify -> Recover.

Each function is one state-changing step. Every step emits an AuditEvent (so the whole sequence
is reconstructable) and commits, so progress is visible to other requests immediately.
Components stay independent; this module only wires them together."""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.domain.actions import ClassifiedAction
from app.domain.enums import (
    ActionType,
    AuditEventType,
    ExecutionStatus,
    PlanStatus,
    PolicyDecision,
    Severity,
    Verdict,
)
from app.models import Diagnostic, Execution, Project, RecoveryPlan, Verification
from app.services import (
    approval, audit, checkup, doctor, executor, importer, planner, rollback, verification,
)
from app.services.events import bus

SessionFactory = Callable[[], AbstractContextManager[Session]]


class PipelineError(Exception):
    """A request that is valid HTTP but not valid for the current state (mapped to 409)."""


class NoPlanError(PipelineError):
    pass


class PlanStateError(PipelineError):
    pass


def _classified(plan: RecoveryPlan) -> list[ClassifiedAction]:
    return [ClassifiedAction.model_validate(a) for a in plan.actions]


# --- Understand -------------------------------------------------------------------------------


def import_project(session: Session, path: str) -> Project:
    manifest = importer.import_project(Path(path))
    project = (
        session.query(Project)
        .filter(Project.path == manifest["path"])
        .order_by(Project.created_at.desc())
        .first()
    )
    if project:  # re-importing a path refreshes it instead of creating a duplicate
        project.name, project.manifest = manifest["name"], manifest
    else:
        project = Project(name=manifest["name"], path=manifest["path"], manifest=manifest)
        session.add(project)
    session.flush()
    audit.record(
        session, project.id, AuditEventType.PROJECT_IMPORTED, "user",
        {"path": project.path, "name": project.name},
    )
    session.commit()
    return project


# --- Diagnose ---------------------------------------------------------------------------------


def diagnose(session: Session, project: Project) -> list[Diagnostic]:
    manifest, findings = checkup.run(project.path)  # re-read and re-probe: state may have changed
    project.manifest = manifest

    session.query(Diagnostic).filter(Diagnostic.project_id == project.id).delete()
    rows = [
        Diagnostic(
            project_id=project.id,
            check=f.check,
            severity=f.severity.value,
            confidence=f.confidence,
            root_cause=f.root_cause,
            evidence=f.evidence,
        )
        for f in findings
    ]
    session.add_all(rows)
    audit.record(
        session, project.id, AuditEventType.DIAGNOSIS_RUN, "system",
        {"findings": [f.model_dump(mode="json") for f in findings]},
    )
    session.commit()
    return rows


# --- Plan / Protect ---------------------------------------------------------------------------


def generate_plan(session: Session, project: Project) -> RecoveryPlan:
    rows = session.query(Diagnostic).filter(Diagnostic.project_id == project.id).all()
    findings = [
        doctor.Finding(
            check=r.check,
            severity=Severity(r.severity),
            confidence=r.confidence,
            root_cause=r.root_cause,
            evidence=r.evidence,
        )
        for r in rows
    ]
    result = planner.plan(findings)
    if not result.actions:
        raise NoPlanError("no automated fix is available for the current findings")

    plan = RecoveryPlan(
        project_id=project.id,
        status=PlanStatus.PROPOSED.value,
        actions=[a.model_dump(mode="json") for a in result.actions],
    )
    session.add(plan)
    session.flush()
    audit.record(
        session, project.id, AuditEventType.PLAN_GENERATED, f"planner:{result.source}",
        {"plan_id": str(plan.id), "actions": plan.actions, "source": result.source,
         "fallback_reason": result.fallback_reason, "review": result.review},
    )
    session.commit()
    return plan


def _require_proposed(plan: RecoveryPlan) -> None:
    if plan.status != PlanStatus.PROPOSED.value:
        raise PlanStateError(f"plan is {plan.status}, expected proposed")


def approve_plan(session: Session, plan: RecoveryPlan) -> RecoveryPlan:
    _require_proposed(plan)
    if any(a.decision is PolicyDecision.BLOCK for a in _classified(plan)):
        raise PlanStateError("plan contains blocked actions and can never be approved")
    plan.status = PlanStatus.APPROVED.value
    audit.record(session, plan.project_id, AuditEventType.PLAN_APPROVED, "user", {"plan_id": str(plan.id)})
    session.commit()
    return plan


def reject_plan(session: Session, plan: RecoveryPlan) -> RecoveryPlan:
    _require_proposed(plan)
    plan.status = PlanStatus.REJECTED.value
    audit.record(session, plan.project_id, AuditEventType.PLAN_REJECTED, "user", {"plan_id": str(plan.id)})
    session.commit()
    return plan


# --- Execute ----------------------------------------------------------------------------------


def start_execution(session: Session, plan: RecoveryPlan) -> tuple[Execution, bool]:
    """Validate that the plan may run and create its Execution. Returns (execution, approved)."""
    if plan.status not in (PlanStatus.PROPOSED.value, PlanStatus.APPROVED.value):
        raise PlanStateError(f"plan is {plan.status} and cannot be executed")
    approved = plan.status == PlanStatus.APPROVED.value
    if not approval.is_executable(_classified(plan), approved):
        raise PlanStateError("plan needs approval or contains blocked actions")

    execution = Execution(plan_id=plan.id, status=ExecutionStatus.PENDING.value, log=[])
    plan.status = PlanStatus.EXECUTING.value
    session.add(execution)
    session.flush()
    audit.record(
        session, plan.project_id, AuditEventType.EXECUTION_STARTED, "system",
        {"plan_id": str(plan.id), "execution_id": str(execution.id)},
    )
    session.commit()
    return execution, approved


def _emit(session: Session, project_id: uuid.UUID, execution: Execution, stage: str, message: str) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "stage": stage, "message": message}
    execution.log = [*execution.log, entry]  # reassign so the JSONB change is detected
    session.commit()
    bus.publish(project_id, {"kind": "log", "execution_id": str(execution.id), **entry})


def _finish(plan: RecoveryPlan, execution: Execution, verdict: Verdict | None) -> None:
    bus.publish(
        plan.project_id,
        {
            "kind": "done",
            "execution_id": str(execution.id),
            "plan_status": plan.status,
            "verdict": verdict.value if verdict else None,
        },
    )


def _rollback(
    session: Session,
    plan: RecoveryPlan,
    execution: Execution,
    reason: str,
    ran: list[ClassifiedAction] | None = None,
) -> bool:
    """Restore the recovery point. Returns False (and leaves the plan EXECUTING, i.e. visibly
    stuck for a human) if the restore itself fails. `ran` = the actions that actually ran (default:
    the whole plan); effects a file restore cannot reverse are reported, not hidden."""
    if not execution.recovery_point:
        _emit(session, plan.project_id, execution, "recover", "no recovery point exists; nothing to restore")
        return False
    project = session.get(Project, plan.project_id)
    try:
        rollback.restore(execution.recovery_point)
    except Exception as e:  # noqa: BLE001 - must be recorded whatever the cause
        audit.record(
            session, plan.project_id, AuditEventType.ROLLBACK_PERFORMED, "system",
            {"execution_id": str(execution.id), "ok": False, "error": str(e), "reason": reason},
        )
        session.commit()
        _emit(session, plan.project_id, execution, "recover", f"ROLLBACK FAILED: {e}")
        return False
    plan.status = PlanStatus.ROLLED_BACK.value
    audit.record(
        session, plan.project_id, AuditEventType.ROLLBACK_PERFORMED, "system",
        {"execution_id": str(execution.id), "ok": True, "reason": reason,
         "recovery_point": execution.recovery_point, "project": project.path if project else None},
    )
    session.commit()
    _emit(session, plan.project_id, execution, "recover", f"files rolled back to the recovery point ({reason})")
    for note in executor.not_undone(_classified(plan) if ran is None else ran):
        _emit(session, plan.project_id, execution, "recover", f"NOT undone: {note}")
    return True


# --- Verify / Recover -------------------------------------------------------------------------


def verify_and_settle(session: Session, plan: RecoveryPlan, execution: Execution) -> Verification:
    """Verify the project; a FAILED verdict triggers automatic rollback."""
    project = session.get(Project, plan.project_id)
    _emit(session, plan.project_id, execution, "verify", "starting project in sandbox and checking health")
    verdict, results = verification.verify(project.path, targeted=planner.targets_of(_classified(plan)))

    record = Verification(execution_id=execution.id, verdict=verdict.value, results=results)
    session.add(record)
    session.flush()
    audit.record(
        session, plan.project_id, AuditEventType.VERIFICATION_COMPLETED, "system",
        {"execution_id": str(execution.id), "verdict": verdict.value, "results": _brief(results)},
    )
    session.commit()
    _emit(session, plan.project_id, execution, "verify", f"verdict: {verdict.value}")

    if verdict is Verdict.FAILED:
        _rollback(session, plan, execution, reason="verification failed")
    else:
        plan.status = PlanStatus.COMPLETED.value
        session.commit()
    _finish(plan, execution, verdict)
    return record


def _brief(results: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in results.items() if k != "logs"}


def run_execution(
    execution_id: uuid.UUID, approved: bool, session_factory: SessionFactory = SessionLocal
) -> None:
    """Background job: snapshot, run each approved action in the sandbox, verify, roll back on
    failure. Never raises: every outcome is recorded in the execution log and audit trail."""
    with session_factory() as session:
        execution = session.get(Execution, execution_id)
        plan = session.get(RecoveryPlan, execution.plan_id)
        project = session.get(Project, plan.project_id)
        pid = project.id

        ran: list[ClassifiedAction] = []
        try:
            execution.status = ExecutionStatus.RUNNING.value
            execution.recovery_point = rollback.create_recovery_point(project.path)
            _emit(session, pid, execution, "protect", f"recovery point {execution.recovery_point[:8]} created")

            for classified in _classified(plan):
                _emit(session, pid, execution, "execute",
                      f"{classified.action.type.value} ({classified.risk.value} risk): {classified.action.rationale}")
                if classified.action.type is not ActionType.RUN_MIGRATION:  # a migration is one transaction
                    ran.append(classified)  # may be partly applied even if it then fails
                result = executor.execute(classified, project.path, approved)
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "action failed"))
                if classified.action.type is ActionType.RUN_MIGRATION:
                    ran.append(classified)
                _emit(session, pid, execution, "execute", result.get("message", "done"))

            execution.status = ExecutionStatus.SUCCEEDED.value
            audit.record(session, pid, AuditEventType.EXECUTION_FINISHED, "system",
                         {"execution_id": str(execution.id), "status": "succeeded"})
            session.commit()
        except Exception as e:  # noqa: BLE001 - any failure must end in rollback + a record
            session.rollback()
            execution = session.get(Execution, execution_id)
            plan = session.get(RecoveryPlan, execution.plan_id)
            execution.status = ExecutionStatus.FAILED.value
            audit.record(session, pid, AuditEventType.EXECUTION_FINISHED, "system",
                         {"execution_id": str(execution.id), "status": "failed", "error": str(e)})
            session.commit()
            _emit(session, pid, execution, "execute", f"failed: {e}")
            _rollback(session, plan, execution, reason="execution failed", ran=ran)
            _finish(plan, execution, None)
            return

        verify_and_settle(session, plan, execution)


def rollback_execution(session: Session, execution: Execution) -> Execution:
    """Manual rollback of a finished execution's changes."""
    plan = session.get(RecoveryPlan, execution.plan_id)
    if not _rollback(session, plan, execution, reason="manual rollback"):
        raise PlanStateError("rollback could not be performed; see the execution log")
    return execution
