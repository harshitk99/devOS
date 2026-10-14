from app.domain.enums import ActionType, PolicyDecision, Severity
from app.services import doctor, planner
from app.services.importer import import_project


def test_doctor_reports_only_the_missing_key(demo_project):
    findings = doctor.diagnose(import_project(demo_project))
    assert [f.check for f in findings] == ["env_missing"]
    f = findings[0]
    assert f.evidence["key"] == "DATABASE_URL"
    assert f.severity is Severity.HIGH and 0 < f.confidence <= 1


def test_healthy_project_has_no_findings(demo_project):
    (demo_project / ".env").write_text("PORT=3100\nDATABASE_URL=postgresql://x\n")
    assert doctor.diagnose(import_project(demo_project)) == []


def test_planner_proposes_typed_env_fix_that_needs_approval(demo_project):
    plan = planner.generate_plan(doctor.diagnose(import_project(demo_project)))
    assert len(plan) == 1
    action = plan[0]
    assert action.action.type is ActionType.SET_ENV_VAR
    assert action.action.params == {
        "path": ".env",
        "key": "DATABASE_URL",
        "value": "postgresql://u:p@localhost/db",
    }
    assert action.decision is PolicyDecision.REQUIRE_APPROVAL


def test_planner_never_invents_a_value(demo_project):
    (demo_project / ".env.example").write_text("PORT=3100\nDATABASE_URL=\n")
    assert planner.generate_plan(doctor.diagnose(import_project(demo_project))) == []


def test_finding_without_a_rule_yields_no_action():
    unknown = doctor.Finding(check="mystery", severity=Severity.LOW, confidence=0.5, root_cause="?")
    assert planner.generate_plan([unknown]) == []


# --- rules for the newer problem types ---------------------------------------------------------

from app.domain.enums import Severity  # noqa: E402  (kept next to the tests that use it)
from app.services.doctor import Finding  # noqa: E402


def _finding(check, **evidence):
    return Finding(check=check, severity=Severity.HIGH, confidence=0.9, root_cause=f"{check} happened", evidence=evidence)


def _plan(*findings):
    return [(a.action.type, a.action.params, a.decision) for a in planner.plan(list(findings)).actions]


def test_missing_dependencies_get_an_approved_install_of_what_package_json_lists():
    [(kind, params, decision)] = _plan(_finding("dependency_missing", missing=["express"], manager="npm"))
    assert kind is ActionType.INSTALL_DEPENDENCY and params == {"manager": "npm"}  # no package names at all
    assert decision is PolicyDecision.REQUIRE_APPROVAL


def test_port_conflict_changes_the_port_setting_not_the_other_program():
    [(kind, params, _)] = _plan(_finding("port_in_use", port=3100, suggested_port=3101, env_file=".env", key="PORT",
                                        holder={"pid": 42, "name": "python.exe"}))
    assert kind is ActionType.SET_ENV_VAR and params == {"path": ".env", "key": "PORT", "value": "3101"}
    assert ActionType.FREE_PORT not in [k for k, _, _ in _plan(_finding("port_in_use", suggested_port=3101, holder={"pid": 42}))]


def test_port_conflict_with_no_free_port_offers_nothing():
    assert _plan(_finding("port_in_use", port=3100, suggested_port=None)) == []


def test_stopped_database_gets_its_compose_service_started():
    [(kind, params, decision)] = _plan(_finding("database_unreachable", service="db"))
    assert kind is ActionType.RESTART_SERVICE and params == {"service": "db"}
    assert decision is PolicyDecision.REQUIRE_APPROVAL
    assert _plan(_finding("database_unreachable", service=None)) == []


def test_wrong_password_is_only_fixed_with_a_login_that_was_tested():
    url = "postgresql://demo:demo@localhost:5433/demo"
    [(kind, params, _)] = _plan(_finding("database_auth", key="DATABASE_URL", env_file=".env", example_works=True, example_value=url))
    assert kind is ActionType.SET_ENV_VAR and params["value"] == url
    assert _plan(_finding("database_auth", key="DATABASE_URL", example_works=False, example_value="")) == []
    assert _plan(_finding("database_auth", key="DATABASE_URL", example_works=False, example_value=url)) == []


def test_missing_tables_run_the_migration_and_it_is_high_risk():
    [(kind, params, decision)] = _plan(_finding("schema_missing", missing_tables=["notes"], migration="db/schema.sql"))
    assert kind is ActionType.RUN_MIGRATION and params == {"path": "db/schema.sql"}
    assert planner.plan([_finding("schema_missing", migration="db/schema.sql")]).actions[0].risk.value == "high"
    assert decision is PolicyDecision.REQUIRE_APPROVAL


def test_diagnosis_only_problems_produce_no_plan():
    assert _plan(_finding("runtime_crash"), _finding("database_missing", database="x")) == []


def test_steps_run_in_a_safe_order_whatever_order_the_findings_arrive_in():
    findings = [
        _finding("schema_missing", migration="db/schema.sql"),
        _finding("database_unreachable", service="db"),
        _finding("dependency_missing", missing=["a"], manager="npm"),
        _finding("env_missing", key="PORT", env_file=".env", example_value="3100"),
    ]
    kinds = [k for k, _, _ in _plan(*findings)]
    assert kinds == [ActionType.SET_ENV_VAR, ActionType.INSTALL_DEPENDENCY, ActionType.RESTART_SERVICE, ActionType.RUN_MIGRATION]


def test_missing_port_and_busy_port_become_one_step_with_the_free_port():
    steps = _plan(
        _finding("env_missing", key="PORT", env_file=".env", example_value="3100"),
        _finding("port_in_use", port=3100, suggested_port=3101, env_file=".env", key="PORT", holder=None),
    )
    assert [(k, p["key"], p["value"]) for k, p, _ in steps] == [(ActionType.SET_ENV_VAR, "PORT", "3101")]


def test_every_action_the_rules_can_emit_has_a_policy_rule():
    from app.services import policy

    assert {ActionType.INSTALL_DEPENDENCY, ActionType.RESTART_SERVICE, ActionType.RUN_MIGRATION, ActionType.SET_ENV_VAR} <= set(policy.RULES)
