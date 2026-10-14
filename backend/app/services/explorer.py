"""Read-only view of an imported project for the UI: file tree, file contents, and a preview of
what a plan would change.

This is an HTTP-reachable file reader, so every path is confined to the project root (no absolute
paths, no `..`, no symlink escapes), heavy/irrelevant directories are never listed, files are size
capped, and binaries are not returned. The one exception is `write_file` below: a direct save of a
file the developer is editing in the UI. This is NOT the AI Planner's (unimplemented) `write_file`
ActionType and never runs through the sandbox, policy engine or an Execution/Verification/rollback
cycle — it is the human acting on their own project files, same as editing them in any other editor,
confined the same way reads are and kept out of secret-bearing .env files so a masked value already
on screen can never be saved back over the real one."""

import difflib
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from app.domain.actions import ClassifiedAction
from app.domain.enums import ActionType
from app.services.rollback import IGNORED_DIRS as _BASE_IGNORED

IGNORED_DIRS = _BASE_IGNORED | {
    ".venv", "venv", "dist", "build", ".next", ".devos_data", ".pytest_cache", ".ruff_cache",
}
MAX_FILES = 2000
MAX_BYTES = 256 * 1024
MASK = "********"
_TEMPLATE_ENV = {".env.example", ".env.sample", ".env.template", ".env.dist"}
_ENV_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")


class ExplorerError(ValueError):
    """A path the explorer refuses to serve (mapped to HTTP 400)."""


def resolve_within(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes it or hides in ignored dirs."""
    root = root.resolve()
    if not rel or PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).is_absolute():
        raise ExplorerError("path must be relative to the project")
    target = (root / rel).resolve()  # follows symlinks, so a link out of the project is caught
    if not target.is_relative_to(root):
        raise ExplorerError("path escapes the project")
    if IGNORED_DIRS & set(target.relative_to(root).parts[:-1]):
        raise ExplorerError("path is in an ignored directory")
    return target


def list_files(root: Path) -> tuple[list[dict[str, Any]], bool]:
    """All files (relative posix paths + sizes), pruned of ignored dirs. Returns (files, truncated)."""
    root = root.resolve()
    files: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for name in sorted(filenames):
            if len(files) >= MAX_FILES:
                return files, True
            full = Path(dirpath) / name
            try:
                size = full.stat().st_size
            except OSError:
                continue
            files.append({"path": full.relative_to(root).as_posix(), "size": size})
    return files, False


def is_secret_env(name: str) -> bool:
    return name.startswith(".env") and name not in _TEMPLATE_ENV


def mask_env(text: str, reveal_keys: set[str] | frozenset[str] = frozenset()) -> str:
    """Hide the value of every KEY=VALUE line except keys in reveal_keys."""
    out = []
    for line in text.split("\n"):
        m = _ENV_LINE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(4).strip() and m.group(2) not in reveal_keys:
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}"
        out.append(line)
    return "\n".join(out)


def read_file(root: Path, rel: str, reveal: bool = False) -> dict[str, Any]:
    path = resolve_within(root, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    size = path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(MAX_BYTES + 1)
    if b"\x00" in raw[:8192]:
        return {"path": rel, "size": size, "binary": True, "truncated": False, "masked": False, "content": ""}
    truncated = len(raw) > MAX_BYTES
    text = raw[:MAX_BYTES].decode("utf-8", errors="replace")
    masked = is_secret_env(path.name) and not reveal
    if masked:
        text = mask_env(text)
    return {"path": rel, "size": size, "binary": False, "truncated": truncated, "masked": masked, "content": text}


def write_file(root: Path, rel: str, content: str) -> dict[str, Any]:
    """Save new content for a file the developer is editing. Refuses to touch a file it hasn't
    already read as editable: binaries, and secret-bearing .env files (whose displayed content may
    be masked, and whose values already have a safer, typed path: a plan's `set_env_var` step)."""
    path = resolve_within(root, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    if is_secret_env(path.name):
        raise ExplorerError("secret .env files can't be edited here; use a plan's 'Set env var' step instead")
    with path.open("rb") as f:
        if b"\x00" in f.read(8192):
            raise ExplorerError("binary files can't be edited here")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ExplorerError(f"content is too large to save ({len(encoded)} bytes, max {MAX_BYTES})")
    path.write_bytes(encoded)
    return read_file(root, rel)


# --- change preview ---------------------------------------------------------------------------


def preview_env_change(text: str, key: str, value: str) -> str:
    """What sandbox/runner.js's set_env_var does to a file. Keep the two in sync (a test runs both)."""
    lines = [] if text == "" else re.split(r"\r?\n", re.sub(r"\r?\n$", "", text))
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def diff_lines(before: str, after: str) -> list[dict[str, str]]:
    """Full-file line diff as [{op: same|add|del, text}]."""
    a, b = before.splitlines(), after.splitlines()
    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [{"op": "same", "text": t} for t in a[i1:i2]]
        else:
            out += [{"op": "del", "text": t} for t in a[i1:i2]]
            out += [{"op": "add", "text": t} for t in b[j1:j2]]
    return out


def preview_plan(root: Path, actions: list[ClassifiedAction]) -> list[dict[str, Any]]:
    """Before/after/diff for each action, without touching disk. Values in env files stay masked
    except the key being set, so the reviewer sees exactly what will be written."""
    items: list[dict[str, Any]] = []
    for index, classified in enumerate(actions):
        action = classified.action
        item: dict[str, Any] = {"index": index, "type": action.type.value, "path": None, "diff": []}
        if action.type is ActionType.SET_ENV_VAR:
            rel, key, value = action.params.get("path", ".env"), action.params["key"], action.params["value"]
            path = resolve_within(root, rel)
            before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            after = preview_env_change(before, key, value)
            # Only the value being written is shown; the value it replaces stays masked.
            masked_before, masked_after = mask_env(before), mask_env(after, {key})
            item.update(path=rel, created=not path.is_file(), diff=diff_lines(masked_before, masked_after))
        items.append(item)
    return items
