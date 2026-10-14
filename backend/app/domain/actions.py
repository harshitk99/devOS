"""Typed action schema exposed to the AI Planner. The planner emits these, never shell strings."""

from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import ActionType, PolicyDecision, RiskLevel


class ProposedAction(BaseModel):
    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class StepReview(BaseModel):
    """The AI's opinion of a rule-made step, after the deterministic cross-check. Advisory only."""

    verified: bool  # the rules reproduced this exact step and policy allows it
    checks: list[str] = Field(default_factory=list)  # what the cross-check confirmed
    verdict: str = "agree"  # the AI's: "agree" | "doubt"
    ai_note: str = ""
    ai_score: int = 0  # 0-100, how urgent the AI thinks the step is; may reorder within a rule tier
    rank: int = 0  # 1-based position after ranking


class ClassifiedAction(BaseModel):
    action: ProposedAction
    risk: RiskLevel
    decision: PolicyDecision
    reason: str
    review: StepReview | None = None  # absent when no AI review ran (no key, or it failed)
