"""Run DevOS end to end against the demo backend, for each kind of problem, through the real API
(the same calls the web UI makes) and check the outcome, including that the app really works after.

    python scripts/run_scenarios.py                 # all scenarios
    python scripts/run_scenarios.py db-down combo   # just these

Needs the backend on http://localhost:8000, Docker running, and the demo Postgres (inject_failure.py
starts it). Fixing missing packages downloads them, so that scenario needs internet.
"""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo-app" / "backend"
API = "http://localhost:8000/api"

# scenario -> (expected findings, expected verdict or None for "no plan possible")
SCENARIOS = {
    "missing-env": ({"env_missing"}, "VERIFIED"),
    "missing-dependency": ({"dependency_missing"}, "VERIFIED"),
    "port-conflict": ({"port_in_use"}, "VERIFIED"),
    "db-down": ({"database_unreachable"}, "VERIFIED"),
    "db-auth": ({"database_auth"}, "VERIFIED"),
    "db-schema": ({"schema_missing"}, "VERIFIED"),
    "combo": ({"env_missing", "dependency_missing", "database_unreachable"}, "VERIFIED"),
    "crash": ({"runtime_crash"}, None),
    "unfixable": ({"env_missing"}, "FAILED"),
}


def inject(mode: str) -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "inject_failure.py"), mode], check=True, capture_output=True)


def app_answers() -> str:
    """Start the demo backend on the host as a developer would, and use it."""
    port = next((ln.split("=", 1)[1] for ln in (DEMO / ".env").read_text().splitlines() if ln.startswith("PORT=")), "3100")
    proc = subprocess.Popen(["node", "server.js"], cwd=DEMO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(20):
            time.sleep(0.4)
            try:
                with urllib.request.urlopen(f"http://localhost:{port}/api/notes", timeout=2) as r:
                    return f"http {r.status} on port {port}"
            except urllib.error.HTTPError as e:
                return f"http {e.code} on port {port}"
            except OSError:
                if proc.poll() is not None:
                    return f"app exited: {proc.stdout.read().decode(errors='replace').strip()[:120]}"
        return "no answer"
    finally:
        proc.terminate()


def run(name: str, c: httpx.Client) -> bool:
    want_findings, want_verdict = SCENARIOS[name]
    print(f"\n=== {name}")
    inject(name)
    project = c.post("/projects", json={"path": str(DEMO)}).json()
    findings = c.post(f"/projects/{project['id']}/diagnose").json()
    got = {f["check"] for f in findings}
    print(f"  diagnosed : {sorted(got)}  (ranked: {[(f['rank'], f['check']) for f in findings]})")
    ok = got == want_findings
    if not ok:
        print(f"  !! expected findings {sorted(want_findings)}")

    r = c.post(f"/projects/{project['id']}/plans")
    if want_verdict is None:
        no_plan = r.status_code == 409
        print(f"  plan      : {'none possible (diagnosis only), as expected' if no_plan else 'UNEXPECTED plan'}")
        return ok and no_plan
    if r.status_code != 201:
        print(f"  !! plan failed: {r.status_code} {r.text}")
        return False
    plan = r.json()
    for i, a in enumerate(plan["actions"], 1):
        print(f"  step {i}    : {a['action']['type']:19} {a['risk']:6} {a['decision']}")
    c.post(f"/plans/{plan['id']}/approve")
    execution = c.post(f"/plans/{plan['id']}/execute").json()
    for _ in range(300):
        time.sleep(1)
        if c.get(f"/plans/{plan['id']}").json()["status"] != "executing":
            break
    plan = c.get(f"/plans/{plan['id']}").json()
    verdicts = c.get(f"/executions/{execution['id']}/verifications").json()
    verdict = verdicts[-1]["verdict"] if verdicts else "n/a"
    log = c.get(f"/executions/{execution['id']}").json()["log"]
    notes = [e["message"] for e in log if e["message"].startswith("NOT undone")]
    print(f"  result    : plan {plan['status']}, verdict {verdict}" + (f", {len(notes)} not-undone note(s)" if notes else ""))
    if verdicts:
        r = verdicts[-1]["results"]
        print(f"  checks    : start={r.get('startup')} build={(r.get('build') or {}).get('status')} tests={(r.get('tests') or {}).get('status')} "
              f"recheck remaining={len((r.get('recheck') or {}).get('remaining', []))} other={len((r.get('recheck') or {}).get('other', []))}")
    good = verdict == want_verdict
    if want_verdict == "VERIFIED":
        answer = app_answers()
        print(f"  the app   : {answer}")
        good = good and answer.startswith("http 200")
    return ok and good


def main() -> int:
    names = sys.argv[1:] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        sys.exit(f"unknown scenario(s): {unknown}. Choose from {list(SCENARIOS)}")
    results = {}
    with httpx.Client(base_url=API, timeout=120) as c:
        for name in names:
            results[name] = run(name, c)
    inject_reset = subprocess.run([sys.executable, str(ROOT / "scripts" / "inject_failure.py"), "--reset"], capture_output=True)
    print("\n" + "-" * 50)
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("demo reset to healthy" if inject_reset.returncode == 0 else "WARNING: reset failed")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
