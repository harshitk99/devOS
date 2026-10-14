"""`devos`: the DevOS loop (understand, diagnose, protect, execute, verify, recover) from a terminal."""

import functools
import time
from pathlib import Path
from typing import Annotated

import typer

from devos_cli import __version__, ui
from devos_cli.api import Api, DevosError

app = typer.Typer(
    name="devos",
    help="Self-healing dev environment: find what's broken, fix it safely, prove it worked.",
    add_completion=False,
    pretty_exceptions_enable=False,
)

PathOpt = Annotated[Path, typer.Option("--path", "-p", help="Project folder (default: current folder).")]
POLL_S = 0.6


def _guard(fn):
    """Turn expected failures into one clean line and a non-zero exit code."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DevosError as e:
            ui.error(str(e))
            raise typer.Exit(2) from None
        except KeyboardInterrupt:
            ui.note("interrupted (a running plan keeps going on the backend; see `devos audit`)")
            raise typer.Exit(130) from None

    return wrapper


def _open(path: Path) -> tuple[Api, dict]:
    api = Api()
    return api, api.import_project(str(path.resolve()))


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
) -> None:
    if version:
        typer.echo(f"devos {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        ui.console.print(ctx.get_help())


def _diagnose(path: Path, verbose: bool = False) -> tuple[Api, dict, list[dict]]:
    api, project = _open(path)
    ui.banner(project)
    with ui.console.status("[dim]checking the project…[/dim]", spinner="dots"):
        findings = api.diagnose(project["id"])
    if not findings:
        ui.console.print("\n[bold green]✓ Healthy[/bold green]  nothing wrong found.\n")
    else:
        ui.console.print(f"\n[bold]{len(findings)} problem(s)[/bold], most urgent first\n")
        ui.findings_table(findings, verbose)
        ui.console.print()
    return api, project, findings


@app.command()
@_guard
def doctor(
    path: PathOpt = Path("."),
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show the evidence behind each problem.")] = False,
) -> None:
    """Check the project and list what's wrong, most urgent first."""
    _, _, findings = _diagnose(path, verbose)
    if findings:
        ui.note("run `devos fix` to plan a repair")
        raise typer.Exit(1)


@app.command()
@_guard
def status(path: PathOpt = Path(".")) -> None:
    """One-screen summary of the project and its setup."""
    api, project = _open(path)
    ui.banner(project)
    m = project["manifest"]
    install, cfg, git, env = m.get("install", {}), m.get("config", {}), m.get("git", {}), m.get("env", {})
    stack = " + ".join(x for x in (m.get("runtime"), m.get("framework"), (m.get("database") or {}).get("kind")) if x)
    missing = install.get("missing", [])
    packages = "installed" if install.get("node_modules_exists") and not missing else f"{len(missing)} missing"
    branch = f"{git.get('branch')}{' (uncommitted changes)' if git.get('dirty') else ''}" if git.get("is_repo") else "not a repo"
    rows = [
        ("stack", stack or "unknown"),
        ("start", m.get("start_command") or "none"),
        ("port", str(cfg.get("port") or "not set")),
        ("packages", packages),
        (".env", "present" if env.get("env_file_exists") else "missing"),
        ("git", branch),
    ]
    ui.console.print()
    for key, value in rows:
        ui.console.print(f"  [dim]{key:>9}[/dim]  {value}")
    with ui.console.status("[dim]checking the project…[/dim]", spinner="dots"):
        findings = api.diagnose(project["id"])
    health = f"[bold red]{len(findings)} problem(s)[/bold red]  run `devos doctor`" if findings else "[bold green]healthy[/bold green]"
    ui.console.print(f"  [dim]{'health':>9}[/dim]  {health}\n")


@app.command()
@_guard
def audit(
    path: PathOpt = Path("."),
    last: Annotated[int, typer.Option("--last", "-n", help="Show only the newest N events.")] = 20,
) -> None:
    """The recorded history of everything DevOS did to this project."""
    api, project = _open(path)
    ui.banner(project)
    events = sorted(api.audit(project["id"]), key=lambda e: e["created_at"])[-last:]
    ui.console.print()
    if events:
        ui.audit_table(events)
    else:
        ui.note("nothing recorded yet")
    ui.console.print()


@app.command()
@_guard
def up(
    path: PathOpt = Path("."),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the approval question whenever a step needs one (the plan is still shown).")] = False,
    max_rounds: Annotated[int, typer.Option("--max-rounds", help="Safety cap on diagnose-fix-verify cycles.")] = 5,
) -> None:
    """Diagnose, fix, and verify in a loop until the project is healthy (or DevOS can't go further).

    Each round plans and runs every fix the Doctor can currently see in one shot; the loop exists
    for problems a fix only uncovers afterwards (e.g. starting the database reveals a missing table).
    Approval is still asked for whenever a step needs it — this never silently auto-approves risk."""
    api, project = _open(path)
    ui.banner(project)
    previous_fixable: set[str] | None = None

    for round_num in range(1, max_rounds + 1):
        with ui.console.status("[dim]checking the project…[/dim]", spinner="dots"):
            findings = api.diagnose(project["id"])

        if not findings:
            ui.console.print("\n[bold green]✓ Healthy[/bold green]  devos up finished.\n")
            return

        ui.console.print(f"\n[bold]round {round_num}[/bold] · {len(findings)} problem(s)\n")
        ui.findings_table(findings)
        ui.console.print()

        fixable = {f["check"] for f in findings if f["fix_available"]}
        if not fixable:
            ui.note("remaining problem(s) have no automatic fix; stopping here")
            raise typer.Exit(1)
        if fixable == previous_fixable:
            ui.note("the same problem(s) came back after a fix attempt; stopping to avoid looping")
            raise typer.Exit(1)
        previous_fixable = fixable

        plan = api.generate_plan(project["id"])
        ui.plan_view(plan, api.preview(plan["id"]))

        if any(a["decision"] == "require_approval" for a in plan["actions"]):
            if not yes and not typer.confirm("This step needs approval. Approve and run it?", default=False):
                api.reject(plan["id"])
                ui.note("plan rejected; stopping")
                raise typer.Exit(1)
            api.approve(plan["id"])

        execution = api.execute(plan["id"])
        ui.console.print()
        seen = 0
        with ui.console.status("[dim]running…[/dim]", spinner="dots"):
            while True:
                time.sleep(POLL_S)
                current = api.execution(execution["id"])
                for entry in current["log"][seen:]:
                    ui.log_line(entry)
                seen = len(current["log"])
                plan = api.plan(plan["id"])
                if plan["status"] != "executing":
                    break
        ui.console.print()

        verifications = api.verifications(execution["id"])
        latest = verifications[-1] if verifications else None
        ui.verdict_panel(latest, plan["status"])
        verdict = latest["verdict"] if latest else None
        if verdict not in ("VERIFIED", "PARTIALLY VERIFIED"):
            ui.note("verification didn't succeed; stopping here (see `devos audit` for detail)")
            raise typer.Exit(1)
        ui.console.print()

    ui.note(f"stopped after {max_rounds} round(s) without a clean diagnosis; see `devos doctor`")
    raise typer.Exit(1)


@app.command()
@_guard
def fix(
    path: PathOpt = Path("."),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the approval question (the plan is still shown).")] = False,
) -> None:
    """Plan a repair, ask for approval, run it in the sandbox, and verify the result."""
    api, project, findings = _diagnose(path)
    if not findings:
        return
    if not any(f["fix_available"] for f in findings):
        ui.note("DevOS can only diagnose these; there is no automatic fix.")
        raise typer.Exit(1)

    plan = api.generate_plan(project["id"])
    ui.plan_view(plan, api.preview(plan["id"]))

    if not yes and not typer.confirm("Approve and run this plan?", default=False):
        api.reject(plan["id"])
        ui.note("plan rejected; nothing was changed")
        raise typer.Exit(1)
    api.approve(plan["id"])

    execution = api.execute(plan["id"])
    ui.console.print()
    seen = 0
    with ui.console.status("[dim]running…[/dim]", spinner="dots"):
        while True:
            time.sleep(POLL_S)
            current = api.execution(execution["id"])
            for entry in current["log"][seen:]:
                ui.log_line(entry)
            seen = len(current["log"])
            plan = api.plan(plan["id"])
            if plan["status"] != "executing":
                break
    ui.console.print()

    verifications = api.verifications(execution["id"])
    latest = verifications[-1] if verifications else None
    ui.verdict_panel(latest, plan["status"])
    if not latest or latest["verdict"] not in ("VERIFIED", "PARTIALLY VERIFIED"):
        raise typer.Exit(1)
