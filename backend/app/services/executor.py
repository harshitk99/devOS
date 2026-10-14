"""Runs ONE approved action. Nearly everything runs in the Docker sandbox; starting a stopped
service goes through services/docker_services (Docker API, limited to the project's own compose
containers). Both refuse anything the policy engine has not cleared: BLOCKed actions never run,
and REQUIRE_APPROVAL ones run only when the plan was approved."""

from typing import Any

from app.domain.actions import ClassifiedAction
from app.domain.enums import ActionType
from app.services import approval, docker_services, sandbox

# What a rollback cannot take back. A rollback restores the project's FILES from the snapshot;
# these effects live outside them, so DevOS says so instead of implying everything was undone.
NOT_UNDONE: dict[ActionType, str] = {
    ActionType.INSTALL_DEPENDENCY: "installed packages stay in node_modules (package.json and lock files were restored)",
    ActionType.RUN_MIGRATION: "the database migration stays applied (database changes are not rolled back)",
    ActionType.RESTART_SERVICE: "the service that was started keeps running",
}


def execute(action: ClassifiedAction, project_path: str, approved: bool) -> dict[str, Any]:
    if not approval.is_executable([action], approved):
        raise PermissionError(f"action {action.action.type} is not permitted to run ({action.decision})")
    if action.action.type is ActionType.RESTART_SERVICE:
        try:
            return docker_services.start_service(project_path, action.action.params.get("service"))
        except docker_services.ServiceError as e:
            return {"ok": False, "error": str(e)}
    return sandbox.run(action, project_path, approved=approved)


def not_undone(actions: list[ClassifiedAction]) -> list[str]:
    """Plain-language list of effects a file rollback leaves in place, for actions that ran."""
    seen: list[str] = []
    for a in actions:
        note = NOT_UNDONE.get(a.action.type)
        if note and note not in seen:
            seen.append(note)
    return seen


def describe(action: ClassifiedAction) -> dict[str, Any]:
    """What a step does, in words, plus the honest caveats a reviewer needs before approving:
    whether it needs network access and whether a rollback can take it back."""
    a = action.action
    p = a.params
    summary = {
        ActionType.SET_ENV_VAR: f"Set {p.get('key')} in {p.get('path', '.env')}",
        ActionType.INSTALL_DEPENDENCY: "Install the packages listed in package.json (install scripts are skipped)",
        ActionType.RESTART_SERVICE: f"Start the '{p.get('service')}' service from the project's compose file",
        ActionType.RUN_MIGRATION: f"Run {p.get('path')} against the project's database, in one transaction",
        ActionType.WRITE_FILE: f"Write {p.get('path')}",
        ActionType.READ_FILE: f"Read {p.get('path')}",
    }.get(a.type, a.type.value.replace("_", " "))
    return {
        "summary": summary,
        "needs_network": a.type in sandbox.NETWORK_ACTIONS,
        "warning": NOT_UNDONE.get(a.type) and f"Rolling back will not undo this: {NOT_UNDONE[a.type]}.",
    }
