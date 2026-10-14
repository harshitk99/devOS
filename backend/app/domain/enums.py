from enum import StrEnum


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


class Verdict(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY VERIFIED"
    FAILED = "FAILED"
    NOT_TESTED = "NOT TESTED"


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AuditEventType(StrEnum):
    PROJECT_IMPORTED = "project_imported"
    DIAGNOSIS_RUN = "diagnosis_run"
    PLAN_GENERATED = "plan_generated"
    PLAN_APPROVED = "plan_approved"
    PLAN_REJECTED = "plan_rejected"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_FINISHED = "execution_finished"
    VERIFICATION_COMPLETED = "verification_completed"
    ROLLBACK_PERFORMED = "rollback_performed"


class ActionType(StrEnum):
    """The closed set of things the AI Planner may propose. Adding a member requires a
    matching rule in services/policy.py (see CLAUDE.md working conventions)."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    INSTALL_DEPENDENCY = "install_dependency"
    SET_ENV_VAR = "set_env_var"
    RESTART_SERVICE = "restart_service"
    FREE_PORT = "free_port"
    RUN_BUILD = "run_build"
    RUN_TESTS = "run_tests"
    RUN_MIGRATION = "run_migration"
