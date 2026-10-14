"""Verification Engine: proves a change actually helped, then returns a Verdict.

Layers, each able to catch a different kind of failure:
  1. startup + health   the app starts in the sandbox and answers /health
  2. build              the project's own build script passes
  3. tests              the project's own test script passes
  4. log review         no error lines in the app's output
  5. re-check           the Doctor runs again and every problem the plan was meant to fix is gone

Ground truth comes from these deterministic rules in `decide`, never from an LLM. FAILED is what
triggers an automatic rollback, so it is reserved for "the app does not work, or the problem the
plan targeted is still there"; softer problems (failing tests, log noise, other unrelated problems
still present) give PARTIALLY VERIFIED and keep the change."""

import re
import time
from typing import Any

from app.domain.enums import Verdict
from app.services import checkup, sandbox

_ERROR_LINE = re.compile(r"\b(error|exception|fatal|unhandled|traceback)\b", re.IGNORECASE)
Target = tuple[str, str | None]  # (finding check, the setting it is about, e.g. ("env_missing", "PORT"))

# A just-started database takes a few seconds to accept connections; give it that long to show up.
SETTLE_CHECKS = {"database_unreachable"}
SETTLE_TRIES, SETTLE_DELAY_S = 8, 2.0


def decide(results: dict[str, Any]) -> Verdict:
    """Map the gathered results (startup / build / tests / logs / re-check) to a verdict."""
    recheck = results.get("recheck") or {}
    if results.get("startup") == "not_run":  # no start script: only the re-check can say anything
        if recheck.get("remaining"):
            return Verdict.FAILED
        return Verdict.PARTIALLY_VERIFIED if "remaining" in recheck else Verdict.NOT_TESTED

    health = results.get("health") or {}
    if results.get("startup") != "ok" or health.get("status") != 200:
        return Verdict.FAILED
    if (results.get("build") or {}).get("status") == "failed":
        return Verdict.FAILED
    if recheck.get("remaining"):  # the problem the plan targeted is still there
        return Verdict.FAILED

    tests_failed = (results.get("tests") or {}).get("status") == "failed"
    if tests_failed or results.get("log_errors") or recheck.get("other") or results.get("recheck_error"):
        return Verdict.PARTIALLY_VERIFIED
    return Verdict.VERIFIED


def _label(target: Target) -> str:
    return target[0] if target[1] is None else f"{target[0]}:{target[1]}"


def recheck(project_path: str, targeted: set[Target]) -> dict[str, Any]:
    """Run the whole health check again. `remaining` = problems the plan targeted that are still
    there; `other` = problems that were never the plan's business (still present). A finding counts
    as targeted only if both its type and the setting it is about match."""
    settle = any(check in SETTLE_CHECKS for check, _ in targeted)
    is_target = lambda f: (f.check, f.evidence.get("key")) in targeted or (f.check, None) in targeted  # noqa: E731
    for attempt in range(SETTLE_TRIES if settle else 1):
        _, findings = checkup.run(project_path, runtime=False)  # the sandbox run already covers startup
        remaining = [f for f in findings if is_target(f)]
        if not (remaining and settle and attempt < SETTLE_TRIES - 1):
            break
        time.sleep(SETTLE_DELAY_S)
    summarize = lambda fs: [{"check": f.check, "root_cause": f.root_cause} for f in fs]  # noqa: E731
    return {
        "targeted": sorted(_label(t) for t in targeted),
        "remaining": summarize(remaining),
        "other": summarize(f for f in findings if not is_target(f)),
    }


def verify(project_path: str, targeted: set[Target] | None = None) -> tuple[Verdict, dict[str, Any]]:
    try:
        results = sandbox.run_verify(project_path)
    except (sandbox.SandboxError, PermissionError) as e:
        return Verdict.NOT_TESTED, {"error": str(e)}
    if not results.get("ok", True):  # the sandbox failed, not the app: cannot conclude anything
        return Verdict.NOT_TESTED, {"error": results.get("error", "sandbox error")}

    log = results.get("log", "")
    results["log_errors"] = [line for line in log.splitlines() if _ERROR_LINE.search(line)]
    if targeted is not None:
        try:
            results["recheck"] = recheck(project_path, targeted)
        except Exception as e:  # noqa: BLE001 - a broken re-check must not masquerade as a pass
            results["recheck_error"] = str(e)
    return decide(results), results
