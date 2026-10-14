"""The Doctor's new checks, driven by hand-built manifests (no Docker, database or ports needed)."""

import pytest

from app.domain.enums import Severity
from app.services import doctor


def checks(manifest):
    return {f.check: f for f in doctor.diagnose(manifest)}


# --- dependencies -----------------------------------------------------------------------------


def test_missing_packages_are_reported_once_with_the_names():
    manifest = {"install": {"declared": ["express", "pg", "zod"], "missing": ["express", "pg"], "node_modules_exists": True}}
    f = checks(manifest)["dependency_missing"]
    assert f.evidence["missing"] == ["express", "pg"] and "express, pg" in f.root_cause
    assert f.severity is Severity.HIGH


def test_no_node_modules_at_all_is_described_plainly():
    manifest = {"install": {"declared": ["a"], "missing": ["a"], "node_modules_exists": False}}
    assert "no node_modules" in checks(manifest)["dependency_missing"].root_cause


def test_everything_installed_is_fine():
    assert "dependency_missing" not in checks({"install": {"declared": ["a"], "missing": [], "node_modules_exists": True}})


# --- ports ------------------------------------------------------------------------------------


def _port(**over):
    return {"probes": {"port": {"port": 3100, "in_use": True, "suggested_port": 3101,
                                "holder": {"pid": 42, "name": "python.exe", "same_project": False}, **over}}}


def test_port_conflict_names_the_holder_and_suggests_a_free_port():
    f = checks(_port())["port_in_use"]
    assert "python.exe" in f.root_cause and "3100" in f.root_cause
    assert f.evidence["suggested_port"] == 3101 and f.evidence["key"] == "PORT"


def test_free_port_or_own_server_is_not_a_conflict():
    assert "port_in_use" not in checks({"probes": {"port": {"port": 3100, "in_use": False}}})
    own = _port(holder={"pid": 1, "name": "node.exe", "same_project": True})
    assert "port_in_use" not in checks(own)


def test_port_conflict_with_unknown_holder_still_reported():
    assert "another program" in checks(_port(holder=None))["port_in_use"].root_cause


# --- database ---------------------------------------------------------------------------------

DB = {"host": "localhost", "port": 5433, "database": "demo", "user": "demo", "tables": []}


def _db(state, **over):
    return {"probes": {"database": {**DB, "state": state, **over}}, "docker": {"file": "docker-compose.yml", "postgres_service": "db"},
            "database": {"expected_tables": ["notes"], "schema_files": ["db/schema.sql"]},
            "env": {"example_values": {"DATABASE_URL": "postgresql://demo:demo@localhost:5433/demo"}}}


def test_unreachable_database_points_at_the_compose_service():
    f = checks(_db("unreachable"))["database_unreachable"]
    assert f.evidence["service"] == "db" and "localhost:5433/demo" in f.root_cause


def test_unreachable_without_a_compose_file_has_no_service_to_start():
    manifest = _db("unreachable")
    manifest["docker"] = {"file": None, "postgres_service": None}
    assert checks(manifest)["database_unreachable"].evidence["service"] is None


def test_wrong_password_offers_the_example_value_only_if_it_works():
    good = checks(_db("auth_failed", example_works=True))["database_auth"]
    assert good.evidence["example_value"].startswith("postgresql://demo") and good.evidence["example_works"]
    bad = checks(_db("auth_failed", example_works=False))["database_auth"]
    assert bad.evidence["example_value"] == "" and not bad.evidence["example_works"]


def test_database_finding_never_contains_a_password():
    for f in doctor.diagnose(_db("auth_failed", example_works=False, detail="password authentication failed")):
        assert "wrong-password" not in f.model_dump_json()


def test_missing_database_is_diagnosis_only():
    assert "database_missing" in checks(_db("missing_database"))


def test_missing_tables_point_at_the_migration_file():
    f = checks(_db("ok", tables=[]))["schema_missing"]
    assert f.evidence == {"missing_tables": ["notes"], "migration": "db/schema.sql"}


def test_healthy_database_has_no_findings():
    assert not [c for c in checks(_db("ok", tables=["notes"])) if c.startswith(("database", "schema"))]


# --- runtime crash ----------------------------------------------------------------------------


def _crash(log, **extra):
    return {"probes": {"runtime": {"ran": True, "startup": "crashed", "exit_code": 1, "log": log}}, **extra}


def test_unexplained_crash_is_reported_with_the_error_line():
    f = checks(_crash("starting\nError: FORCE_CRASH is set\n  at x"))["runtime_crash"]
    assert "FORCE_CRASH is set" in f.root_cause and f.evidence["exit_code"] == 1


def test_crash_already_explained_by_missing_env_is_not_repeated():
    manifest = _crash("Error: Missing required environment variable: DATABASE_URL",
                      env={"required": ["DATABASE_URL"], "present": [], "example_values": {}})
    assert "runtime_crash" not in checks(manifest)


def test_crash_already_explained_by_missing_packages_is_not_repeated():
    manifest = _crash("Error: Cannot find module 'express'", install={"declared": ["express"], "missing": ["express"]})
    assert "runtime_crash" not in checks(manifest)


def test_undeclared_missing_module_is_still_a_crash():
    manifest = _crash("Error: Cannot find module 'left-pad'", install={"declared": [], "missing": []})
    assert "runtime_crash" in checks(manifest)


@pytest.mark.parametrize("runtime", [{"ran": False, "reason": "docker down"}, {"ran": True, "startup": "ok"}])
def test_no_crash_no_finding(runtime):
    assert "runtime_crash" not in checks({"probes": {"runtime": runtime}})


# --- ranking ----------------------------------------------------------------------------------


def test_priority_weighs_severity_by_confidence():
    assert doctor.priority(Severity.HIGH, 0.95) == 2.85
    assert doctor.priority(Severity.LOW, 1.0) < doctor.priority(Severity.MEDIUM, 0.6) + 0.5
    assert doctor.priority(Severity.CRITICAL, 0.5) == 2.0


def test_crash_excerpt_prefers_the_error_line_over_the_source_line_node_prints_above_it():
    log = (
        "/app/server.js:26\n"
        'if (process.env.FORCE_CRASH) throw new Error("FORCE_CRASH is set: simulated startup failure");\n'
        "                             ^\n\n"
        "Error: FORCE_CRASH is set: simulated startup failure\n"
        "    at Object.<anonymous> (/app/server.js:26:37)\n"
    )
    root_cause = checks(_crash(log))["runtime_crash"].root_cause
    assert root_cause == "The app crashes when it starts: Error: FORCE_CRASH is set: simulated startup failure"


def test_a_database_problem_found_through_the_example_value_says_so():
    manifest = _db("unreachable", assumed_from_example=True)
    assert "using the value from .env.example" in checks(manifest)["database_unreachable"].root_cause
    assert "using the value" not in checks(_db("unreachable", assumed_from_example=False))["database_unreachable"].root_cause
