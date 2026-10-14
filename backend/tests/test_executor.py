import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.core.config import REPO_DIR, SandboxLimits
from app.domain.actions import ProposedAction
from app.domain.enums import ActionType, PolicyDecision
from app.services import docker_services, executor, policy, sandbox


def act(kind, **params):
    return policy.classify(ProposedAction(type=kind, params=params))


# --- routing and refusal ----------------------------------------------------------------------


def test_service_start_goes_through_docker_services_and_the_rest_through_the_sandbox(monkeypatch):
    calls = []
    monkeypatch.setattr(docker_services, "start_service", lambda p, s: calls.append(("service", s)) or {"ok": True})
    monkeypatch.setattr(sandbox, "run", lambda a, p, approved=False, limits=None: calls.append(("sandbox", a.action.type)) or {"ok": True})
    executor.execute(act(ActionType.RESTART_SERVICE, service="db"), "/p", approved=True)
    executor.execute(act(ActionType.INSTALL_DEPENDENCY, manager="npm"), "/p", approved=True)
    assert calls == [("service", "db"), ("sandbox", ActionType.INSTALL_DEPENDENCY)]


@pytest.mark.parametrize("kind, params", [
    (ActionType.RESTART_SERVICE, {"service": "db"}),
    (ActionType.INSTALL_DEPENDENCY, {"manager": "npm"}),
    (ActionType.RUN_MIGRATION, {"path": "db/schema.sql"}),
])
def test_nothing_runs_without_approval(monkeypatch, kind, params):
    monkeypatch.setattr(docker_services, "start_service", lambda *a: pytest.fail("ran without approval"))
    monkeypatch.setattr(sandbox, "run", lambda *a, **k: pytest.fail("ran without approval"))
    assert act(kind, **params).decision is PolicyDecision.REQUIRE_APPROVAL
    with pytest.raises(PermissionError):
        executor.execute(act(kind, **params), "/p", approved=False)


def test_a_blocked_action_never_runs_even_when_approved(monkeypatch):
    monkeypatch.setattr(sandbox, "run", lambda *a, **k: pytest.fail("ran a blocked action"))
    with pytest.raises(PermissionError):
        executor.execute(act(ActionType.RUN_MIGRATION, path="../../outside.sql"), "/p", approved=True)


def test_a_service_error_becomes_a_failed_result_not_a_crash(monkeypatch):
    def fail(*a):
        raise docker_services.ServiceError("no such container")

    monkeypatch.setattr(docker_services, "start_service", fail)
    assert executor.execute(act(ActionType.RESTART_SERVICE, service="db"), "/p", approved=True) == {"ok": False, "error": "no such container"}


def test_not_undone_lists_each_lasting_effect_once_in_plain_words():
    actions = [act(ActionType.RUN_MIGRATION, path="db/schema.sql"), act(ActionType.SET_ENV_VAR, path=".env", key="A", value="b"),
               act(ActionType.RUN_MIGRATION, path="db/other.sql"), act(ActionType.RESTART_SERVICE, service="db")]
    notes = executor.not_undone(actions)
    assert len(notes) == 2 and "database" in notes[0] and "service" in notes[1]
    assert executor.not_undone([act(ActionType.SET_ENV_VAR, path=".env", key="A", value="b")]) == []  # files: fully undone


# --- the sandbox only gets network where an approved action needs it ---------------------------


class _FakeDocker:
    def __init__(self):
        self.runs = []
        self.images = SimpleNamespace(get=lambda tag: None)
        self.containers = SimpleNamespace(run=self._run)

    def _run(self, image, **kw):
        self.runs.append(kw)
        return SimpleNamespace(wait=lambda timeout=None: {"StatusCode": 0}, logs=lambda: b'DEVOS_RESULT:{"ok":true}\n', remove=lambda force=False: None)


@pytest.fixture
def docker_spy(monkeypatch, tmp_path):
    fake = _FakeDocker()
    monkeypatch.setattr(sandbox.docker, "from_env", lambda: fake)
    fake.project = str(tmp_path)
    return fake


@pytest.mark.parametrize(
    "kind, params, network, scratch",
    [
        (ActionType.SET_ENV_VAR, {"path": ".env", "key": "A", "value": "b"}, "none", "64m"),
        (ActionType.INSTALL_DEPENDENCY, {"manager": "npm"}, "bridge", "512m"),
        (ActionType.RUN_MIGRATION, {"path": "db/schema.sql"}, "bridge", "64m"),
    ],
)
def test_network_is_opened_only_for_the_actions_that_need_it(docker_spy, kind, params, network, scratch):
    sandbox.run(act(kind, **params), docker_spy.project, approved=True)
    kw = docker_spy.runs[0]
    assert kw["network_mode"] == network and f"size={scratch}" in kw["tmpfs"]["/tmp"]
    # every run, whatever the network, keeps the rest of the lock-down
    assert kw["cap_drop"] == ["ALL"] and kw["read_only"] is True and kw["security_opt"] == ["no-new-privileges"]
    assert kw["mem_limit"] == "512m" and kw["nano_cpus"] == 1_000_000_000


def test_probe_and_verify_runs_have_no_network(docker_spy):
    sandbox.run_probe(docker_spy.project)
    sandbox.run_verify(docker_spy.project)
    assert [r["network_mode"] for r in docker_spy.runs] == ["none", "none"]
    assert all(r["extra_hosts"] is None for r in docker_spy.runs)


def test_only_two_action_types_are_ever_given_a_network():
    assert set(sandbox.NETWORK_ACTIONS) == {ActionType.INSTALL_DEPENDENCY, ActionType.RUN_MIGRATION}
    assert SandboxLimits().network == "none"


# --- starting a service only ever touches this project's own containers -----------------------


def _container(name, service, working_dir, status="exited"):
    started = []
    c = SimpleNamespace(name=name, status=status, labels={"com.docker.compose.service": service, "com.docker.compose.project.working_dir": working_dir},
                        attrs={"State": {}}, started=started)
    c.start = lambda: (started.append(name), setattr(c, "status", "running"))
    c.reload = lambda: None
    return c


class _FakeClient:
    def __init__(self, containers):
        self._all = containers
        self.containers = SimpleNamespace(list=lambda all=True, filters=None: [c for c in self._all if c.labels["com.docker.compose.service"] in str(filters)])


def test_only_containers_from_this_projects_compose_folder_are_started(monkeypatch, tmp_path):
    mine = _container("mine-db-1", "db", str(tmp_path))
    theirs = _container("other-db-1", "db", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(docker_services.docker, "from_env", lambda: _FakeClient([mine, theirs]))
    result = docker_services.start_service(str(tmp_path), "db", wait_s=2)
    assert result["ok"] and mine.started == ["mine-db-1"] and theirs.started == []  # the other project's db is untouched


def test_path_spelling_differences_still_match(tmp_path):
    label = str(tmp_path).replace("\\", "/").upper() if os.name == "nt" else str(tmp_path)
    assert docker_services._same_dir(label, tmp_path)
    assert not docker_services._same_dir(None, tmp_path) and not docker_services._same_dir(str(tmp_path / "x"), tmp_path)


@pytest.mark.parametrize("bad", ["", "db; rm -rf /", "$(id)", "a b", "-rf", None, "../x"])
def test_service_names_are_validated(bad):
    with pytest.raises(docker_services.ServiceError, match="invalid service name"):
        docker_services.start_service("/p", bad)


def test_a_service_with_no_container_explains_what_to_do(monkeypatch, tmp_path):
    monkeypatch.setattr(docker_services.docker, "from_env", lambda: _FakeClient([]))
    with pytest.raises(docker_services.ServiceError, match="docker compose up -d db"):
        docker_services.start_service(str(tmp_path), "db")


# --- the new handlers inside the container (checks that run without Docker or a database) -----

RUNNER = REPO_DIR / "sandbox" / "runner.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _runner(work, action_type, params):
    payload = '{"mode":"action","action":{"type":"%s","params":%s}}' % (action_type, params)
    out = subprocess.run(["node", str(RUNNER)], env={**os.environ, "DEVOS_WORK": str(work), "DEVOS_PAYLOAD": payload},
                         capture_output=True, text=True, timeout=60, check=False).stdout
    return out


@needs_node
@pytest.mark.parametrize("params, expected", [
    ('{"manager": "pip"}', 'package manager \\"pip\\" is not supported'),
    ('{"manager": "npm"}', "no package.json"),
])
def test_runner_install_refuses_other_managers_and_projects_without_package_json(tmp_path, params, expected):
    assert expected in _runner(tmp_path, "install_dependency", params)


@needs_node
def test_runner_install_takes_no_package_names(tmp_path):
    """Extra parameters are ignored: a package name in the request can never reach npm's arguments."""
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}')
    out = _runner(tmp_path, "install_dependency", '{"manager": "npm", "packages": ["evil-pkg"], "args": "--foo"}')
    assert "evil-pkg" not in out and not (tmp_path / "node_modules" / "evil-pkg").exists()


@needs_node
@pytest.mark.parametrize("params, prep, expected", [
    ('{"path": "server.js"}', {}, "only .sql files"),
    ('{"path": "../x.sql"}', {}, "path escapes project root"),
    ('{"path": "db/schema.sql"}', {"db/schema.sql": "select 1"}, "DATABASE_URL is not set"),
])
def test_runner_migration_input_checks(tmp_path, params, prep, expected):
    for rel, text in prep.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    assert expected in _runner(tmp_path, "run_migration", params)


# --- describing a step for the person who has to approve it -----------------------------------


def test_describe_gives_plain_words_and_honest_caveats():
    install = executor.describe(act(ActionType.INSTALL_DEPENDENCY, manager="npm"))
    assert "package.json" in install["summary"] and install["needs_network"] and "node_modules" in install["warning"]
    migration = executor.describe(act(ActionType.RUN_MIGRATION, path="db/schema.sql"))
    assert "db/schema.sql" in migration["summary"] and migration["needs_network"] and "database" in migration["warning"]
    service = executor.describe(act(ActionType.RESTART_SERVICE, service="db"))
    assert "'db'" in service["summary"] and not service["needs_network"] and service["warning"]


def test_a_settings_change_needs_no_caveats():
    setting = executor.describe(act(ActionType.SET_ENV_VAR, path=".env", key="PORT", value="3101"))
    assert setting == {"summary": "Set PORT in .env", "needs_network": False, "warning": None}
