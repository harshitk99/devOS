"""Process/Port Monitor: live process, port and container state via psutil and the Docker API.

Read-only. Feeds the UI's Live tab and helps a human see the same facts the Doctor's probes use
(which program holds a port, which containers are up). It never stops, signals or changes anything."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
import psutil
from docker.errors import DockerException

_DEV_PROCESSES = ("node", "python", "java", "postgres", "docker", "com.docker", "vite", "uvicorn", "npm", "deno", "bun", "ruby", "php")
_LABEL_DIR = "com.docker.compose.project.working_dir"
_LABEL_PROJECT = "com.docker.compose.project"
_LABEL_SERVICE = "com.docker.compose.service"


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _inside(child: str | None, parent: str) -> bool:
    if not child:
        return False
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p + os.sep)


def _owner(cwd: str | None, projects: dict[str, str]) -> str | None:
    """Name of the imported project a path belongs to, if any."""
    return next((name for path, name in projects.items() if _inside(cwd, path)), None)


def _process_info(pid: int) -> tuple[str | None, str | None, list[str]]:
    try:
        proc = psutil.Process(pid)
        try:
            cwd = proc.cwd()
        except (psutil.Error, OSError):
            cwd = None
        try:
            cmd = proc.cmdline()
        except (psutil.Error, OSError):
            cmd = []
        return proc.name(), cwd, cmd
    except (psutil.Error, OSError):
        return None, None, []


def listening_ports(projects: dict[str, str], project_ports: dict[int, str]) -> list[dict[str, Any]]:
    """Every listening TCP port once (IPv4 and IPv6 listeners merged), with the program holding it."""
    seen: dict[tuple[int, int | None], dict[str, Any]] = {}
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return []
    for conn in connections:
        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
            continue
        port, pid = conn.laddr.port, conn.pid
        if (port, pid) in seen:
            continue
        name, cwd, _ = _process_info(pid) if pid else (None, None, [])
        owner = _owner(cwd, projects) or project_ports.get(port)
        relevance = "project" if owner else ("dev" if name and name.lower().startswith(_DEV_PROCESSES) else "system")
        seen[(port, pid)] = {
            "port": port, "address": "all interfaces" if conn.laddr.ip in ("::", "0.0.0.0") else conn.laddr.ip,
            "pid": pid, "process": name, "relevance": relevance, "project": owner,
        }
    return sorted(seen.values(), key=lambda p: ({"project": 0, "dev": 1, "system": 2}[p["relevance"]], p["port"]))


def project_processes(projects: dict[str, str]) -> list[dict[str, Any]]:
    """Dev runtimes (node, python, ...) whose working folder is inside an imported project: its dev
    servers and the like. Editors and shells are left out (noise), and commands are cut short because
    a command line can carry secrets."""
    found = []
    for proc in psutil.process_iter(["pid", "name"]):
        if not (proc.info["name"] or "").lower().startswith(_DEV_PROCESSES):
            continue
        try:
            cwd = proc.cwd()
        except (psutil.Error, OSError):
            continue
        if owner := _owner(cwd, projects):
            try:
                cmd = " ".join(proc.cmdline())[:100]
            except (psutil.Error, OSError):
                cmd = ""
            found.append({"pid": proc.info["pid"], "name": proc.info["name"], "command": cmd, "project": owner})
    return sorted(found, key=lambda p: p["pid"])


def containers(projects: dict[str, str]) -> dict[str, Any]:
    try:
        client = docker.from_env(timeout=5)
        rows = []
        for c in client.containers.list(all=True):
            labels = c.labels or {}
            ports = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
            published = sorted({f"{b['HostPort']}->{inner}" for inner, binds in ports.items() for b in (binds or [])})
            rows.append({
                "name": c.name, "image": (c.image.tags[0] if c.image.tags else c.attrs.get("Config", {}).get("Image", "?")),
                "status": c.status, "ports": published, "service": labels.get(_LABEL_SERVICE),
                "compose_project": labels.get(_LABEL_PROJECT), "project": _owner(labels.get(_LABEL_DIR), projects),
            })
        rows.sort(key=lambda r: (r["project"] is None, r["status"] != "running", r["name"]))
        return {"available": True, "error": None, "items": rows}
    except (DockerException, OSError) as e:
        return {"available": False, "error": str(e), "items": []}


def snapshot(projects: dict[str, str] | None = None, project_ports: dict[int, str] | None = None) -> dict[str, Any]:
    """`projects` maps an imported project's folder to its name; `project_ports` maps a port an
    imported project is configured to use to its name (so a squatter on it is attributed)."""
    projects = projects or {}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ports": listening_ports(projects, project_ports or {}),
        "project_processes": project_processes(projects),
        "docker": containers(projects),
    }
