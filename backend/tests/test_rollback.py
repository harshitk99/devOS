import pytest

from app.core.config import settings
from app.services import rollback


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path_factory.mktemp("devos_data"))


def test_restore_reverts_edits_and_removes_new_files(demo_project):
    point = rollback.create_recovery_point(str(demo_project))

    (demo_project / ".env").write_text("PORT=1\nJUNK=1\n")
    (demo_project / "new.txt").write_text("created after snapshot")
    (demo_project / "package.json").unlink()

    rollback.restore(point)

    assert (demo_project / ".env").read_text() == "PORT=3100\n"
    assert (demo_project / "package.json").is_file()
    assert not (demo_project / "new.txt").exists()


def test_node_modules_and_git_are_left_alone(demo_project):
    (demo_project / "node_modules").mkdir()
    (demo_project / "node_modules" / "keep.js").write_text("x")
    point = rollback.create_recovery_point(str(demo_project))

    (demo_project / "node_modules" / "later.js").write_text("y")
    rollback.restore(point)

    assert (demo_project / "node_modules" / "keep.js").exists()
    assert (demo_project / "node_modules" / "later.js").exists()  # untouched, not snapshotted


@pytest.mark.parametrize("bad", ["../../etc", "not-a-uuid", ""])
def test_recovery_point_ids_cannot_be_paths(bad):
    with pytest.raises(ValueError):
        rollback.restore(bad)


def test_unknown_recovery_point():
    with pytest.raises(FileNotFoundError):
        rollback.restore("00000000-0000-0000-0000-000000000000")
