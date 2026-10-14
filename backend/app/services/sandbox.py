"""Docker Sandbox: runs only approved, policy-classified actions, isolated, with the bounded
limits from core.config.SandboxLimits (CPU, memory, timeout, filesystem, network).

The container image (sandbox/Dockerfile) contains a fixed dispatcher (sandbox/runner.js). We pass
it a typed request as JSON; no caller-supplied command line is ever executed."""

import hashlib
import json
from pathlib import Path
from typing import Any

import docker
import requests
from docker.errors import DockerException, ImageNotFound

from app.core.config import REPO_DIR, SandboxLimits, settings
from app.domain.actions import ClassifiedAction
from app.domain.enums import ActionType
from app.services import approval

IMAGE_NAME = "devos-sandbox"
SANDBOX_CONTEXT = REPO_DIR / "sandbox"
IMAGE_FILES = ("Dockerfile", "runner.js")  # what is baked into the image
RESULT_MARKER = "DEVOS_RESULT:"


# Everything runs with network=none EXCEPT these approved actions, which cannot work without it:
# installing packages needs the package registry, and a migration needs to reach the database.
# Both are REQUIRE_APPROVAL in the policy engine. Installs also get room for npm's cache.
NETWORK_ACTIONS = {ActionType.INSTALL_DEPENDENCY: "bridge", ActionType.RUN_MIGRATION: "bridge"}
BIG_SCRATCH = {ActionType.INSTALL_DEPENDENCY}


class SandboxError(RuntimeError):
    """The sandbox could not run the request (Docker down, timeout, bad output...)."""


def image_tag() -> str:
    """Tag derived from the image's contents, so editing runner.js or the Dockerfile builds a new
    image instead of silently running a stale one."""
    digest = hashlib.sha256()
    for name in IMAGE_FILES:
        digest.update(name.encode())
        digest.update((SANDBOX_CONTEXT / name).read_bytes())
    return f"{IMAGE_NAME}:{digest.hexdigest()[:12]}"


def _ensure_image(client: docker.DockerClient) -> str:
    tag = image_tag()
    try:
        client.images.get(tag)
    except ImageNotFound:
        client.images.build(path=str(SANDBOX_CONTEXT), tag=tag, rm=True)
    return tag


def _parse_result(logs: str) -> dict[str, Any]:
    for line in reversed(logs.splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER):])
    raise SandboxError(f"sandbox produced no result. Output: {logs[-500:]!r}")


def _execute(
    project_path: str,
    payload: dict[str, Any],
    limits: SandboxLimits,
    network: str | None = None,
    scratch_mb: int = 64,
) -> dict[str, Any]:
    root = Path(project_path).resolve()
    if not root.is_dir():
        raise SandboxError(f"project path is not a directory: {root}")

    try:
        client = docker.from_env()
        tag = _ensure_image(client)
        container = client.containers.run(
            tag,
            detach=True,
            environment={"DEVOS_PAYLOAD": json.dumps(payload)},
            volumes={str(root): {"bind": "/work", "mode": "rw"}},
            network_mode=network or limits.network,
            extra_hosts={"host.docker.internal": "host-gateway"} if network else None,
            nano_cpus=int(limits.cpus * 1_000_000_000),
            mem_limit=f"{limits.memory_mb}m",
            memswap_limit=f"{limits.memory_mb}m",  # no swap
            read_only=limits.read_only_root,
            tmpfs={"/tmp": f"rw,size={scratch_mb}m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=256 if scratch_mb > 64 else 128,
        )
    except DockerException as e:
        raise SandboxError(f"docker unavailable: {e}") from e

    try:
        try:
            container.wait(timeout=limits.timeout_s)
        except requests.exceptions.RequestException as e:  # docker-py surfaces timeouts this way
            container.kill()
            raise SandboxError(f"sandbox timed out after {limits.timeout_s}s") from e
        logs = container.logs().decode("utf-8", errors="replace")
    finally:
        container.remove(force=True)

    return {**_parse_result(logs), "logs": logs}


def run(
    action: ClassifiedAction,
    project_path: str,
    approved: bool = False,
    limits: SandboxLimits = settings.sandbox,
) -> dict[str, Any]:
    """Run one classified action. Refuses BLOCKed actions and unapproved REQUIRE_APPROVAL ones
    even if the caller forgot to check."""
    if not approval.is_executable([action], approved):
        raise PermissionError(f"action {action.action.type} is not permitted to run ({action.decision})")
    payload = {
        "mode": "action",
        "action": {"type": action.action.type.value, "params": action.action.params},
    }
    kind = action.action.type
    return _execute(
        project_path,
        payload,
        limits,
        network=NETWORK_ACTIONS.get(kind),
        scratch_mb=512 if kind in BIG_SCRATCH else 64,
    )


def run_probe(project_path: str, limits: SandboxLimits = settings.sandbox) -> dict[str, Any]:
    """Only start the project's own `npm start` in the sandbox and probe /health (loopback only).
    Used during diagnosis to see whether the app crashes on start."""
    return _execute(project_path, {"mode": "probe"}, limits)


def run_verify(project_path: str, limits: SandboxLimits = settings.sandbox) -> dict[str, Any]:
    """Full verification run in the sandbox (startup + health, and build/tests when present)."""
    return _execute(project_path, {"mode": "verify"}, limits)
