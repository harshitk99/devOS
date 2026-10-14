"""End-to-end pipeline tests on real Postgres with a fake sandbox that really edits the project
files, so rollback assertions are meaningful."""

from contextlib import nullcontext
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.enums import AuditEventType, ExecutionStatus, PlanStatus
from app.models import Verification
from app.services import audit, pipeline, sandbox

GOOD = {"ok": True, "startup": "ok", "health": {"status": 200}, "port": 3100, "log": "listening\n"}
CRASH = {"ok": True, "startup": "crashed", "exit_code": 1, "health": None, "log": "Error: boom\n"}


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path_factory.mktemp("devos_data"))


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Applies set_env_var for real on the host dir; verification result is configurable."""
    state = {"verify": GOOD, "run_result": None}

    def run(action, project_path, approved=False, limits=None):
        if state["run_result"] is not None:
            return state["run_result"]
        p = action.action.params
        with (Path(project_path) / p["path"]).open("a") as f:
            f.write(f"{p['key']}={p['value']}\n")
        return {"ok": True, "message": f"set {p['key']}"}

    monkeypatch.setattr(sandbox, "run", run)
    monkeypatch.setattr(sandbox, "run_verify", lambda project_path, limits=None: dict(state["verify"]))
    return state


def _ready_plan(session, path):
    project = pipeline.import_project(session, str(path))
    pipeline.diagnose(session, project)
    return project, pipeline.generate_plan(session, project)


def _run(session, execution, approved=True):
    pipeline.run_execution(execution.id, approved, session_factory=lambda: nullcontext(session))


def _audit_types(session, project):
    return [e.type for e in audit.for_project(session, project.id)]


def test_happy_path_is_verified_and_fully_audited(db_session, demo_project, fake_sandbox):
    project, plan = _ready_plan(db_session, demo_project)
    assert plan.status == PlanStatus.PROPOSED.value

    pipeline.approve_plan(db_session, plan)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)

    assert "DATABASE_URL=postgresql" in (demo_project / ".env").read_text()
    assert plan.status == PlanStatus.COMPLETED.value
    assert execution.status == ExecutionStatus.SUCCEEDED.value
    verification = db_session.query(Verification).filter_by(execution_id=execution.id).one()
    assert verification.verdict == "VERIFIED"
    assert _audit_types(db_session, project) == [
        AuditEventType.PROJECT_IMPORTED,
        AuditEventType.DIAGNOSIS_RUN,
        AuditEventType.PLAN_GENERATED,
        AuditEventType.PLAN_APPROVED,
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.EXECUTION_FINISHED,
        AuditEventType.VERIFICATION_COMPLETED,
    ]


def test_failed_verification_rolls_back_automatically(db_session, demo_project, fake_sandbox):
    original = (demo_project / ".env").read_text()
    fake_sandbox["verify"] = CRASH
    project, plan = _ready_plan(db_session, demo_project)
    pipeline.approve_plan(db_session, plan)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)

    assert (demo_project / ".env").read_text() == original  # change undone
    assert plan.status == PlanStatus.ROLLED_BACK.value
    types = _audit_types(db_session, project)
    assert types[-2:] == [AuditEventType.VERIFICATION_COMPLETED, AuditEventType.ROLLBACK_PERFORMED]
    assert db_session.query(Verification).filter_by(execution_id=execution.id).one().verdict == "FAILED"


def test_failed_action_rolls_back_without_verifying(db_session, demo_project, fake_sandbox):
    original = (demo_project / ".env").read_text()
    fake_sandbox["run_result"] = {"ok": False, "error": "boom"}
    project, plan = _ready_plan(db_session, demo_project)
    pipeline.approve_plan(db_session, plan)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)

    assert execution.status == ExecutionStatus.FAILED.value
    assert plan.status == PlanStatus.ROLLED_BACK.value
    assert (demo_project / ".env").read_text() == original
    assert db_session.query(Verification).filter_by(execution_id=execution.id).count() == 0
    assert AuditEventType.ROLLBACK_PERFORMED in _audit_types(db_session, project)


def test_sandbox_crash_is_contained_and_rolled_back(db_session, demo_project, fake_sandbox, monkeypatch):
    def explode(*a, **k):
        raise sandbox.SandboxError("docker unavailable")

    monkeypatch.setattr(sandbox, "run", explode)
    _, plan = _ready_plan(db_session, demo_project)
    pipeline.approve_plan(db_session, plan)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)  # must not raise

    assert execution.status == ExecutionStatus.FAILED.value
    assert plan.status == PlanStatus.ROLLED_BACK.value
    assert any("docker unavailable" in e["message"] for e in execution.log)


def test_unapproved_plan_cannot_execute(db_session, demo_project, fake_sandbox):
    _, plan = _ready_plan(db_session, demo_project)
    with pytest.raises(pipeline.PlanStateError):
        pipeline.start_execution(db_session, plan)
    assert not (demo_project / ".env").read_text().count("DATABASE_URL")


def test_rejected_plan_cannot_execute_or_be_approved(db_session, demo_project, fake_sandbox):
    _, plan = _ready_plan(db_session, demo_project)
    pipeline.reject_plan(db_session, plan)
    with pytest.raises(pipeline.PlanStateError):
        pipeline.start_execution(db_session, plan)
    with pytest.raises(pipeline.PlanStateError):
        pipeline.approve_plan(db_session, plan)


def test_no_plan_when_nothing_is_fixable(db_session, demo_project):
    (demo_project / ".env").write_text("PORT=3100\nDATABASE_URL=postgresql://x\n")
    project = pipeline.import_project(db_session, str(demo_project))
    assert pipeline.diagnose(db_session, project) == []
    with pytest.raises(pipeline.NoPlanError):
        pipeline.generate_plan(db_session, project)


def test_manual_rollback_restores_files(db_session, demo_project, fake_sandbox):
    original = (demo_project / ".env").read_text()
    _, plan = _ready_plan(db_session, demo_project)
    pipeline.approve_plan(db_session, plan)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)
    assert (demo_project / ".env").read_text() != original

    pipeline.rollback_execution(db_session, execution)
    assert (demo_project / ".env").read_text() == original
    assert plan.status == PlanStatus.ROLLED_BACK.value


# --- rollback is honest about what it cannot undo ---------------------------------------------


def _multi_step_plan(session, project_path):
    """A plan with a package install, a migration and a settings change, built directly."""
    from app.domain.actions import ProposedAction
    from app.domain.enums import ActionType
    from app.models import RecoveryPlan
    from app.services import policy

    project = pipeline.import_project(session, str(project_path))
    steps = [
        ProposedAction(type=ActionType.INSTALL_DEPENDENCY, params={"manager": "npm"}, rationale="install"),
        ProposedAction(type=ActionType.RUN_MIGRATION, params={"path": "db/schema.sql"}, rationale="migrate"),
        ProposedAction(type=ActionType.SET_ENV_VAR, params={"path": ".env", "key": "X", "value": "1"}, rationale="setting"),
    ]
    plan = RecoveryPlan(project_id=project.id, status="approved", actions=[policy.classify(s).model_dump(mode="json") for s in steps])
    session.add(plan)
    session.commit()
    return project, plan


def _recover_messages(execution):
    return [e["message"] for e in execution.log if e["stage"] == "recover"]


def test_failed_verification_says_what_the_rollback_could_not_undo(db_session, demo_project, fake_sandbox, monkeypatch):
    from app.services import executor

    monkeypatch.setattr(executor, "execute", lambda action, path, approved: {"ok": True, "message": f"did {action.action.type.value}"})
    fake_sandbox["verify"] = CRASH
    _, plan = _multi_step_plan(db_session, demo_project)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)

    notes = [m for m in _recover_messages(execution) if m.startswith("NOT undone")]
    assert plan.status == PlanStatus.ROLLED_BACK.value
    assert len(notes) == 2 and any("node_modules" in n for n in notes) and any("database migration" in n for n in notes)


def test_a_failure_midway_only_lists_effects_of_steps_that_ran(db_session, demo_project, fake_sandbox, monkeypatch):
    from app.domain.enums import ActionType
    from app.services import executor

    def fake(action, path, approved):
        if action.action.type is ActionType.RUN_MIGRATION:
            return {"ok": False, "error": "migration failed and was rolled back: syntax error"}
        return {"ok": True, "message": "ok"}

    monkeypatch.setattr(executor, "execute", fake)
    _, plan = _multi_step_plan(db_session, demo_project)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)

    notes = [m for m in _recover_messages(execution) if m.startswith("NOT undone")]
    assert execution.status == ExecutionStatus.FAILED.value and plan.status == PlanStatus.ROLLED_BACK.value
    assert len(notes) == 1 and "node_modules" in notes[0]  # the install ran; the migration was one transaction that failed


def test_a_plan_of_only_file_changes_reports_nothing_left_behind(db_session, demo_project, fake_sandbox):
    fake_sandbox["verify"] = CRASH
    _, plan = _ready_plan(db_session, demo_project)
    pipeline.approve_plan(db_session, plan)
    execution, approved = pipeline.start_execution(db_session, plan)
    _run(db_session, execution, approved)
    assert not [m for m in _recover_messages(execution) if m.startswith("NOT undone")]
