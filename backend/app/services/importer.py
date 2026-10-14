"""Project Importer: detect framework, runtime, dependencies, config, Docker, database,
commands and Git state, and return a machine-readable manifest.

MVP scope: React + Node.js + PostgreSQL only. Do not generalize."""

import json
import re
from pathlib import Path
from typing import Any

import yaml

_POSTGRES_PACKAGES = {"pg", "pg-promise", "postgres", "knex", "sequelize", "typeorm", "prisma"}
_DOCKER_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml")
_COMPOSE_FILES = _DOCKER_FILES[1:]
_CREATE_TABLE = re.compile(
    r'^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?(?:"?\w+"?\.)?"?(\w+)"?', re.IGNORECASE | re.MULTILINE
)


def parse_env(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines (comments and blanks ignored). Later duplicates win."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.isidentifier():
            values[key] = value.strip()
    return values


def _read_env(path: Path) -> dict[str, str] | None:
    return parse_env(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else None


def _git_state(path: Path) -> dict[str, Any]:
    try:
        from git import InvalidGitRepositoryError, NoSuchPathError, Repo

        repo = Repo(path, search_parent_directories=True)
        try:
            branch = repo.active_branch.name
        except TypeError:  # detached HEAD
            branch = None
        return {"is_repo": True, "branch": branch, "dirty": repo.is_dirty(untracked_files=True)}
    except (ImportError, InvalidGitRepositoryError, NoSuchPathError):
        return {"is_repo": False, "branch": None, "dirty": False}


def _install_state(root: Path, deps: dict[str, str]) -> dict[str, Any]:
    """Which declared packages are not present in node_modules."""
    declared = sorted(deps)
    missing = [n for n in declared if not (root / "node_modules" / n / "package.json").is_file()]
    return {"declared": declared, "missing": missing, "node_modules_exists": (root / "node_modules").is_dir()}


def _schema_info(root: Path) -> tuple[list[str], list[str]]:
    """SQL files under db/ and the tables they create (what the database is expected to contain)."""
    files = sorted(p.relative_to(root).as_posix() for p in (root / "db").glob("*.sql")) if (root / "db").is_dir() else []
    tables: list[str] = []
    for rel in files:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        tables += [t for t in _CREATE_TABLE.findall(text) if t not in tables]
    return files, tables


def _compose_info(root: Path) -> dict[str, Any]:
    """Services declared in the project's compose file, and which one looks like Postgres."""
    for name in _COMPOSE_FILES:
        f = root / name
        if not f.is_file():
            continue
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return {"file": name, "services": {}, "postgres_service": None}
        services = {
            svc: str((spec or {}).get("image", "")) for svc, spec in (doc.get("services") or {}).items()
        }
        pg = next((s for s, image in services.items() if "postgres" in image.lower()), None)
        return {"file": name, "services": services, "postgres_service": pg}
    return {"file": None, "services": {}, "postgres_service": None}


def _port(actual: dict[str, str] | None, example: dict[str, str]) -> dict[str, Any]:
    for source, values in ((".env", actual or {}), (".env.example", example)):
        if values.get("PORT", "").isdigit():
            return {"port": int(values["PORT"]), "source": source}
    return {"port": None, "source": None}


def import_project(path: Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")

    pkg: dict[str, Any] = {}
    pkg_file = root / "package.json"
    if pkg_file.is_file():
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pkg = {}

    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    scripts = pkg.get("scripts", {})

    example = _read_env(root / ".env.example") or {}
    actual = _read_env(root / ".env")
    present = sorted(k for k, v in (actual or {}).items() if v != "")

    uses_postgres = bool(_POSTGRES_PACKAGES & deps.keys()) or any(
        v.startswith(("postgres://", "postgresql://")) for v in example.values()
    )
    schema_files, tables = _schema_info(root)

    return {
        "name": pkg.get("name") or root.name,
        "path": str(root),
        "runtime": "node" if pkg_file.is_file() else None,
        "framework": "react" if "react" in deps else None,
        "dependencies": deps,
        "scripts": scripts,
        "start_command": "npm start" if "start" in scripts else None,
        "env": {
            "required": sorted(example),
            "example_values": example,
            "present": present,
            "env_file_exists": actual is not None,
        },
        "install": _install_state(root, deps),
        "config": _port(actual, example),
        "database": {"kind": "postgres" if uses_postgres else None, "schema_files": schema_files, "expected_tables": tables},
        "docker": {"files": [f for f in _DOCKER_FILES if (root / f).is_file()], **_compose_info(root)},
        "git": _git_state(root),
    }
