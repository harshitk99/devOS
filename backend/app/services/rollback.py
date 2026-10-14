"""Rollback System: create a recovery point before execution; restore it when verification
fails (Verdict.FAILED must always trigger this).

A recovery point is a copy of the project files under settings.data_dir/snapshots/<id>. The
returned id is what gets stored on the Execution row."""

import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

from app.core.config import settings

# Directories that are heavy and re-creatable (or version control itself): never snapshotted,
# never touched on restore.
IGNORED_DIRS = {"node_modules", ".git", "__pycache__"}


def _snapshot_dir(recovery_point: str) -> Path:
    try:
        uuid.UUID(recovery_point)  # ids are uuids; refuse anything that could be a path
    except ValueError as e:
        raise ValueError(f"invalid recovery point: {recovery_point!r}") from e
    return settings.data_dir / "snapshots" / recovery_point


def _files(root: Path) -> Iterator[Path]:
    """Relative paths of every file under root, skipping IGNORED_DIRS."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and not IGNORED_DIRS & set(rel.parts):
            yield rel


def create_recovery_point(project_path: str) -> str:
    root = Path(project_path).resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    recovery_point = str(uuid.uuid4())
    snap = _snapshot_dir(recovery_point)
    (snap / "files").mkdir(parents=True)
    for rel in _files(root):
        dest = snap / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
    (snap / "meta.json").write_text(json.dumps({"project_path": str(root)}))
    return recovery_point


def restore(recovery_point: str) -> None:
    snap = _snapshot_dir(recovery_point)
    meta_file = snap / "meta.json"
    if not meta_file.is_file():
        raise FileNotFoundError(f"recovery point not found: {recovery_point}")
    root = Path(json.loads(meta_file.read_text())["project_path"])
    saved = set(_files(snap / "files"))

    for rel in list(_files(root)):  # files created after the snapshot go away
        if rel not in saved:
            (root / rel).unlink()
    for rel in saved:  # everything else returns to its snapshotted content
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap / "files" / rel, root / rel)
