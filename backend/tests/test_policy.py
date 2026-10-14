import pytest

from app.domain.actions import ClassifiedAction, ProposedAction
from app.domain.enums import ActionType, PolicyDecision, RiskLevel
from app.services import policy
from app.services.approval import is_executable


def _classify(type: ActionType, **params) -> ClassifiedAction:
    return policy.classify(ProposedAction(type=type, params=params))


def test_every_action_type_has_a_rule():
    assert set(policy.RULES) == set(ActionType)


def test_read_only_actions_are_allowed():
    assert _classify(ActionType.RUN_TESTS).decision is PolicyDecision.ALLOW


def test_state_changing_actions_need_approval():
    assert _classify(ActionType.WRITE_FILE, path="src/app.js").decision is PolicyDecision.REQUIRE_APPROVAL
    assert _classify(ActionType.FREE_PORT, port=3000).risk is RiskLevel.HIGH


@pytest.mark.parametrize("path", ["../secrets", "/etc/passwd", "C:\\Windows\\system32", "a/../../b"])
def test_paths_escaping_project_are_blocked(path):
    assert _classify(ActionType.WRITE_FILE, path=path).decision is PolicyDecision.BLOCK


def test_unruled_action_is_denied_by_default(monkeypatch):
    monkeypatch.delitem(policy.RULES, ActionType.RUN_MIGRATION)
    assert _classify(ActionType.RUN_MIGRATION).decision is PolicyDecision.BLOCK


def test_plan_with_blocked_action_never_executes():
    actions = [_classify(ActionType.RUN_TESTS), _classify(ActionType.WRITE_FILE, path="../x")]
    assert not is_executable(actions, approved=True)


def test_plan_needing_approval_requires_it():
    actions = [_classify(ActionType.SET_ENV_VAR, name="PORT")]
    assert not is_executable(actions, approved=False)
    assert is_executable(actions, approved=True)
