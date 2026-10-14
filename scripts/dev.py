"""Start everything DevOS needs with one command, from the repo root:

    python scripts/dev.py            (or: npm run dev)

Starts Postgres (Docker), the FastAPI backend and the Vite UI, prefixes each service's output,
and stops the backend and UI on Ctrl+C. Postgres is left running; add --stop-db to stop it too.
First run also creates backend/.venv, installs dependencies and copies .env.example to .env.
"""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"
IS_WIN = os.name == "nt"
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")

API_PORT, UI_PORT = 8000, 5173


def say(msg: str) -> None:
    print(f"[dev] {msg}", flush=True)


def run(cmd: list[str], cwd: Path = ROOT, quiet: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=quiet, text=True, check=False)


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def fail(msg: str) -> None:
    sys.exit(f"[dev] ERROR: {msg}")


def prepare() -> None:
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
        say("created .env from .env.example")

    if not VENV_PY.exists():
        say("first run: creating backend/.venv and installing dependencies (a few minutes)...")
        if run([sys.executable, "-m", "venv", str(BACKEND / ".venv")]).returncode:
            fail("could not create the virtualenv")
        if run([str(VENV_PY), "-m", "pip", "install", "-q", "-e", ".[dev]"], BACKEND).returncode:
            fail("pip install failed")

    npm = shutil.which("npm")
    if not npm:
        fail("npm not found; install Node.js 20+")
    if not (FRONTEND / "node_modules").exists():
        say("first run: npm install in frontend/ ...")
        if run([npm, "install"], FRONTEND).returncode:
            fail("npm install failed")

    if run(["docker", "info"], quiet=True).returncode:
        fail("Docker is not running. Start Docker Desktop and try again.")


def start_database() -> None:
    say("starting Postgres (docker compose up -d db)...")
    if run(["docker", "compose", "up", "-d", "db"], quiet=True).returncode:
        fail("docker compose up failed; run `docker compose up -d db` to see why")
    # A brand-new container runs a temporary server for first-time setup, then restarts: the first
    # "ready" can be a lie. Require several consecutive successes before trusting it.
    streak = 0
    for _ in range(90):
        ready = run(["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "devos", "-d", "devos"], quiet=True)
        streak = streak + 1 if ready.returncode == 0 else 0
        if streak >= 3:
            say("Postgres is ready")
            return
        time.sleep(1)
    fail("Postgres did not become ready within 90s")


def pump(name: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout:  # type: ignore[union-attr]
        print(f"[{name}] {line.rstrip()}", flush=True)


def spawn(name: str, cmd: list[str], cwd: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    threading.Thread(target=pump, args=(name, proc), daemon=True).start()
    return proc


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if IS_WIN:  # uvicorn --reload and npm both spawn children
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.terminate()


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Postgres, the DevOS backend and the UI.")
    parser.add_argument("--stop-db", action="store_true", help="also stop Postgres on exit")
    args = parser.parse_args()

    # Child tools print non-ASCII (Vite's arrow, colours); the Windows console codepage can't encode
    # it, and a crashed output pump would eventually stall the child on a full pipe.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")

    for port, what in ((API_PORT, "backend"), (UI_PORT, "UI")):
        if port_in_use(port):
            fail(f"port {port} is already in use (is the {what} already running?)")

    # Make termination signals behave like Ctrl+C so the cleanup below always runs.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    if IS_WIN:
        signal.signal(signal.SIGBREAK, signal.default_int_handler)

    prepare()
    start_database()

    procs = {
        "api": spawn("api", [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--reload", "--port", str(API_PORT)], BACKEND),
        "ui ": spawn("ui ", [shutil.which("npm"), "run", "dev"], FRONTEND),
    }
    try:
        announced = False
        while True:
            for name, proc in procs.items():
                if proc.poll() is not None:
                    fail(f"{name.strip()} exited with code {proc.returncode}; stopping the rest")
            if not announced and healthy(f"http://localhost:{API_PORT}/health") and healthy(f"http://localhost:{UI_PORT}/"):
                say(f"READY  ->  UI: http://localhost:{UI_PORT}   API docs: http://localhost:{API_PORT}/docs   (Ctrl+C to stop)")
                announced = True
            time.sleep(1)
    except KeyboardInterrupt:
        say("stopping backend and UI...")
    finally:
        for proc in procs.values():
            kill_tree(proc)
        if args.stop_db:
            run(["docker", "compose", "stop", "db"], quiet=True)
            say("Postgres stopped")
        else:
            say("Postgres left running (use --stop-db to stop it)")


if __name__ == "__main__":
    main()
