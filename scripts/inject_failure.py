"""Break (or repair) the demo backend so DevOS has something to diagnose.

    python scripts/inject_failure.py <mode>
    python scripts/inject_failure.py --reset       # healthy state (installs/starts/seeds what is missing)

Modes (each one is a different kind of problem):
  missing-env         DATABASE_URL absent from .env                    -> DevOS can fix
  missing-dependency  node_modules deleted                             -> DevOS can fix (needs internet once)
  port-conflict       another program is holding the app's port        -> DevOS can fix (picks a free port)
  db-down             the demo Postgres container is stopped           -> DevOS can fix (starts it)
  db-auth             .env has the wrong database password             -> DevOS can fix
  db-schema           the notes table is missing                       -> DevOS can fix (runs the migration)
  combo               missing setting + missing packages + database stopped -> one plan, three steps
  crash               the app crashes on startup for its own reasons   -> diagnosis only, no automatic fix
  unfixable           like missing-env, but the suggested value is bad -> fix fails verification, rolls back
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "demo-app" / "backend"
SQUATTER_PID = Path(tempfile.gettempdir()) / "devos_port_squatter.pid"

PORT = 3100
GOOD_URL = "postgresql://demo:demo@localhost:5433/demo"
GOOD_EXAMPLE = f"PORT={PORT}\nDATABASE_URL={GOOD_URL}\n"
BAD_EXAMPLE = f"PORT={PORT}\nDATABASE_URL=not-a-url\n"


def sh(cmd: list[str], cwd: Path = BACKEND, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check, **kw)


def npm() -> str:
    return shutil.which("npm") or sys.exit("npm not found")


def db_ready(timeout: int = 90) -> None:
    """Wait until Postgres accepts queries *and keeps doing so*. A brand-new container runs a
    temporary server for first-time setup and then restarts, so the first 'ready' can be a lie."""
    query = ["docker", "compose", "exec", "-T", "db", "psql", "-U", "demo", "-d", "demo", "-tAc", "select 1"]
    streak = 0
    for _ in range(timeout):
        streak = streak + 1 if sh(query, check=False).returncode == 0 else 0
        if streak >= 3:
            return
        time.sleep(1)
    sys.exit("demo Postgres did not become ready")


def psql(sql: str) -> None:
    sh(["docker", "compose", "exec", "-T", "db", "psql", "-U", "demo", "-d", "demo", "-v", "ON_ERROR_STOP=1", "-c", sql])


def write_env(text: str) -> None:
    (BACKEND / ".env").write_text(text)


def _bind_like_node(sock_family_module=socket):
    """Bind the way Node's listen(port) does: dual-stack wildcard (IPv6 `::` that also takes IPv4)."""
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        s.bind(("::", PORT))
    except OSError as e:
        if getattr(e, "errno", None) in (10048, 98, 48):  # address already in use
            raise
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # no IPv6 on this machine
        s.bind(("0.0.0.0", PORT))
    return s


def port_in_use() -> bool:
    try:
        _bind_like_node().close()
        return False
    except OSError:
        return True


def stop_squatter() -> None:
    if SQUATTER_PID.exists():
        pid = SQUATTER_PID.read_text().strip()
        if os.name == "nt":  # /T: a venv python is a launcher plus a child; the child holds the port
            subprocess.run(["taskkill", "/T", "/F", "/PID", pid], capture_output=True, check=False)
        else:
            subprocess.run(["kill", pid], capture_output=True, check=False)
        SQUATTER_PID.unlink(missing_ok=True)


def start_squatter() -> None:
    """A throwaway program that holds the app's port, like a forgotten old dev server."""
    stop_squatter()
    code = (
        "import socket,time\n"
        "try:\n"
        "    s=socket.socket(socket.AF_INET6); s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0); s.bind(('::',%d))\n"
        "except OSError:\n"
        "    s=socket.socket(); s.bind(('0.0.0.0',%d))\n"
        "s.listen(); time.sleep(7200)\n" % (PORT, PORT)
    )
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    proc = subprocess.Popen([sys.executable, "-c", code], creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    SQUATTER_PID.write_text(str(proc.pid))
    for _ in range(50):  # wait until the port is genuinely held
        if port_in_use():
            return
        time.sleep(0.2)
    sys.exit(f"could not start a program holding port {PORT}")


def reset() -> None:
    stop_squatter()
    (BACKEND / ".env.example").write_text(GOOD_EXAMPLE)
    write_env(GOOD_EXAMPLE)
    if not (BACKEND / "node_modules" / "express").exists():
        print("installing dependencies...")
        sh([npm(), "install"])
    sh(["docker", "compose", "up", "-d", "db"])
    db_ready()
    sh(["docker", "compose", "exec", "-T", "db", "psql", "-U", "demo", "-d", "demo", "-v", "ON_ERROR_STOP=1", "-f", "-"],
       input=(BACKEND / "db" / "schema.sql").read_text())
    print("demo backend reset to a healthy state (dependencies, database, schema, .env, port)")


def inject(mode: str) -> None:
    # Start from healthy so each mode breaks exactly one thing.
    reset()
    if mode == "missing-env":
        write_env(f"PORT={PORT}\n")
    elif mode == "unfixable":
        (BACKEND / ".env.example").write_text(BAD_EXAMPLE)
        write_env(f"PORT={PORT}\n")
    elif mode == "missing-dependency":
        shutil.rmtree(BACKEND / "node_modules")
    elif mode == "port-conflict":
        start_squatter()
    elif mode == "db-down":
        sh(["docker", "compose", "stop", "db"])
    elif mode == "db-auth":
        write_env(f"PORT={PORT}\nDATABASE_URL=postgresql://demo:wrong-password@localhost:5433/demo\n")
    elif mode == "db-schema":
        psql("DROP TABLE IF EXISTS notes")
    elif mode == "combo":
        write_env(f"PORT={PORT}\n")
        shutil.rmtree(BACKEND / "node_modules")
        sh(["docker", "compose", "stop", "db"])
    elif mode == "crash":
        write_env(GOOD_EXAMPLE + "FORCE_CRASH=1\n")
    print(f"injected '{mode}'")


MODES = ["missing-env", "missing-dependency", "port-conflict", "db-down", "db-auth", "db-schema", "combo", "crash", "unfixable"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("mode", nargs="?", choices=MODES)
    parser.add_argument("--reset", action="store_true", help="restore the healthy state")
    args = parser.parse_args()
    if args.reset:
        reset()
    elif args.mode:
        inject(args.mode)
    else:
        parser.error("give a failure mode or --reset")


if __name__ == "__main__":
    main()
