"""One complete health check of a project: import it, probe the real environment, run the Doctor.

Shared by diagnosis and by verification (which re-runs it after a fix to confirm the problems are
actually gone), so both always look at the project the same way."""

from pathlib import Path
from typing import Any

from app.services import doctor, importer, probes


def run(project_path: str, runtime: bool = True) -> tuple[dict[str, Any], list[doctor.Finding]]:
    manifest = importer.import_project(Path(project_path))
    manifest["probes"] = probes.collect(manifest["path"], manifest, runtime=runtime)
    return manifest, doctor.diagnose(manifest)
