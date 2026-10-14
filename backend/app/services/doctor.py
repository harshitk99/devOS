"""Project Doctor: deterministic checks (code, dependencies, environment, runtime, database,
ports, Git). Compares required vs. actual state. Prefer rules here over LLM judgment.

`diagnose` is a pure function of the manifest, which carries both what the Importer read from the
files and what the probes (services/probes.py) observed in the real environment."""

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import Severity

_ERROR_LINE = re.compile(r"^(?:Uncaught )?\w*(?:Error|Exception)\b:")


class Finding(BaseModel):
    check: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    root_cause: str
    evidence: dict[str, Any] = Field(default_factory=dict)


SEVERITY_WEIGHT = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}


def priority(severity: Severity, confidence: float) -> float:
    """Ranking score: how bad it is, discounted by how sure we are. Deterministic, no LLM."""
    return round(SEVERITY_WEIGHT[severity] * confidence, 2)


def _probes(manifest: dict[str, Any]) -> dict[str, Any]:
    return manifest.get("probes", {})


def check_env_missing(manifest: dict[str, Any]) -> list[Finding]:
    """Every key listed in .env.example must be set (non-empty) in .env."""
    env = manifest.get("env", {})
    present = set(env.get("present", []))
    return [
        Finding(
            check="env_missing",
            severity=Severity.HIGH,
            confidence=0.95,
            root_cause=f"Required environment variable {key} is not set in .env",
            evidence={
                "key": key,
                "env_file": ".env",
                "env_file_exists": env.get("env_file_exists", False),
                "example_value": env.get("example_values", {}).get(key, ""),
            },
        )
        for key in env.get("required", [])
        if key not in present
    ]


def check_dependency_missing(manifest: dict[str, Any]) -> list[Finding]:
    """Packages listed in package.json must be installed."""
    install = manifest.get("install", {})
    missing = install.get("missing", [])
    if not missing:
        return []
    shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
    everything = len(missing) == len(install.get("declared", []))
    return [
        Finding(
            check="dependency_missing",
            severity=Severity.HIGH,
            confidence=0.97,
            root_cause=(
                "Dependencies have not been installed (there is no node_modules folder)"
                if everything and not install.get("node_modules_exists")
                else f"{len(missing)} package(s) the project lists are not installed: {shown}"
            ),
            evidence={"missing": missing, "manager": "npm", "node_modules_exists": install.get("node_modules_exists", False)},
        )
    ]


def check_port_in_use(manifest: dict[str, Any]) -> list[Finding]:
    """The port the app is configured to use must be free."""
    port = _probes(manifest).get("port")
    if not port or not port.get("in_use"):
        return []
    holder = port.get("holder") or {}
    if holder.get("same_project"):  # the project's own server is already running there: not a conflict
        return []
    who = f"{holder.get('name', 'another program')}" + (f" (process {holder['pid']})" if holder.get("pid") else "")
    return [
        Finding(
            check="port_in_use",
            severity=Severity.HIGH,
            confidence=0.9,
            root_cause=f"Port {port['port']} is already in use by {who}, so the app cannot start on it",
            evidence={
                "port": port["port"],
                "holder": holder or None,
                "suggested_port": port.get("suggested_port"),
                "env_file": ".env",
                "key": "PORT",
            },
        )
    ]


def check_database(manifest: dict[str, Any]) -> list[Finding]:
    """The database must be reachable, accept the login, and contain the expected tables."""
    db = _probes(manifest).get("database")
    if not db:
        return []
    where = f"{db.get('host')}:{db.get('port')}/{db.get('database')}"
    if db.get("assumed_from_example"):  # .env has no DATABASE_URL yet; this is the one it would get
        where += " (using the value from .env.example, as .env has none)"
    state = db["state"]
    if state == "unreachable":
        compose = manifest.get("docker", {})
        return [
            Finding(
                check="database_unreachable",
                severity=Severity.HIGH,
                confidence=0.9,
                root_cause=f"The database at {where} is not accepting connections",
                evidence={"host": db.get("host"), "port": db.get("port"), "detail": db.get("detail"),
                          "service": compose.get("postgres_service") if compose.get("file") else None},
            )
        ]
    if state == "auth_failed":
        return [
            Finding(
                check="database_auth",
                severity=Severity.HIGH,
                confidence=0.9,
                root_cause=f"The database at {where} rejected the login for user '{db.get('user')}'",
                evidence={
                    "key": "DATABASE_URL",
                    "env_file": ".env",
                    "user": db.get("user"),
                    "example_works": bool(db.get("example_works")),
                    "example_value": manifest.get("env", {}).get("example_values", {}).get("DATABASE_URL", "")
                    if db.get("example_works") else "",
                },
            )
        ]
    if state == "missing_database":
        return [
            Finding(
                check="database_missing",
                severity=Severity.HIGH,
                confidence=0.9,
                root_cause=f"The database server is up but has no database named '{db.get('database')}'",
                evidence={"database": db.get("database"), "host": db.get("host")},
            )
        ]
    if state == "ok":
        expected = manifest.get("database", {}).get("expected_tables", [])
        missing = [t for t in expected if t not in db.get("tables", [])]
        files = manifest.get("database", {}).get("schema_files", [])
        if missing:
            return [
                Finding(
                    check="schema_missing",
                    severity=Severity.HIGH,
                    confidence=0.95,
                    root_cause=f"The database is missing table(s) the app needs: {', '.join(missing)}",
                    evidence={"missing_tables": missing, "migration": files[0] if files else None},
                )
            ]
    return []


def check_runtime_crash(manifest: dict[str, Any]) -> list[Finding]:
    """The app must survive startup. Crashes already explained by another finding are not repeated."""
    runtime = _probes(manifest).get("runtime")
    if not runtime or not runtime.get("ran") or runtime.get("startup") != "crashed":
        return []
    log = runtime.get("log", "")
    env_missing = bool(manifest.get("env", {}).get("required")) and any(
        k not in manifest.get("env", {}).get("present", []) for k in manifest["env"]["required"]
    )
    if "Missing required environment variable" in log and env_missing:
        return []
    if "Cannot find module" in log and manifest.get("install", {}).get("missing"):
        return []
    lines = [ln.strip() for ln in log.splitlines() if ln.strip()]
    # Prefer the actual error line ("Error: ...") over the source line Node prints above it.
    excerpt = (
        next((ln for ln in lines if _ERROR_LINE.match(ln)), None)
        or next((ln for ln in lines if "error" in ln.lower() and not ln.startswith("at ")), None)
        or (lines[-1] if lines else "no output")
    )
    return [
        Finding(
            check="runtime_crash",
            severity=Severity.HIGH,
            confidence=0.85,
            root_cause=f"The app crashes when it starts: {excerpt[:200]}",
            evidence={"exit_code": runtime.get("exit_code"), "log_tail": log[-800:]},
        )
    ]


# Add new checks here.
CHECKS: list[Callable[[dict[str, Any]], list[Finding]]] = [
    check_env_missing,
    check_dependency_missing,
    check_port_in_use,
    check_database,
    check_runtime_crash,
]


def diagnose(manifest: dict[str, Any]) -> list[Finding]:
    return [finding for check in CHECKS for finding in check(manifest)]
