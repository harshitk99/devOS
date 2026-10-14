import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import models  # noqa: F401  (registers tables)
from app.core.db import Base, engine


@pytest.fixture(autouse=True)
def _no_real_probes(monkeypatch, request):
    """Probes look at the real machine (ports, Docker, databases). Stub them so ordinary tests do
    not depend on what happens to be running; tests that want the real thing use @real_probes."""
    if request.node.get_closest_marker("real_probes"):
        return
    from app.services import probes

    monkeypatch.setattr(probes, "collect", lambda *a, **k: {})


@pytest.fixture
def db_session():
    """A Session on the real Postgres, wrapped in a transaction that is rolled back after the
    test (commits inside the code under test only release a savepoint). Skips if the DB is down."""
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("Postgres not reachable (docker compose up -d db)")
    Base.metadata.create_all(connection)
    connection.commit()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def demo_project(tmp_path):
    """A tiny Node project whose .env is missing DATABASE_URL."""
    (tmp_path / "package.json").write_text('{"name": "demo", "scripts": {"start": "node server.js"}}')
    (tmp_path / ".env.example").write_text("PORT=3100\nDATABASE_URL=postgresql://u:p@localhost/db\n")
    (tmp_path / ".env").write_text("PORT=3100\n")
    return tmp_path
