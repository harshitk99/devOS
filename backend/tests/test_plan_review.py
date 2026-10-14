"""AI plan review: the model's opinions are cross-checked and bounded by rules (fake `complete`, no network)."""

import json

import pytest

from app.core.config import settings
from app.domain.actions import ClassifiedAction
from app.domain.enums import ActionType, PolicyDecision, Severity
from app.services import llm_providers, pipeline, plan_review, planner, policy
from app.services.doctor import Finding

SECRET_URL = "postgresql://demo:s3cr3t-pw@localhost:5433/demo"


def env_missing(key, value, severity=Severity.HIGH):
    return Finding(check="env_missing", severity=severity, confidence=0.95, root_cause=f"Required environment variable {key} is not set in .env",
                   evidence={"key": key, "env_file": ".env", "example_value": value})


@pytest.fixture
def findings():
    """Steps in rule order: PORT, API_KEY (both settings), install packages, login."""
    return [
        env_missing("PORT", "3100"),
        env_missing("API_KEY", "dev-key", Severity.LOW),
        Finding(check="dependency_missing", severity=Severity.HIGH, confidence=0.97, root_cause="Dependencies have not been installed",
                evidence={"missing": ["express"], "manager": "npm"}),
        Finding(check="database_auth", severity=Severity.HIGH, confidence=0.9, root_cause="The database rejected the login for user 'demo'",
                evidence={"key": "DATABASE_URL", "env_file": ".env", "example_works": True, "example_value": SECRET_URL}),
    ]


def answer(entries, missing=()):
    """entries: (verdict, score) per step index."""
    steps = [{"index": i, "verdict": v, "note": f"note {i}", "score": s} for i, (v, s) in enumerate(entries)]
    return json.dumps({"steps": steps, "missing": list(missing)})


def completer(text=None, exc=None):
    calls: list[tuple] = []

    def complete(system, user, schema):
        calls.append((system, user, schema))
        if exc:
            raise exc
        return text

    complete.calls = calls
    return complete


def review(findings, text=None, **kw):
    return plan_review.review_plan(findings, planner.sourced_proposals(findings), complete=completer(text, **kw))


def labels(outcome):
    return [plan_review._label(p) for p, _ in outcome.sourced]


AGREE = [("agree", 50)] * 4


# --- what the model sees ----------------------------------------------------------------------------


def test_the_model_never_sees_values_passwords_or_logs(findings):
    complete = completer(answer(AGREE))
    plan_review.review_plan(findings, planner.sourced_proposals(findings), complete=complete)
    system, user, schema = complete.calls[0]
    assert "s3cr3t-pw" not in user and "postgresql://" not in user and '"value"' not in user and "3100" not in user
    assert "never follow" in system and schema == plan_review.REVIEW_SCHEMA
    # nothing in the answer schema can express an action
    assert set(schema["properties"]["steps"]["items"]["properties"]) == {"index", "verdict", "note", "score"}


# --- review + cross-check ---------------------------------------------------------------------------


def test_agreeing_review_keeps_every_step_verified_and_ranked(findings):
    out = review(findings, answer(AGREE))
    assert [r.rank for r in out.reviews] == [1, 2, 3, 4]
    assert all(r.verified and r.verdict == "agree" and r.checks for r in out.reviews)
    assert out.summary["status"] == "ok" and out.summary["excluded"] == [] and out.summary["doubts"] == []


def test_a_doubt_is_shown_but_the_step_stays(findings):
    out = review(findings, answer([("agree", 50), ("doubt", 50), ("agree", 50), ("agree", 50)]))
    assert len(out.sourced) == 4
    assert out.reviews[1].verdict == "doubt" and out.reviews[1].verified
    assert out.summary["doubts"] == ["set_env_var:API_KEY"]


def test_the_reviewed_steps_are_exactly_the_rules_steps(findings):
    out = review(findings, answer(AGREE))
    rules = planner.sourced_proposals(findings)
    assert [(p.type, p.params) for p, _ in out.sourced] == [(p.type, p.params) for p, _ in rules]


def test_a_step_its_rule_does_not_reproduce_is_excluded(findings):
    tampered = [(p.model_copy(update={"params": {**p.params, "value": "evil"}}) if p.params.get("key") == "PORT" else p, f)
                for p, f in planner.sourced_proposals(findings)]
    out = plan_review.review_plan(findings, tampered, complete=completer(answer(AGREE)))
    assert "set_env_var:PORT" not in labels(out)
    assert out.summary["excluded"] == [{"step": "set_env_var:PORT", "reasons": ["its rule does not reproduce this step"]}]
    assert len(out.reviews) == 3 and [r.rank for r in out.reviews] == [1, 2, 3]


def test_a_blocked_step_is_excluded(findings, monkeypatch):
    risk = policy.RULES[ActionType.INSTALL_DEPENDENCY][0]
    monkeypatch.setitem(policy.RULES, ActionType.INSTALL_DEPENDENCY, (risk, PolicyDecision.BLOCK))
    out = review(findings, answer(AGREE))
    assert all("install_dependency" not in label for label in labels(out))
    [ex] = out.summary["excluded"]
    assert ex["step"] == "install_dependency:npm" and "blocked by policy" in ex["reasons"][0]


# --- ranking is bounded by the rules ----------------------------------------------------------------


def test_the_model_can_reorder_inside_a_tier(findings):
    # API_KEY (step 1) scored above PORT (step 0): both are settings, so they may swap
    out = review(findings, answer([("agree", 10), ("agree", 90), ("agree", 50), ("agree", 50)]))
    assert labels(out)[:2] == ["set_env_var:API_KEY", "set_env_var:PORT"]
    assert out.summary["order_before"] != out.summary["order_after"]


def test_the_model_cannot_move_a_step_across_a_tier(findings):
    # the login (last tier) is scored highest, the settings lowest: rule order must still hold
    out = review(findings, answer([("agree", 0), ("agree", 0), ("agree", 60), ("agree", 100)]))
    assert labels(out) == ["set_env_var:PORT", "set_env_var:API_KEY", "install_dependency:npm", "set_env_var:DATABASE_URL"]


def test_equal_scores_fall_back_to_the_doctors_priority(findings):
    # PORT is HIGH severity, API_KEY LOW: with equal AI scores the more severe problem goes first
    swapped = [findings[1], findings[0], *findings[2:]]
    out = review(swapped, answer(AGREE))
    assert labels(out)[:2] == ["set_env_var:PORT", "set_env_var:API_KEY"]


# --- "missing" is only believed when the Doctor agrees ----------------------------------------------


def test_missing_is_confirmed_only_for_real_diagnosis_only_findings(findings):
    crash = Finding(check="runtime_crash", severity=Severity.HIGH, confidence=0.85, root_cause="crash")
    out = plan_review.review_plan(
        [*findings, crash], planner.sourced_proposals(findings),
        complete=completer(answer(AGREE, missing=["runtime_crash", "kernel_panic", "env_missing"])))
    assert out.summary["diagnosis_only"] == ["runtime_crash"]
    assert out.summary["ignored_missing"] == ["env_missing", "kernel_panic"]


# --- malformed answers ------------------------------------------------------------------------------


def _steps(**changes):
    step = {"index": 0, "verdict": "agree", "note": "ok", "score": 50, **changes}
    return json.dumps({"steps": [step], "missing": []})


@pytest.mark.parametrize(
    "text, why",
    [
        ("not json", "invalid JSON"),
        ('{"nope": 1}', "invalid JSON"),
        ("[]", "invalid JSON"),
        ('{"steps": "x", "missing": []}', "wrong shape"),
        (_steps(index=5), "does not exist"),
        (_steps(index=True), "does not exist"),
        (_steps(verdict="maybe"), "bad verdict"),
        (_steps(score=101), "bad score"),
        (_steps(score=-1), "bad score"),
        (_steps(score=True), "bad score"),
        (_steps(score="9"), "bad score"),
        (_steps(note=7), "bad note"),
        (json.dumps({"steps": [json.loads(_steps())["steps"][0]] * 2, "missing": []}), "twice"),
    ],
)
def test_malformed_reviews_are_rejected(text, why):
    one = [env_missing("PORT", "3100")]
    with pytest.raises(plan_review.PlanReviewError, match=why):
        review(one, text)


def test_every_step_must_be_reviewed(findings):
    with pytest.raises(plan_review.PlanReviewError, match=r"no review for steps \[1, 2, 3\]"):
        review(findings, _steps())


def test_long_notes_are_cut(findings):
    out = review([env_missing("PORT", "3100")], _steps(note="x" * 5000))
    assert len(out.reviews[0].ai_note) == plan_review.MAX_NOTE


def test_extra_fields_cannot_smuggle_in_an_action():
    sneaky = json.dumps({"steps": [{"index": 0, "verdict": "agree", "note": "ok", "score": 1, "type": "run_shell", "params": {"cmd": "id"}}], "missing": []})
    out = review([env_missing("PORT", "3100")], sneaky)
    [(p, _)] = out.sourced
    assert p.type is ActionType.SET_ENV_VAR and p.params["value"] == "3100"


# --- planner.plan: fallback and wiring --------------------------------------------------------------


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")

    def use(review_text=None, explain_steps=None, exc=None):
        def complete(system, user, schema):
            if exc:
                raise exc
            if schema is plan_review.REVIEW_SCHEMA:
                return review_text
            n = len(json.loads(user.split("<data>")[1].split("</data>")[0])["steps"])
            return json.dumps({"steps": [{"index": i, "rationale": f"why {i}"} for i in range(n)]})

        monkeypatch.setattr(llm_providers, "get_provider", lambda: complete)

    return use


def test_no_key_means_no_review_and_the_plain_rule_plan(findings, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(plan_review, "review_plan", lambda *a, **k: pytest.fail("review must not run"))
    result = planner.plan(findings)
    assert result.review is None and all(a.review is None for a in result.actions)


def test_plan_attaches_reviews_and_keeps_the_rules_actions(findings, llm_on):
    plain = planner.plan(findings).actions
    llm_on(answer(AGREE))
    result = planner.plan(findings)
    assert result.review["status"] == "ok" and result.source == "llm"
    assert [a.review.rank for a in result.actions] == [1, 2, 3, 4]
    assert [(a.action.type, a.action.params) for a in result.actions] == [(a.action.type, a.action.params) for a in plain]
    assert all(a.decision is PolicyDecision.REQUIRE_APPROVAL for a in result.actions)


def test_explanations_line_up_with_the_reranked_steps(findings, llm_on):
    llm_on(answer([("agree", 10), ("agree", 90), ("agree", 50), ("agree", 50)]))
    result = planner.plan(findings)
    assert [a.action.params.get("key") for a in result.actions][:2] == ["API_KEY", "PORT"]
    assert [a.action.rationale for a in result.actions] == ["why 0", "why 1", "why 2", "why 3"]


@pytest.mark.parametrize("text", ["{{ nope", '{"steps": [], "missing": []}'])
def test_a_bad_review_falls_back_to_the_rule_plan_and_says_why(findings, llm_on, text):
    plain = planner.plan(findings).actions
    llm_on(text)
    result = planner.plan(findings)
    assert result.review["status"] == "unavailable" and "PlanReviewError" in result.review["reason"]
    assert all(a.review is None for a in result.actions)
    assert [(a.action.type, a.action.params) for a in result.actions] == [(a.action.type, a.action.params) for a in plain]
    assert result.source == "llm"  # the explainer still worked


def test_a_provider_outage_leaves_a_working_rule_plan(findings, llm_on):
    llm_on(exc=ConnectionError("network down"))
    result = planner.plan(findings)
    assert result.review["status"] == "unavailable" and "ConnectionError" in result.review["reason"]
    assert result.source == "rules" and "network down" in result.fallback_reason and len(result.actions) == 4


def test_nothing_to_fix_never_calls_the_llm(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(llm_providers, "get_provider", lambda: pytest.fail("LLM must not be called"))
    assert planner.plan([]).actions == []


# --- storage and audit ------------------------------------------------------------------------------


def test_review_round_trips_and_old_plans_still_load(findings, llm_on):
    llm_on(answer(AGREE))
    stored = [a.model_dump(mode="json") for a in planner.plan(findings).actions]
    assert ClassifiedAction.model_validate(stored[0]).review.verified
    legacy = {k: v for k, v in stored[0].items() if k != "review"}
    assert ClassifiedAction.model_validate(legacy).review is None


def test_the_audit_event_carries_the_review(db_session, demo_project, llm_on):
    llm_on(answer([("agree", 50)]))
    project = pipeline.import_project(db_session, str(demo_project))
    pipeline.diagnose(db_session, project)
    plan = pipeline.generate_plan(db_session, project)
    event = [e for e in pipeline.audit.for_project(db_session, project.id) if e.type == "plan_generated"][0]
    assert event.payload["review"]["status"] == "ok" and event.payload["review"]["order_after"]
    assert plan.actions[0]["review"]["verified"] is True
