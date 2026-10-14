# PLAN: AI review of the rule plan (cross-check, verify, rank)

Status legend: `[ ]` todo, `[x]` done.

## Goal
The LLM reviews the plan that `planner.RULES` produces. Deterministic code cross-checks that review,
then the verified steps are ranked (AI suggests, rules bound it) and run through the existing
approval -> sandbox -> verification -> rollback path.

Decisions:
- Rules still propose every step. The AI never adds, drops or alters an action.
- The AI returns *opinions about* rule steps (agree/doubt, note, score, "missing" check names).
- Ranking: AI score may reorder steps only **within the same rule tier**; tiers keep the order of
  `list(planner.RULES)` (settings -> packages -> port -> start DB -> login -> migration).
- "Edited in the sandbox" = verified steps are applied by the existing sandbox executor (the sandbox
  mounts the project at `/work` rw). No new `write_file` action.
- Any LLM failure -> exactly today's rule plan, with the review marked `unavailable` + reason.

## Design

### Review (LLM, strict JSON schema) — `services/plan_review.py`
Input: problems + numbered steps, reusing the value-free `llm_planner._payload` (no values,
passwords, logs). Output:
`{"steps": [{"index", "verdict": "agree"|"doubt", "note", "score": 0-100}], "missing": [check, ...]}`.
Unknown / duplicate / missing index, bad verdict, bad score, non-JSON -> `PlanReviewError`.

### Cross-check — `verify_review` (deterministic)
Per step:
1. the rule for the step's source finding in `planner.RULES` reproduces the same action (type + params)
2. `policy.classify` is not `BLOCK`
3. the step is covered by `planner.targets_of` (verification can re-check it)
4. AI `doubt` is a visible warning; it never removes a step
5. AI `missing` is confirmed only if it names a real Doctor finding that has no rule
   (`runtime_crash`, `database_missing`); anything else is ignored
Steps that fail 1-3 are excluded before the plan is stored, so they can never reach the sandbox.

### Rank — `rank_steps`
Tier = index of the step's source check in `list(planner.RULES)`. Sort key:
`(tier, -ai_score, -doctor.priority(finding), original_index)`. AI cannot cross a tier.

### Failure
Any exception from the review step -> rule plan unchanged, `review.status = "unavailable"`.

## Changes
- [x] `backend/app/domain/actions.py`: `StepReview`, `ClassifiedAction.review` (optional; JSONB, no migration)
- [x] `backend/app/services/plan_review.py` (new)
- [x] `backend/app/services/planner.py`: `sourced_proposals` (step + its finding), wire review -> verify -> rank -> explain; `PlanResult.review`
- [x] `backend/app/services/pipeline.py`: audit payload carries the review
- [~] `backend/app/schemas/api.py`: dropped `PlanOut.review_status` (no column to back it; per-step `review` flows out via `ClassifiedAction`, the plan-level summary is in the audit event)
- [x] `backend/tests/test_plan_review.py` (new, 35 tests)
- [x] `frontend/src` Plan tab: AI agrees/doubts badge, "checked by rules", AI note (rank is the step order). No "unavailable" banner (see above)
- [x] `CLAUDE.md`: component table + conventions
- [x] pytest (280 passed, 1 skipped) and `npm run build`
- [ ] `python scripts/run_scenarios.py` and a live LLM run: not run yet (needs Docker + Postgres, and an API key for the LLM path)

Order of calls in `planner.plan()`: rules -> review/cross-check/rank -> explain (last, so its step numbers match the final order).

## Tests (fake provider, no network)
- agree/doubt paths; doubt is shown, step kept
- AI reorders across a tier -> ignored; within a tier -> honoured
- unknown/duplicate/missing index, bad score, invalid JSON -> fallback to plain rule plan
- fake `missing` check -> ignored; real diagnosis-only check -> confirmed
- step whose params differ from what `RULES` reproduces -> excluded; BLOCKed step -> excluded
- payload has no values/secrets
- pipeline: audit event carries review; plan JSON round-trips; legacy plan without `review` loads

## Verification
- `cd backend && .venv/Scripts/python -m pytest`
- `python scripts/run_scenarios.py` (all 9 with LLM off; with a key set check `combo` and `bad-fix`)
- `cd frontend && npm run build`; inject `combo`, open the Plan tab
