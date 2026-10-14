"""Read-only probes of the real environment, run from the host during diagnosis.

They gather facts (is the port free, does the database answer, does the app crash on start); the
Project Doctor turns those facts into findings with deterministic rules. Nothing here changes
anything: it only binds a port briefly to test it, runs SELECTs, and starts the app in the sandbox.
Database passwords are used to connect but never returned or logged."""

import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil
import psycopg

from app.services import sandbox
from app.services.importer import parse_env

_ADDRESS_IN_USE = (10048, 98, 48)  # WSAEADDRINUSE, Linux, macOS


# --- ports ------------------------------------------------------------------------------------


def _try_bind(family: int, address: str, port: int) -> bool | None:
    """True = bound (port free), False = address already in use, None = could not tell."""
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return None  # this address family is not available on the machine
    try:
        if family == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # also take IPv4, like Node
        s.bind((address, port))
        return True
    except OSError as e:
        return False if e.errno in _ADDRESS_IN_USE else None  # e.g. permission denied: unknown
    finally:
        s.close()


def _can_bind(port: int) -> bool:
    """Bind the way Node's listen(port) does (dual-stack wildcard). Testing a different address
    family can wrongly report a port as free on Windows. Unknown counts as free: no false alarms."""
    result = _try_bind(socket.AF_INET6, "::", port)
    if result is None:
        result = _try_bind(socket.AF_INET, "0.0.0.0", port)
    return result is not False


def find_free_port(start: int, limit: int = 200) -> int | None:
    for port in range(max(start, 1024), min(start + limit, 65535)):
        if _can_bind(port):
            return port
    return None


def _holder(port: int, project: Path | None) -> dict[str, Any] | None:
    """Best effort: which local process is listening on the port."""
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port and conn.pid:
                proc = psutil.Process(conn.pid)
                same = False
                try:
                    cwd = Path(proc.cwd()).resolve()
                    same = project is not None and (cwd == project or project in cwd.parents)
                except (psutil.Error, OSError):
                    pass
                return {"pid": conn.pid, "name": proc.name(), "same_project": same}
    except (psutil.Error, OSError):
        pass
    return None


def port_status(port: int, project: Path | None = None) -> dict[str, Any]:
    free = _can_bind(port)
    return {
        "port": port,
        "in_use": not free,
        "holder": None if free else _holder(port, project.resolve() if project else None),
        "suggested_port": None if free else find_free_port(port + 1),
    }


# --- database ---------------------------------------------------------------------------------


def _scrub(text: str, secret: str | None) -> str:
    text = text.strip().replace("\n", " ")
    return (text.replace(secret, "***") if secret else text)[:240]


def _classify(e: psycopg.Error) -> str:
    """Why did the connection fail? A rejected connection carries no error code in psycopg, only the
    server's message ('FATAL:  password authentication failed...'), so read that too."""
    sqlstate = getattr(e, "sqlstate", None)
    text = str(e)
    if sqlstate in ("28P01", "28000") or "password authentication failed" in text or "no pg_hba.conf entry" in text:
        return "auth_failed"
    if sqlstate == "3D000" or ("FATAL:" in text and 'database "' in text and "does not exist" in text):
        return "missing_database"
    if isinstance(e, psycopg.OperationalError) and sqlstate is None:
        return "unreachable"  # refused, timed out, or the host name did not resolve
    return "error"


def database_status(url: str, timeout: int = 3) -> dict[str, Any]:
    """Can we connect, and which tables exist? state is one of ok | unreachable | auth_failed |
    missing_database | error. The URL (and its password) is never included in the result."""
    try:
        parts = urlsplit(url)
        info = {"host": parts.hostname, "port": parts.port or 5432, "database": parts.path.lstrip("/"), "user": parts.username}
    except ValueError:
        return {"state": "error", "tables": [], "detail": "DATABASE_URL is not a valid URL"}
    try:
        with psycopg.connect(url, connect_timeout=timeout) as conn:
            rows = conn.execute("select table_name from information_schema.tables where table_schema = 'public'").fetchall()
        return {**info, "state": "ok", "tables": sorted(r[0] for r in rows)}
    except psycopg.Error as e:
        return {**info, "state": _classify(e), "tables": [], "detail": _scrub(str(e), parts.password)}


# --- running the app --------------------------------------------------------------------------


def runtime_probe(project_path: str) -> dict[str, Any]:
    """Start the app in the sandbox and report whether it stayed up. Needs Docker."""
    try:
        result = sandbox.run_probe(project_path)
    except (sandbox.SandboxError, OSError) as e:
        return {"ran": False, "reason": str(e)}
    if not result.get("ok"):  # the sandbox itself failed; that says nothing about the app
        return {"ran": False, "reason": result.get("error", "sandbox error")}
    return {
        "ran": True,
        "startup": result.get("startup"),
        "exit_code": result.get("exit_code"),
        "health": result.get("health"),
        "log": result.get("log", "")[-1500:],
    }


# --- everything the Doctor needs --------------------------------------------------------------


def collect(project_path: str, manifest: dict[str, Any], runtime: bool = True) -> dict[str, Any]:
    root = Path(project_path)
    env_file = root / ".env"
    env = parse_env(env_file.read_text(encoding="utf-8", errors="replace")) if env_file.is_file() else {}
    probes: dict[str, Any] = {}

    port = manifest.get("config", {}).get("port")
    if port:
        probes["port"] = port_status(port, root)

    example = manifest.get("env", {}).get("example_values", {}).get("DATABASE_URL")
    # If .env has no URL yet, look at the database through the one .env.example offers: that is what the
    # app will use once the missing setting is fixed, and it stops that gap from hiding a second problem.
    url, assumed = (env["DATABASE_URL"], False) if env.get("DATABASE_URL") else (example, True)
    if manifest.get("database", {}).get("kind") == "postgres" and url:
        db = database_status(url)
        db["assumed_from_example"] = assumed
        if db["state"] == "auth_failed" and example and example != url:
            db["example_works"] = database_status(example)["state"] == "ok"
        probes["database"] = db

    if runtime and manifest.get("start_command"):
        probes["runtime"] = runtime_probe(project_path)
    return probes
