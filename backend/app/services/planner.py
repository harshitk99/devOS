"""AI Planner: turns diagnostic findings into a typed recovery plan.

What the plan DOES is decided by the deterministic rule table below (finding -> typed action). An
LLM, when configured, only writes the human explanation for each step (services/llm_planner); it
cannot add, remove, reorder or change any action. If the model is unavailable, fails, or answers
off-script, the rules' own explanations are used, so planning never depends on the model.
Every action goes through policy.classify() before it is returned or stored; nothing produces shell."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.actions import ClassifiedAction, ProposedAction, StepReview
from app.domain.enums import ActionType
from app.services import llm_planner, policy
from app.services.doctor import Finding

log = logging.getLogger(__name__)


@dataclass
class PlanResult:
    actions: list[ClassifiedAction]
    source: str  # "llm" (explanations written by the model) | "rules"
    fallback_reason: str | None = None  # set when the LLM was configured but the rules' text was used
    review: dict | None = None  # AI review summary (status ok|unavailable, order, excluded steps); None = no LLM


# --- rules: finding -> action ------------------------------------------------------------------


def _plan_env_missing(f: Finding) -> ProposedAction | None:
    value = f.evidence.get("example_value", "")
    if not value:  # never invent a value (e.g. a secret) that we cannot derive from the project
        return None
    return ProposedAction(
        type=ActionType.SET_ENV_VAR,
        params={"path": f.evidence.get("env_file", ".env"), "key": f.evidence["key"], "value": value},
        rationale=f"{f.root_cause}; use the default from .env.example",
    )


def _plan_dependency_missing(f: Finding) -> ProposedAction | None:
    return ProposedAction(
        type=ActionType.INSTALL_DEPENDENCY,
        params={"manager": f.evidence.get("manager", "npm")},
        rationale=f"{f.root_cause}; install exactly what package.json lists (install scripts disabled)",
    )


def _plan_port_in_use(f: Finding) -> ProposedAction | None:
    port = f.evidence.get("suggested_port")
    if not port:
        return None
    return ProposedAction(
        type=ActionType.SET_ENV_VAR,
        params={"path": f.evidence.get("env_file", ".env"), "key": f.evidence.get("key", "PORT"), "value": str(port)},
        rationale=f"{f.root_cause}; switch the app to free port {port} (a setting, so it can be undone)",
    )


def _plan_database_unreachable(f: Finding) -> ProposedAction | None:
    service = f.evidence.get("service")
    if not service:
        return None
    return ProposedAction(
        type=ActionType.RESTART_SERVICE,
        params={"service": service},
        rationale=f"{f.root_cause}; start the '{service}' service from the project's compose file",
    )


def _plan_database_auth(f: Finding) -> ProposedAction | None:
    if not (f.evidence.get("example_works") and f.evidence.get("example_value")):
        return None  # only offer a login we have verified actually works
    return ProposedAction(
        type=ActionType.SET_ENV_VAR,
        params={"path": f.evidence.get("env_file", ".env"), "key": f.evidence["key"], "value": f.evidence["example_value"]},
        rationale=f"{f.root_cause}; the login in .env.example was tested and works",
    )


def _plan_schema_missing(f: Finding) -> ProposedAction | None:
    migration = f.evidence.get("migration")
    if not migration:
        return None
    return ProposedAction(
        type=ActionType.RUN_MIGRATION,
        params={"path": migration},
        rationale=f"{f.root_cause}; run {migration} (database changes cannot be rolled back automatically)",
    )


# The order steps run in: settings first, then dependencies, then the database (it must be up before
# its schema is applied). Checks with no rule (runtime_crash, database_missing) are diagnosis-only.
RULES: dict[str, Callable[[Finding], ProposedAction | None]] = {
    "env_missing": _plan_env_missing,
    "dependency_missing": _plan_dependency_missing,
    "port_in_use": _plan_port_in_use,
    "database_unreachable": _plan_database_unreachable,
    "database_auth": _plan_database_auth,
    "schema_missing": _plan_schema_missing,
}


def sourced_proposals(findings: list[Finding]) -> list[tuple[ProposedAction, Finding]]:
    """The rule-made steps in run order, each with the finding whose rule produced it."""
    ordered = sorted((f for f in findings if f.check in RULES), key=lambda f: list(RULES).index(f.check))
    pairs = [(p, f) for f in ordered if (p := RULES[f.check](f)) is not None]
    # Two findings can want the same setting (e.g. PORT missing AND port taken). The later rule knows
    # more (it picked a free port), so it wins, at the earlier step's position.
    latest: dict[tuple, tuple[ProposedAction, Finding]] = {}
    for p, f in pairs:
        if p.type is ActionType.SET_ENV_VAR:
            latest[(p.params["path"], p.params["key"])] = (p, f)
    seen: set[tuple] = set()
    result = []
    for p, f in pairs:
        if p.type is ActionType.SET_ENV_VAR:
            key = (p.params["path"], p.params["key"])
            if key in seen:
                continue
            seen.add(key)
            p, f = latest[key]
        result.append((p, f))
    return result


def rule_proposals(findings: list[Finding]) -> list[ProposedAction]:
    return [p for p, _ in sourced_proposals(findings)]


# --- planning ---------------------------------------------------------------------------------


def plan(findings: list[Finding]) -> PlanResult:
    """Plan with rules. When the LLM is configured it reviews the steps (rules cross-check the review
    and bound the ranking), then explains them; any LLM problem falls back to the plain rule plan."""
    from app.services import plan_review  # local: plan_review needs RULES from this module

    sourced = sourced_proposals(findings)
    source, fallback_reason = "rules", None
    review: dict | None = None
    reviews: list[StepReview] | None = None
    if sourced and llm_planner.enabled():
        try:
            outcome = plan_review.review_plan(findings, sourced)
            sourced, reviews, review = outcome.sourced, outcome.reviews, outcome.summary
        except Exception as e:  # noqa: BLE001 - planning must never fail because the model did
            review = {"status": "unavailable", "reason": f"{type(e).__name__}: {e}"}
            log.warning("LLM plan review unavailable, using the rule plan as is: %s", review["reason"])
    proposals = [p for p, _ in sourced]
    if proposals and llm_planner.enabled():
        try:
            proposals = llm_planner.explain(findings, proposals)
            source = "llm"
        except Exception as e:  # noqa: BLE001
            fallback_reason = f"{type(e).__name__}: {e}"
            log.warning("LLM explanations unavailable, using rule text: %s", fallback_reason)
    actions = [policy.classify(p) for p in proposals]
    if reviews:
        actions = [c.model_copy(update={"review": r}) for c, r in zip(actions, reviews, strict=True)]
    return PlanResult(actions, source, fallback_reason, review)


def targets_of(actions: list[ClassifiedAction]) -> set[tuple[str, str | None]]:
    """Which problems these actions are meant to fix, as (check, setting) pairs, so verification can
    confirm exactly those are gone and not blame a plan for problems it never touched."""
    targets: set[tuple[str, str | None]] = set()
    for c in actions:
        a = c.action
        if a.type is ActionType.SET_ENV_VAR:
            key = a.params.get("key")
            targets |= {("env_missing", key), ("port_in_use", key), ("database_auth", key)}
        elif a.type is ActionType.INSTALL_DEPENDENCY:
            targets.add(("dependency_missing", None))
        elif a.type is ActionType.RESTART_SERVICE:
            targets.add(("database_unreachable", None))
        elif a.type is ActionType.RUN_MIGRATION:
            targets.add(("schema_missing", None))
    return targets


def generate_plan(findings: list[Finding]) -> list[ClassifiedAction]:
    return plan(findings).actions
