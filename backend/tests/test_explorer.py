import os
import shutil
import subprocess

import pytest

from app.core.config import REPO_DIR
from app.domain.actions import ProposedAction
from app.domain.enums import ActionType
from app.services import explorer, policy

# --- path safety ------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../secret", "a/../../secret", "/etc/passwd", "C:\\Windows\\win.ini", ""])
def test_paths_outside_the_project_are_refused(demo_project, bad):
    with pytest.raises(explorer.ExplorerError):
        explorer.read_file(demo_project, bad)


def test_ignored_directories_are_not_served_or_listed(demo_project):
    (demo_project / "node_modules" / "pkg").mkdir(parents=True)
    (demo_project / "node_modules" / "pkg" / "index.js").write_text("x")
    (demo_project / ".git").mkdir()
    (demo_project / ".git" / "config").write_text("[core]")
    files, _ = explorer.list_files(demo_project)
    assert all(not f["path"].startswith(("node_modules", ".git")) for f in files)
    with pytest.raises(explorer.ExplorerError):
        explorer.read_file(demo_project, "node_modules/pkg/index.js")
    with pytest.raises(explorer.ExplorerError):
        explorer.read_file(demo_project, ".git/config")


def test_symlink_out_of_the_project_is_refused(demo_project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("top secret")
    link = demo_project / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("cannot create symlinks here (needs privileges on Windows)")
    with pytest.raises(explorer.ExplorerError, match="escapes"):
        explorer.read_file(demo_project, "link.txt")


def test_missing_file(demo_project):
    with pytest.raises(FileNotFoundError):
        explorer.read_file(demo_project, "nope.txt")


# --- listing and reading ----------------------------------------------------------------------


def test_listing_is_sorted_relative_posix_paths(demo_project):
    (demo_project / "src").mkdir()
    (demo_project / "src" / "a.js").write_text("1")
    files, truncated = explorer.list_files(demo_project)
    assert [f["path"] for f in files] == [".env", ".env.example", "package.json", "src/a.js"]
    assert truncated is False


def test_listing_is_capped(demo_project, monkeypatch):
    monkeypatch.setattr(explorer, "MAX_FILES", 2)
    files, truncated = explorer.list_files(demo_project)
    assert len(files) == 2 and truncated


def test_large_files_are_truncated_and_binaries_not_returned(demo_project, monkeypatch):
    monkeypatch.setattr(explorer, "MAX_BYTES", 10)
    (demo_project / "big.txt").write_text("x" * 100)
    (demo_project / "img.bin").write_bytes(b"\x89PNG\x00\x01\x02")
    big = explorer.read_file(demo_project, "big.txt")
    assert big["truncated"] and len(big["content"]) == 10 and big["size"] == 100
    binary = explorer.read_file(demo_project, "img.bin")
    assert binary["binary"] and binary["content"] == ""


# --- secrets ----------------------------------------------------------------------------------


def test_env_values_are_masked_unless_revealed(demo_project):
    (demo_project / ".env").write_text("# note\nPORT=3100\nDB_PASSWORD=hunter2\nEMPTY=\n")
    masked = explorer.read_file(demo_project, ".env")
    assert masked["masked"] and "hunter2" not in masked["content"] and "3100" not in masked["content"]
    assert "DB_PASSWORD=********" in masked["content"] and "# note" in masked["content"]
    assert "EMPTY=" in masked["content"] and "EMPTY=********" not in masked["content"]  # empty stays empty
    revealed = explorer.read_file(demo_project, ".env", reveal=True)
    assert not revealed["masked"] and "hunter2" in revealed["content"]


def test_env_example_is_not_masked(demo_project):
    result = explorer.read_file(demo_project, ".env.example")
    assert not result["masked"] and "postgresql://" in result["content"]


# --- change preview ---------------------------------------------------------------------------


def _set_env(**params):
    return policy.classify(ProposedAction(type=ActionType.SET_ENV_VAR, params=params))


def test_preview_shows_only_the_added_line_and_keeps_other_values_masked(demo_project):
    (demo_project / ".env").write_text("PORT=3100\nSECRET=hunter2\n")
    [item] = explorer.preview_plan(
        demo_project, [_set_env(path=".env", key="DATABASE_URL", value="postgresql://u:p@h/db")]
    )
    assert item["path"] == ".env" and item["created"] is False
    assert [d for d in item["diff"] if d["op"] != "same"] == [
        {"op": "add", "text": "DATABASE_URL=postgresql://u:p@h/db"}
    ]
    shown = "\n".join(d["text"] for d in item["diff"])
    assert "hunter2" not in shown and "3100" not in shown


def test_preview_of_a_replaced_value_and_a_new_file(demo_project):
    (demo_project / ".env").write_text("PORT=1\n")
    [replaced] = explorer.preview_plan(demo_project, [_set_env(path=".env", key="PORT", value="3100")])
    assert [(d["op"], d["text"]) for d in replaced["diff"]] == [("del", "PORT=********"), ("add", "PORT=3100")]
    (demo_project / ".env").unlink()
    [created] = explorer.preview_plan(demo_project, [_set_env(path=".env", key="PORT", value="3100")])
    assert created["created"] is True and created["diff"] == [{"op": "add", "text": "PORT=3100"}]


def test_preview_does_not_touch_disk(demo_project):
    before = (demo_project / ".env").read_text()
    explorer.preview_plan(demo_project, [_set_env(path=".env", key="X", value="1")])
    assert (demo_project / ".env").read_text() == before


# --- manual edit (the developer saving a file directly, not an AI action) ---------------------


def test_write_file_saves_and_returns_a_fresh_read(demo_project):
    (demo_project / "src.js").write_text("old")
    result = explorer.write_file(demo_project, "src.js", "new content")
    assert result["content"] == "new content" and (demo_project / "src.js").read_text() == "new content"


def test_write_file_refuses_a_path_outside_the_project(demo_project):
    with pytest.raises(explorer.ExplorerError):
        explorer.write_file(demo_project, "../secret", "x")


def test_write_file_refuses_a_missing_file(demo_project):
    with pytest.raises(FileNotFoundError):
        explorer.write_file(demo_project, "nope.txt", "x")


def test_write_file_refuses_secret_env_files(demo_project):
    before = (demo_project / ".env").read_text()
    with pytest.raises(explorer.ExplorerError, match="Set env var"):
        explorer.write_file(demo_project, ".env", "PORT=9999\n")
    assert (demo_project / ".env").read_text() == before  # refused before touching disk


def test_write_file_allows_the_env_template(demo_project):
    result = explorer.write_file(demo_project, ".env.example", "PORT=3100\n")
    assert not result["masked"] and result["content"] == "PORT=3100\n"


def test_write_file_refuses_binary_content(demo_project):
    (demo_project / "img.bin").write_bytes(b"\x89PNG\x00\x01\x02")
    with pytest.raises(explorer.ExplorerError, match="binary"):
        explorer.write_file(demo_project, "img.bin", "not an image anymore")


def test_write_file_refuses_content_over_the_size_cap(demo_project, monkeypatch):
    monkeypatch.setattr(explorer, "MAX_BYTES", 10)
    (demo_project / "src.js").write_text("short")
    with pytest.raises(explorer.ExplorerError, match="too large"):
        explorer.write_file(demo_project, "src.js", "x" * 20)
    assert (demo_project / "src.js").read_text() == "short"  # refused before touching disk


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "existing",
    ["", "\n", "A=1", "A=1\n", "A=1\r\nB=2\r\n", "# c\nKEY=old\nB=2\n", "  KEY = spaced\n", "KEY=1\nKEY=2\n"],
)
def test_preview_matches_what_the_sandbox_runner_actually_writes(tmp_path, existing):
    (tmp_path / ".env").write_bytes(existing.encode())
    payload = '{"mode":"action","action":{"type":"set_env_var","params":{"key":"KEY","value":"new"}}}'
    subprocess.run(
        ["node", str(REPO_DIR / "sandbox" / "runner.js")],
        env={**os.environ, "DEVOS_WORK": str(tmp_path), "DEVOS_PAYLOAD": payload},
        capture_output=True, check=True,
    )
    assert (tmp_path / ".env").read_bytes().decode() == explorer.preview_env_change(existing, "KEY", "new")
