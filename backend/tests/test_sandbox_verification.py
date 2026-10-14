import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import REPO_DIR
from app.domain.actions import ProposedAction
from app.domain.enums import ActionType, Verdict
from app.services import policy, sandbox, verification

OK = {"startup": "ok", "health": {"status": 200}, "log": "listening", "log_errors": []}


@pytest.mark.parametrize(
    "results, expected",
    [
        (OK, Verdict.VERIFIED),
        ({**OK, "log_errors": ["Error: deprecated"]}, Verdict.PARTIALLY_VERIFIED),
        ({**OK, "health": {"status": 500}}, Verdict.FAILED),
        ({**OK, "health": {"status": None}}, Verdict.FAILED),
        ({"startup": "crashed", "exit_code": 1, "health": None}, Verdict.FAILED),
        ({"startup": "timeout", "health": None}, Verdict.FAILED),
        ({"startup": "not_run"}, Verdict.NOT_TESTED),
    ],
)
def test_decide(results, expected):
    assert verification.decide(results) is expected


def test_sandbox_infrastructure_failure_is_not_tested(monkeypatch):
    def boom(*a, **k):
        raise sandbox.SandboxError("docker unavailable")

    monkeypatch.setattr(sandbox, "run_verify", boom)
    verdict, results = verification.verify("/anywhere")
    assert verdict is Verdict.NOT_TESTED and "docker unavailable" in results["error"]


def test_log_errors_are_extracted(monkeypatch):
    monkeypatch.setattr(sandbox, "run_verify", lambda p: {**OK, "log": "ok\nUnhandled rejection\nfine"})
    verdict, results = verification.verify("/p")
    assert results["log_errors"] == ["Unhandled rejection"]
    assert verdict is Verdict.PARTIALLY_VERIFIED


def _classified(**params):
    return policy.classify(ProposedAction(type=ActionType.SET_ENV_VAR, params=params))


def test_sandbox_refuses_unapproved_actions_before_touching_docker(tmp_path):
    action = _classified(path=".env", key="A", value="b")
    with pytest.raises(PermissionError):
        sandbox.run(action, str(tmp_path), approved=False)


def test_sandbox_refuses_blocked_actions_even_if_approved(tmp_path):
    action = _classified(path="../escape", key="A", value="b")
    with pytest.raises(PermissionError):
        sandbox.run(action, str(tmp_path), approved=True)


# --- the runner that lives inside the container (run with host node if present) ---------------

RUNNER = REPO_DIR / "sandbox" / "runner.js"


def _run_runner(work: Path, payload: str) -> str:
    import os

    env = {**os.environ, "DEVOS_WORK": str(work), "DEVOS_PAYLOAD": payload}
    out = subprocess.run(
        ["node", str(RUNNER)], env=env, capture_output=True, text=True, timeout=30, check=False
    )
    return out.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "params, expected",
    [
        ('{"path": "../x/.env", "key": "A", "value": "b"}', "path escapes project root"),
        ('{"path": "server.js", "key": "A", "value": "b"}', "only .env files"),
        ('{"path": ".env", "key": "A;rm -rf", "value": "b"}', "invalid variable name"),
        ('{"path": ".env", "key": "A", "value": "b\\nEVIL=1"}', "invalid variable value"),
    ],
)
def test_runner_rejects_unsafe_set_env_var(tmp_path, params, expected):
    out = _run_runner(tmp_path, f'{{"mode":"action","action":{{"type":"set_env_var","params":{params}}}}}')
    assert expected in out and '"ok":false' in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_runner_has_no_shell_action(tmp_path):
    out = _run_runner(tmp_path, '{"mode":"action","action":{"type":"run_shell","params":{"cmd":"id"}}}')
    assert "not supported" in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_runner_set_env_var_replaces_existing_key(tmp_path):
    (tmp_path / ".env").write_text("A=old\nB=keep\n")
    _run_runner(tmp_path, '{"mode":"action","action":{"type":"set_env_var","params":{"key":"A","value":"new"}}}')
    assert (tmp_path / ".env").read_text() == "A=new\nB=keep\n"


# --- real Docker (skipped if the daemon is unavailable) ---------------------------------------


@pytest.fixture(scope="module")
def docker_ready():
    try:
        import docker

        docker.from_env().ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Docker daemon not available")


def test_real_sandbox_applies_and_verifies_a_fix(docker_ready, tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node server.js"}}')
    (tmp_path / "server.js").write_text(
        "require('fs').readFileSync('.env','utf8').split('\\n').forEach(l=>{const m=l.split('=');"
        "if(m[0])process.env[m[0]]=m[1]});"
        "if(!process.env.X){console.error('Error: no X');process.exit(1)}"
        "require('http').createServer((q,r)=>r.end('ok')).listen(3199)"
    )
    (tmp_path / ".env").write_text("PORT=3199\n")

    verdict, _ = verification.verify(str(tmp_path))
    assert verdict is Verdict.FAILED  # X is not set yet

    action = _classified(path=".env", key="X", value="1")
    assert sandbox.run(action, str(tmp_path), approved=True)["ok"] is True
    assert "X=1" in (tmp_path / ".env").read_text()

    verdict, results = verification.verify(str(tmp_path))
    assert verdict is Verdict.VERIFIED, results


# --- the image is rebuilt when its contents change --------------------------------------------


def test_image_tag_follows_the_runner_contents(monkeypatch, tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM node:20-alpine")
    (tmp_path / "runner.js").write_text("v1")
    monkeypatch.setattr(sandbox, "SANDBOX_CONTEXT", tmp_path)
    first = sandbox.image_tag()
    assert first == sandbox.image_tag() and first.startswith("devos-sandbox:")
    (tmp_path / "runner.js").write_text("v2")
    assert sandbox.image_tag() != first


def test_a_sandbox_that_fails_is_not_a_verdict_about_the_app(monkeypatch):
    monkeypatch.setattr(sandbox, "run_verify", lambda p: {"ok": False, "error": 'unknown mode "verify"'})
    verdict, results = verification.verify("/p")
    assert verdict is Verdict.NOT_TESTED and "unknown mode" in results["error"]


# --- stronger verification: build, tests, and re-running the Doctor ----------------------------

from app.domain.enums import Severity  # noqa: E402
from app.services import checkup, planner  # noqa: E402
from app.services.doctor import Finding  # noqa: E402


def _finding(check, **evidence):
    return Finding(check=check, severity=Severity.HIGH, confidence=0.9, root_cause=f"{check} still there", evidence=evidence)


@pytest.mark.parametrize(
    "extra, expected",
    [
        ({"build": {"status": "failed"}}, Verdict.FAILED),
        ({"build": {"status": "passed"}, "tests": {"status": "passed"}}, Verdict.VERIFIED),
        ({"build": {"status": "skipped"}, "tests": {"status": "skipped"}}, Verdict.VERIFIED),
        ({"tests": {"status": "failed"}}, Verdict.PARTIALLY_VERIFIED),  # failing tests keep the change
        ({"recheck": {"remaining": [{"check": "port_in_use"}], "other": []}}, Verdict.FAILED),
        ({"recheck": {"remaining": [], "other": [{"check": "runtime_crash"}]}}, Verdict.PARTIALLY_VERIFIED),
        ({"recheck": {"remaining": [], "other": []}}, Verdict.VERIFIED),
        ({"recheck_error": "boom"}, Verdict.PARTIALLY_VERIFIED),  # a broken re-check can never look like a pass
    ],
)
def test_decide_with_the_extra_layers(extra, expected):
    assert verification.decide({**OK, **extra}) is expected


@pytest.mark.parametrize(
    "recheck, expected",
    [
        ({"remaining": [{"check": "x"}], "other": []}, Verdict.FAILED),
        ({"remaining": [], "other": []}, Verdict.PARTIALLY_VERIFIED),  # fixed as diagnosed, but startup untestable
        (None, Verdict.NOT_TESTED),
    ],
)
def test_no_start_script_relies_on_the_recheck(recheck, expected):
    results = {"startup": "not_run"} | ({"recheck": recheck} if recheck else {})
    assert verification.decide(results) is expected


def _findings_after_fix(monkeypatch, *findings):
    monkeypatch.setattr(checkup, "run", lambda path, runtime=True: ({}, list(findings)))


def test_recheck_blames_the_plan_only_for_the_settings_it_touched(monkeypatch):
    """A plan that fixed PORT must not fail because a different setting was already missing."""
    _findings_after_fix(monkeypatch, _finding("env_missing", key="DATABASE_URL"))
    result = verification.recheck("/p", {("env_missing", "PORT"), ("port_in_use", "PORT")})
    assert result["remaining"] == [] and [o["check"] for o in result["other"]] == ["env_missing"]


def test_recheck_reports_a_targeted_problem_that_is_still_there(monkeypatch):
    _findings_after_fix(monkeypatch, _finding("port_in_use", key="PORT", port=3100))
    result = verification.recheck("/p", {("port_in_use", "PORT")})
    assert [r["check"] for r in result["remaining"]] == ["port_in_use"] and result["targeted"] == ["port_in_use:PORT"]


def test_recheck_waits_for_a_just_started_database(monkeypatch):
    sleeps, calls = [], iter([[_finding("database_unreachable")], [_finding("database_unreachable")], []])
    monkeypatch.setattr(checkup, "run", lambda path, runtime=True: ({}, next(calls)))
    monkeypatch.setattr(verification.time, "sleep", sleeps.append)
    result = verification.recheck("/p", {("database_unreachable", None)})
    assert result["remaining"] == [] and len(sleeps) == 2  # retried until it came up


def test_recheck_gives_up_waiting_eventually(monkeypatch):
    monkeypatch.setattr(verification, "SETTLE_TRIES", 3)
    monkeypatch.setattr(verification.time, "sleep", lambda s: None)
    _findings_after_fix(monkeypatch, _finding("database_unreachable"))
    assert verification.recheck("/p", {("database_unreachable", None)})["remaining"]


def test_recheck_never_waits_for_things_that_do_not_need_it(monkeypatch):
    monkeypatch.setattr(verification.time, "sleep", lambda s: pytest.fail("should not wait"))
    _findings_after_fix(monkeypatch, _finding("env_missing", key="X"))
    verification.recheck("/p", {("env_missing", "X")})


def test_verify_combines_sandbox_results_and_the_recheck(monkeypatch):
    monkeypatch.setattr(sandbox, "run_verify", lambda p: dict(OK, ok=True))
    _findings_after_fix(monkeypatch)  # nothing left
    verdict, results = verification.verify("/p", targeted={("dependency_missing", None)})
    assert verdict is Verdict.VERIFIED and results["recheck"]["remaining"] == []
    _findings_after_fix(monkeypatch, _finding("dependency_missing", missing=["express"]))
    assert verification.verify("/p", targeted={("dependency_missing", None)})[0] is Verdict.FAILED


def test_a_crashing_recheck_downgrades_instead_of_passing(monkeypatch):
    monkeypatch.setattr(sandbox, "run_verify", lambda p: dict(OK, ok=True))

    def boom(*a, **k):
        raise RuntimeError("database probe exploded")

    monkeypatch.setattr(checkup, "run", boom)
    verdict, results = verification.verify("/p", targeted={("schema_missing", None)})
    assert verdict is Verdict.PARTIALLY_VERIFIED and "exploded" in results["recheck_error"]


def test_targets_are_derived_from_the_actions():
    from app.domain.enums import ActionType

    actions = [
        _classified(path=".env", key="PORT", value="3101"),
        planner.policy.classify(ProposedAction(type=ActionType.INSTALL_DEPENDENCY, params={"manager": "npm"})),
        planner.policy.classify(ProposedAction(type=ActionType.RESTART_SERVICE, params={"service": "db"})),
        planner.policy.classify(ProposedAction(type=ActionType.RUN_MIGRATION, params={"path": "db/schema.sql"})),
    ]
    targets = planner.targets_of(actions)
    assert {("env_missing", "PORT"), ("port_in_use", "PORT"), ("dependency_missing", None),
            ("database_unreachable", None), ("schema_missing", None)} <= targets
    assert ("env_missing", "DATABASE_URL") not in targets  # PORT plan is not responsible for DATABASE_URL


def test_real_container_runs_the_projects_build_and_test_scripts(docker_ready, tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"start": "node server.js", "build": "node -e \\"process.exit(0)\\"", "test": "node -e \\"process.exit(1)\\""}}'
    )
    (tmp_path / "server.js").write_text("require('http').createServer((q,r)=>r.end('ok')).listen(3198)")
    (tmp_path / ".env").write_text("PORT=3198\n")
    verdict, results = verification.verify(str(tmp_path))
    assert results["build"]["status"] == "passed" and results["tests"]["status"] == "failed"
    assert results["startup"] == "ok"
    assert verdict is Verdict.PARTIALLY_VERIFIED  # runs fine, but its own tests fail: keep the change, say so
