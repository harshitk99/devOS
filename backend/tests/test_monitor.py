import os
import socket
from pathlib import Path

import pytest
from docker.errors import DockerException
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import app
from app.services import monitor

pytestmark = pytest.mark.real_probes  # these look at the real machine


def _listen():
    s = socket.socket(socket.AF_INET6)
    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    s.bind(("::", 0))
    s.listen()
    return s, s.getsockname()[1]


def _mine(ports, port):
    return next((p for p in ports if p["port"] == port and p["pid"] == os.getpid()), None)


def test_a_listening_port_is_reported_once_with_its_owner():
    s, port = _listen()
    try:
        entry = _mine(monitor.listening_ports({}, {}), port)
        assert entry and entry["process"] and entry["address"] == "all interfaces"
        assert sum(1 for p in monitor.listening_ports({}, {}) if p["port"] == port) == 1  # v4+v6 listeners merged
    finally:
        s.close()


def test_ports_are_attributed_to_the_project_that_owns_the_folder_or_the_port():
    s, port = _listen()
    try:
        by_folder = _mine(monitor.listening_ports({str(Path.cwd()): "my-project"}, {}), port)
        assert by_folder["relevance"] == "project" and by_folder["project"] == "my-project"
        by_port = _mine(monitor.listening_ports({}, {port: "configured-project"}), port)
        assert by_port["project"] == "configured-project"
        unrelated = _mine(monitor.listening_ports({r"Z:\nowhere": "x"}, {}), port)
        assert unrelated["project"] is None and unrelated["relevance"] in ("dev", "system")
    finally:
        s.close()


def test_project_ports_sort_first():
    s, port = _listen()
    try:
        ports = monitor.listening_ports({str(Path.cwd()): "p"}, {})
        assert ports[0]["relevance"] == "project"
        assert [p["relevance"] for p in ports] == sorted((p["relevance"] for p in ports), key={"project": 0, "dev": 1, "system": 2}.get)
    finally:
        s.close()


def test_processes_running_inside_a_project_folder_are_listed():
    mine = [p for p in monitor.project_processes({str(Path.cwd()): "p"}) if p["pid"] == os.getpid()]
    assert mine and mine[0]["project"] == "p"
    assert not [p for p in monitor.project_processes({r"Z:\nowhere": "x"}) if p["pid"] == os.getpid()]


def test_path_matching_is_by_folder_not_by_name_prefix(tmp_path):
    assert monitor._inside(str(tmp_path / "app" / "src"), str(tmp_path / "app"))
    assert not monitor._inside(str(tmp_path / "app-two"), str(tmp_path / "app"))  # "app" is not a prefix of "app-two"
    assert not monitor._inside(None, str(tmp_path))


def test_docker_being_down_is_reported_not_raised(monkeypatch):
    def down(**kw):
        raise DockerException("daemon not running")

    monkeypatch.setattr(monitor.docker, "from_env", down)
    result = monitor.containers({})
    assert result["available"] is False and "daemon not running" in result["error"] and result["items"] == []


def test_the_snapshot_has_every_section():
    snap = monitor.snapshot()
    assert set(snap) == {"generated_at", "ports", "project_processes", "docker"}
    assert isinstance(snap["ports"], list) and "available" in snap["docker"]


def test_api_uses_imported_projects_to_attribute_things(db_session, demo_project):
    from app.services import pipeline

    project = pipeline.import_project(db_session, str(demo_project))
    s, port = _listen()
    try:
        project.manifest = {**project.manifest, "config": {"port": port, "source": ".env"}}
        db_session.commit()
        app.dependency_overrides[get_session] = lambda: db_session
        body = TestClient(app).get("/api/monitor").json()
        entry = next(p for p in body["ports"] if p["port"] == port and p["pid"] == os.getpid())
        assert entry["project"] == project.name and entry["relevance"] == "project"
    finally:
        app.dependency_overrides.clear()
        s.close()
