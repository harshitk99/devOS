"""Thin REST client. The CLI adds no logic of its own to the pipeline: every state change goes
through the same endpoints (and the same policy engine and approval gate) as the web UI."""

import os
from typing import Any

import httpx

DEFAULT_API = "http://localhost:8000/api"


class DevosError(Exception):
    """Something the user can act on; printed as one clean line, never a traceback."""


class Api:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or os.environ.get("DEVOS_API", DEFAULT_API)
        self._client = httpx.Client(base_url=self.base_url, timeout=120)

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise DevosError(
                f"can't reach the DevOS backend at {self.base_url}.\n"
                "  Start it with:  python scripts/dev.py"
            ) from e
        except httpx.HTTPError as e:
            raise DevosError(f"request to the DevOS backend failed: {e}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise DevosError(f"{detail}" if isinstance(detail, str) else f"{r.status_code}: {detail}")
        return r.json()

    # projects and diagnosis
    def import_project(self, path: str) -> dict:
        return self._call("POST", "/projects", json={"path": path})

    def diagnose(self, project_id: str) -> list[dict]:
        return self._call("POST", f"/projects/{project_id}/diagnose")

    def audit(self, project_id: str) -> list[dict]:
        return self._call("GET", f"/projects/{project_id}/audit")

    # plans
    def generate_plan(self, project_id: str) -> dict:
        return self._call("POST", f"/projects/{project_id}/plans")

    def preview(self, plan_id: str) -> list[dict]:
        return self._call("GET", f"/plans/{plan_id}/preview")

    def plan(self, plan_id: str) -> dict:
        return self._call("GET", f"/plans/{plan_id}")

    def approve(self, plan_id: str) -> dict:
        return self._call("POST", f"/plans/{plan_id}/approve")

    def reject(self, plan_id: str) -> dict:
        return self._call("POST", f"/plans/{plan_id}/reject")

    # execution and verification
    def execute(self, plan_id: str) -> dict:
        return self._call("POST", f"/plans/{plan_id}/execute")

    def execution(self, execution_id: str) -> dict:
        return self._call("GET", f"/executions/{execution_id}")

    def verifications(self, execution_id: str) -> list[dict]:
        return self._call("GET", f"/executions/{execution_id}/verifications")
