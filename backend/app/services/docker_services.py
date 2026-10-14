"""Start a stopped service (e.g. the project's Postgres) through the Docker API.

Deliberately narrow: it only starts containers that ALREADY EXIST and were created by the
project's own compose file (matched on Docker's compose labels: service name + the folder the
compose file lives in). It cannot create containers, pull images, run commands, stop anything, or
touch containers belonging to any other project."""

import os
import re
import time
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException

_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_LABEL_SERVICE = "com.docker.compose.service"
_LABEL_DIR = "com.docker.compose.project.working_dir"


class ServiceError(RuntimeError):
    pass


def _same_dir(label: str | None, root: Path) -> bool:
    if not label:
        return False
    norm = lambda p: os.path.normcase(os.path.normpath(str(p)))  # noqa: E731
    return norm(label) == norm(root)


def find_containers(client: docker.DockerClient, project_path: str, service: str) -> list[Any]:
    root = Path(project_path).resolve()
    return [
        c
        for c in client.containers.list(all=True, filters={"label": f"{_LABEL_SERVICE}={service}"})
        if _same_dir(c.labels.get(_LABEL_DIR), root)
    ]


def start_service(project_path: str, service: str, wait_s: int = 30) -> dict[str, Any]:
    if not isinstance(service, str) or not _SERVICE.match(service):
        raise ServiceError(f"invalid service name: {service!r}")
    try:
        client = docker.from_env()
        containers = find_containers(client, project_path, service)
        if not containers:
            raise ServiceError(
                f"no container for service '{service}' exists from this project's compose file; "
                f"create it once with `docker compose up -d {service}`"
            )
        for container in containers:
            if container.status != "running":
                container.start()
        deadline = time.time() + wait_s
        while time.time() < deadline:
            for container in containers:
                container.reload()
            if all(c.status == "running" and c.attrs["State"].get("Health", {}).get("Status", "healthy") == "healthy" for c in containers):
                break
            time.sleep(1)
        else:
            raise ServiceError(f"service '{service}' did not become healthy within {wait_s}s")
    except DockerException as e:
        raise ServiceError(f"docker unavailable: {e}") from e
    names = ", ".join(c.name for c in containers)
    return {"ok": True, "message": f"started service '{service}' ({names})"}
