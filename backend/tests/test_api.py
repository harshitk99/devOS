import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import app
from app.services import pipeline


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(pipeline, "run_execution", lambda *a, **k: None)  # background job tested elsewhere
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_flow_import_diagnose_plan_approve_execute(client, demo_project):
    project = client.post("/api/projects", json={"path": str(demo_project)}).json()
    pid = project["id"]

    findings = client.post(f"/api/projects/{pid}/diagnose").json()
    assert [f["check"] for f in findings] == ["env_missing"]
    assert client.get(f"/api/projects/{pid}/diagnostics").json() == findings

    plan = client.post(f"/api/projects/{pid}/plans").json()
    assert plan["status"] == "proposed"
    action = plan["actions"][0]
    assert action["decision"] == "require_approval" and action["action"]["type"] == "set_env_var"

    # not approved yet -> 409, and nothing runs
    assert client.post(f"/api/plans/{plan['id']}/execute").status_code == 409

    assert client.post(f"/api/plans/{plan['id']}/approve").json()["status"] == "approved"
    execution = client.post(f"/api/plans/{plan['id']}/execute")
    assert execution.status_code == 202
    assert client.get(f"/api/executions/{execution.json()['id']}").json()["plan_id"] == plan["id"]

    events = client.get(f"/api/projects/{pid}/audit").json()
    assert [e["type"] for e in events] == [
        "project_imported", "diagnosis_run", "plan_generated", "plan_approved", "execution_started",
    ]


def test_findings_are_ranked_by_priority(client, demo_project):
    (demo_project / ".env.example").write_text("PORT=3100\nDATABASE_URL=postgresql://x\nAPI_KEY=abc\n")
    (demo_project / ".env").write_text("")
    pid = client.post("/api/projects", json={"path": str(demo_project)}).json()["id"]
    findings = client.post(f"/api/projects/{pid}/diagnose").json()
    assert [f["rank"] for f in findings] == [1, 2, 3]
    priorities = [f["priority"] for f in findings]
    assert priorities == sorted(priorities, reverse=True) and priorities[0] == 2.85  # high(3) x 0.95
    assert client.get(f"/api/projects/{pid}/diagnostics").json() == findings


def test_reimporting_a_path_reuses_the_project(client, demo_project):
    first = client.post("/api/projects", json={"path": str(demo_project)}).json()
    second = client.post("/api/projects", json={"path": str(demo_project)}).json()
    assert first["id"] == second["id"]
    assert [p["id"] for p in client.get("/api/projects").json()].count(first["id"]) == 1


def test_project_list_shows_one_entry_per_folder_even_with_legacy_duplicates(client, db_session, demo_project):
    from app.models import Project

    first = client.post("/api/projects", json={"path": str(demo_project)}).json()
    legacy = Project(name="old", path=first["path"], manifest={})  # a duplicate row from before dedupe
    db_session.add(legacy)
    db_session.flush()
    listed = [p for p in client.get("/api/projects").json() if p["path"] == first["path"]]
    assert len(listed) == 1
    # importing again attaches to the newest row, the same one the list shows
    assert client.post("/api/projects", json={"path": str(demo_project)}).json()["id"] == listed[0]["id"]


def test_file_endpoints_and_plan_preview(client, demo_project):
    pid = client.post("/api/projects", json={"path": str(demo_project)}).json()["id"]
    tree = client.get(f"/api/projects/{pid}/files").json()
    assert ".env.example" in [f["path"] for f in tree["files"]]

    masked = client.get(f"/api/projects/{pid}/files/content", params={"path": ".env"}).json()
    assert masked["masked"] and "3100" not in masked["content"]
    shown = client.get(f"/api/projects/{pid}/files/content", params={"path": ".env", "reveal": True}).json()
    assert "PORT=3100" in shown["content"]

    assert client.get(f"/api/projects/{pid}/files/content", params={"path": "../x"}).status_code == 400
    assert client.get(f"/api/projects/{pid}/files/content", params={"path": "nope.js"}).status_code == 404

    (demo_project / "notes.txt").write_text("old")
    saved = client.put(f"/api/projects/{pid}/files/content", json={"path": "notes.txt", "content": "edited"})
    assert saved.status_code == 200 and saved.json()["content"] == "edited"
    assert (demo_project / "notes.txt").read_text() == "edited"
    events = client.get(f"/api/projects/{pid}/audit").json()
    assert any(e["type"] == "file_edited" and e["payload"]["path"] == "notes.txt" for e in events)

    refused = client.put(f"/api/projects/{pid}/files/content", json={"path": ".env", "content": "PORT=1\n"})
    assert refused.status_code == 400
    missing = client.put(f"/api/projects/{pid}/files/content", json={"path": "nope.js", "content": "x"})
    assert missing.status_code == 404

    client.post(f"/api/projects/{pid}/diagnose")
    plan = client.post(f"/api/projects/{pid}/plans").json()
    [item] = client.get(f"/api/plans/{plan['id']}/preview").json()
    assert item["path"] == ".env"
    assert [d["op"] for d in item["diff"] if d["op"] != "same"] == ["add"]
    assert client.get(f"/api/plans/{plan['id']}/executions").json() == []


def test_import_rejects_bad_path(client, tmp_path):
    assert client.post("/api/projects", json={"path": str(tmp_path / "missing")}).status_code == 400


def test_unknown_ids_are_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/projects/{missing}").status_code == 404
    assert client.post(f"/api/plans/{missing}/approve").status_code == 404


def test_healthy_project_has_no_plan(client, demo_project):
    (demo_project / ".env").write_text("PORT=3100\nDATABASE_URL=postgresql://x\n")
    pid = client.post("/api/projects", json={"path": str(demo_project)}).json()["id"]
    client.post(f"/api/projects/{pid}/diagnose")
    assert client.post(f"/api/projects/{pid}/plans").status_code == 409


def test_findings_say_whether_an_automatic_fix_exists(client, demo_project):
    (demo_project / ".env.example").write_text("PORT=3100\nDATABASE_URL=\n")  # no value to fall back on
    (demo_project / ".env").write_text("")
    pid = client.post("/api/projects", json={"path": str(demo_project)}).json()["id"]
    by_key = {f["evidence"]["key"]: f["fix_available"] for f in client.post(f"/api/projects/{pid}/diagnose").json()}
    assert by_key == {"PORT": True, "DATABASE_URL": False}


def test_plan_preview_carries_summaries_and_warnings(client, demo_project):
    pid = client.post("/api/projects", json={"path": str(demo_project)}).json()["id"]
    client.post(f"/api/projects/{pid}/diagnose")
    plan = client.post(f"/api/projects/{pid}/plans").json()
    [item] = client.get(f"/api/plans/{plan['id']}/preview").json()
    assert item["summary"] == "Set DATABASE_URL in .env" and item["needs_network"] is False and item["warning"] is None
