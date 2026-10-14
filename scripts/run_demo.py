"""Drive the whole DevOS loop against a running backend (http://localhost:8000), the same calls
the web UI makes:

    python scripts/inject_failure.py missing-env && python scripts/run_demo.py
    python scripts/inject_failure.py unfixable   && python scripts/run_demo.py   # -> rollback

Needs `httpx` (installed with the backend's dev extras).
"""

import sys
import time
from pathlib import Path

import httpx

API = "http://localhost:8000/api"
DEMO = Path(__file__).resolve().parent.parent / "demo-app" / "backend"


def main() -> int:
    with httpx.Client(base_url=API, timeout=60) as c:
        def call(method: str, url: str, **kw):
            r = c.request(method, url, **kw)
            if r.status_code >= 400:
                sys.exit(f"{method} {url} -> {r.status_code}: {r.text}")
            return r.json()

        before = (DEMO / ".env").read_text()
        project = call("POST", "/projects", json={"path": str(DEMO)})
        print(f"[understand] imported {project['name']} ({project['id']})")

        findings = call("POST", f"/projects/{project['id']}/diagnose")
        for f in findings:
            print(f"[diagnose]  {f['check']} ({f['severity']}, {f['confidence']:.0%}): {f['root_cause']}")
        if not findings:
            print("nothing to fix")
            return 0

        plan = call("POST", f"/projects/{project['id']}/plans")
        for a in plan["actions"]:
            print(f"[protect]   {a['action']['type']} risk={a['risk']} decision={a['decision']} {a['action']['params']}")

        r = c.post(f"/plans/{plan['id']}/execute")
        print(f"[protect]   execute before approval -> HTTP {r.status_code} (must be 409)")
        call("POST", f"/plans/{plan['id']}/approve")
        print("[protect]   approved")

        execution = call("POST", f"/plans/{plan['id']}/execute")
        seen = 0
        while True:
            state = call("GET", f"/executions/{execution['id']}")
            for entry in state["log"][seen:]:
                print(f"[{entry['stage']:<9}] {entry['message']}")
            seen = len(state["log"])
            if call("GET", f"/plans/{plan['id']}")["status"] != "executing":
                break
            time.sleep(1)

        plan = call("GET", f"/plans/{plan['id']}")
        verdicts = call("GET", f"/executions/{execution['id']}/verifications")
        print(f"\nplan status : {plan['status']}")
        print(f"verdict     : {verdicts[-1]['verdict'] if verdicts else 'n/a'}")
        print(f".env changed : {(DEMO / '.env').read_text() != before}  (False after a rollback)")
        print("audit trail : " + " -> ".join(e["type"] for e in call("GET", f"/projects/{project['id']}/audit")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
