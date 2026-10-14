"""Probes against the real machine: real sockets and the real Postgres that DevOS itself uses."""

import socket

import psycopg
import pytest

from app.core.config import settings
from app.services import probes

pytestmark = pytest.mark.real_probes


def _hold(port: int = 0):
    """Listen the way Node does (dual-stack) and return (socket, port)."""
    s = socket.socket(socket.AF_INET6)
    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    s.bind(("::", port))
    s.listen()
    return s, s.getsockname()[1]


# --- ports ------------------------------------------------------------------------------------


def test_a_held_port_is_reported_in_use_with_a_free_suggestion(tmp_path):
    s, port = _hold()
    try:
        status = probes.port_status(port, tmp_path)
        assert status["in_use"] is True
        assert status["holder"] and status["holder"]["pid"]  # this very process holds it
        assert status["holder"]["same_project"] is False
        suggestion = status["suggested_port"]
        assert suggestion and suggestion != port and probes._can_bind(suggestion)
    finally:
        s.close()


def test_a_free_port_is_reported_free():
    s, port = _hold()
    s.close()
    status = probes.port_status(port)
    assert status["in_use"] is False and status["holder"] is None and status["suggested_port"] is None


def test_find_free_port_skips_busy_ones():
    s, port = _hold()
    try:
        found = probes.find_free_port(port)
        assert found is not None and found != port
    finally:
        s.close()


# --- database ---------------------------------------------------------------------------------


@pytest.fixture
def devos_db_url():
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.Error:
        pytest.skip("DevOS's Postgres is not running")
    return url


def test_reachable_database_lists_tables_and_hides_the_password(devos_db_url):
    status = probes.database_status(devos_db_url)
    assert status["state"] == "ok" and "projects" in status["tables"]
    assert "devos:devos@" not in repr(status) and status["user"] == "devos"


def test_wrong_password_is_auth_failed_and_scrubbed(devos_db_url):
    bad = devos_db_url.replace("devos:devos@", "devos:not-the-password@")
    status = probes.database_status(bad)
    assert status["state"] == "auth_failed"
    assert "not-the-password" not in repr(status)


def test_wrong_database_name(devos_db_url):
    assert probes.database_status(devos_db_url.rsplit("/", 1)[0] + "/no_such_db")["state"] == "missing_database"


def test_nothing_listening_is_unreachable():
    s, port = _hold()
    s.close()
    assert probes.database_status(f"postgresql://u:p@127.0.0.1:{port}/db", timeout=1)["state"] == "unreachable"


def test_garbage_url_is_an_error_not_a_crash():
    assert probes.database_status("not a url at all")["state"] == "error"


# --- collect ----------------------------------------------------------------------------------


def test_collect_gathers_port_and_database_and_can_skip_the_runtime(tmp_path, devos_db_url):
    (tmp_path / ".env").write_text(f"DATABASE_URL={devos_db_url}\n")
    manifest = {"config": {"port": 0}, "database": {"kind": "postgres"}, "env": {"example_values": {}}, "start_command": "npm start"}
    out = probes.collect(str(tmp_path), manifest, runtime=False)
    assert out["database"]["state"] == "ok" and "runtime" not in out and "port" not in out  # port 0 is falsy: skipped


def test_collect_records_whether_the_example_password_would_work(tmp_path, devos_db_url):
    (tmp_path / ".env").write_text(f"DATABASE_URL={devos_db_url.replace('devos:devos@', 'devos:bad@')}\n")
    manifest = {"config": {}, "database": {"kind": "postgres"}, "env": {"example_values": {"DATABASE_URL": devos_db_url}}}
    db = probes.collect(str(tmp_path), manifest, runtime=False)["database"]
    assert db["state"] == "auth_failed" and db["example_works"] is True


def test_a_missing_database_setting_does_not_hide_a_database_problem(tmp_path, devos_db_url):
    """.env has no DATABASE_URL, so the database is probed through the .env.example value instead."""
    (tmp_path / ".env").write_text("PORT=3100\n")
    manifest = {"config": {}, "database": {"kind": "postgres"}, "env": {"example_values": {"DATABASE_URL": devos_db_url}}}
    db = probes.collect(str(tmp_path), manifest, runtime=False)["database"]
    assert db["state"] == "ok" and db["assumed_from_example"] is True


def test_a_real_setting_is_used_as_is_and_not_marked_assumed(tmp_path, devos_db_url):
    (tmp_path / ".env").write_text(f"DATABASE_URL={devos_db_url}\n")
    manifest = {"config": {}, "database": {"kind": "postgres"}, "env": {"example_values": {"DATABASE_URL": "postgresql://x@nowhere/db"}}}
    assert probes.collect(str(tmp_path), manifest, runtime=False)["database"]["assumed_from_example"] is False


def test_no_url_anywhere_means_no_database_probe(tmp_path):
    (tmp_path / ".env").write_text("")
    manifest = {"config": {}, "database": {"kind": "postgres"}, "env": {"example_values": {}}}
    assert "database" not in probes.collect(str(tmp_path), manifest, runtime=False)
