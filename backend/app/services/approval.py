"""Approval System: gates plan execution. A plan may run only if every REQUIRE_APPROVAL action
has been explicitly approved by the developer and no action is BLOCKed."""

from app.domain.actions import ClassifiedAction
from app.domain.enums import PolicyDecision


def is_executable(actions: list[ClassifiedAction], approved: bool) -> bool:
    if any(a.decision is PolicyDecision.BLOCK for a in actions):
        return False
    needs_approval = any(a.decision is PolicyDecision.REQUIRE_APPROVAL for a in actions)
    return approved or not needs_approval
