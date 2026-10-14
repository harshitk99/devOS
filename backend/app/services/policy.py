"""Policy Engine: deterministic, outside the model. Classifies every proposed action as
allow / require_approval / block. Default is deny: an action type with no rule is blocked."""

from pathlib import PurePosixPath, PureWindowsPath

from app.domain.actions import ClassifiedAction, ProposedAction
from app.domain.enums import ActionType, PolicyDecision, RiskLevel

_A, _R, _B = PolicyDecision.ALLOW, PolicyDecision.REQUIRE_APPROVAL, PolicyDecision.BLOCK

# action type -> (risk, decision). Every ActionType member needs an entry here.
RULES: dict[ActionType, tuple[RiskLevel, PolicyDecision]] = {
    ActionType.READ_FILE: (RiskLevel.LOW, _A),
    ActionType.RUN_BUILD: (RiskLevel.LOW, _A),
    ActionType.RUN_TESTS: (RiskLevel.LOW, _A),
    ActionType.WRITE_FILE: (RiskLevel.MEDIUM, _R),
    ActionType.INSTALL_DEPENDENCY: (RiskLevel.MEDIUM, _R),
    ActionType.SET_ENV_VAR: (RiskLevel.MEDIUM, _R),
    ActionType.RESTART_SERVICE: (RiskLevel.MEDIUM, _R),
    ActionType.FREE_PORT: (RiskLevel.HIGH, _R),
    ActionType.RUN_MIGRATION: (RiskLevel.HIGH, _R),
}


def _escapes_project(path: str) -> bool:
    """True if a path is absolute or climbs out of the project root."""
    posix, win = PurePosixPath(path), PureWindowsPath(path)
    return posix.is_absolute() or win.is_absolute() or ".." in posix.parts or ".." in win.parts


def classify(action: ProposedAction) -> ClassifiedAction:
    rule = RULES.get(action.type)
    if rule is None:
        return ClassifiedAction(
            action=action, risk=RiskLevel.HIGH, decision=_B, reason="no policy rule (default deny)"
        )

    path = action.params.get("path")
    if isinstance(path, str) and _escapes_project(path):
        return ClassifiedAction(
            action=action, risk=RiskLevel.HIGH, decision=_B, reason="path escapes project root"
        )

    risk, decision = rule
    return ClassifiedAction(action=action, risk=risk, decision=decision, reason="policy rule")
