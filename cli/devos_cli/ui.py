"""All terminal rendering, kept apart from the commands so the look lives in one place."""

import re
import sys
from datetime import datetime
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252, which can't draw the symbols
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

console = Console(legacy_windows=False)

ACCENT = "cyan"
SEVERITY = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "blue"}
VERDICT = {"VERIFIED": "green", "PARTIALLY VERIFIED": "yellow", "FAILED": "red", "NOT TESTED": "blue"}
STAGE = {"protect": "magenta", "execute": ACCENT, "verify": "yellow", "recover": "red"}
GOOD = {"ok", "passed", "pass", "success"}
BAD = {"failed", "fail", "error"}
_SENSITIVE = ("value", "password", "secret", "token", "url")


def _table(**kwargs: Any) -> Table:
    return Table(box=None, show_edge=False, pad_edge=False, padding=(0, 2, 0, 0), **kwargs)


def banner(project: dict) -> None:
    title = Text.assemble(("devos", f"bold {ACCENT}"), ("  self-healing dev environment", "dim"))
    body = Text.assemble((project["name"], "bold"), ("\n" + project["path"], "dim"))
    console.print(Panel(Group(title, body), box=box.ROUNDED, border_style="dim", padding=(0, 2), expand=False))


def note(message: str, style: str = "dim") -> None:
    console.print(Text(f"  {message}", style))


def error(message: str) -> None:
    console.print(Text.assemble(("✗ ", "bold red"), message))


def findings_table(findings: list[dict], verbose: bool = False) -> None:
    table = _table(show_header=True, header_style="dim")
    table.add_column("#", justify="right", style="dim")
    table.add_column("severity")
    table.add_column("problem")
    table.add_column("what's wrong", overflow="fold")
    table.add_column("fix", no_wrap=True)
    for f in findings:
        fix = Text("✓ can fix", "green") if f["fix_available"] else Text("diagnosis only", "dim")
        cause = Text(f["root_cause"])
        if verbose and f["evidence"]:
            cause.append("\n" + _evidence(f["evidence"]), "dim")
        table.add_row(str(f["rank"]), Text(f["severity"], SEVERITY.get(f["severity"], "")), f["check"], cause, fix)
    console.print(table)


def _evidence(evidence: dict) -> str:
    """Facts behind a finding, without anything that could be a credential."""
    parts = []
    for key, value in evidence.items():
        if any(word in key.lower() for word in _SENSITIVE):
            continue
        shown = str(value)
        parts.append(f"{key}={shown[:60] + '…' if len(shown) > 60 else shown}")
    return ", ".join(parts)


def plan_view(plan: dict, preview: list[dict]) -> None:
    by_index = {p["index"]: p for p in preview}
    steps = _table(show_header=False)
    steps.add_column(justify="right", style="dim")
    steps.add_column()
    steps.add_column()
    for i, step in enumerate(plan["actions"]):
        item = by_index.get(i, {})
        tag = Text("needs approval", "yellow") if step["decision"] == "require_approval" else Text("allowed", "green")
        summary = item.get("summary") or step["action"]["rationale"] or step["action"]["type"]
        steps.add_row(str(i + 1), Text(summary, "bold"), tag)
        if item.get("needs_network"):
            steps.add_row("", Text("⚠ runs with network access", "yellow"), "")
        if item.get("warning"):
            warning = item["warning"].removeprefix("Rolling back will not undo this: ")
            steps.add_row("", Text(f"⚠ rollback won't undo: {warning}", "yellow"), "")
        for line in item.get("diff", []):
            if line["op"] == "same":
                continue
            sign, style = ("+", "green") if line["op"] == "add" else ("-", "red")
            for text in filter(str.strip, line["text"].splitlines()):
                steps.add_row(Text(sign, style), Text(text, style), "")  # sign in its own column: never wraps
    console.print(Panel(steps, title=f"[bold]Plan[/bold] · {len(plan['actions'])} step(s)", title_align="left",
                        box=box.ROUNDED, border_style=ACCENT, padding=(1, 2)))


_NOISE = re.compile(r"^\w+ \(\w+ risk\):")  # "set_env_var (medium risk): ..." repeats the plan shown above


def log_line(entry: dict) -> None:
    stage, message = entry["stage"], entry["message"]
    if _NOISE.match(message):
        return
    msg_style = "yellow" if message.startswith("NOT undone") else "red" if message.startswith(("failed", "ROLLBACK FAILED")) else ""
    row = Table.grid(padding=(0, 2))
    row.add_column(width=10, justify="right", style=STAGE.get(stage, "dim"))
    row.add_column()
    row.add_row(stage, Text(message, msg_style))
    console.print(row)


def _mark(value: Any) -> Text:
    text = str(value).lower()
    if value is True or text in GOOD:
        return Text("✓", "green")
    if value is False or text in BAD:
        return Text("✗", "red")
    return Text("–", "dim")


def verdict_panel(verification: dict | None, plan_status: str) -> None:
    if verification is None:
        console.print(Panel("No verification was recorded.", border_style="dim", box=box.ROUNDED))
        return
    verdict, r = verification["verdict"], verification["results"]
    style = VERDICT.get(verdict, "white")
    rows = _table(show_header=False)
    if "error" in r:
        rows.add_row(Text("–", "dim"), "sandbox", Text(r["error"], "dim"))
    else:
        health = (r.get("health") or {}).get("status")
        build = (r.get("build") or {}).get("status", "not run")
        tests = (r.get("tests") or {}).get("status", "not run")
        errors = r.get("log_errors") or []
        rows.add_row(_mark(r.get("startup")), "app starts", Text(str(r.get("startup")), "dim"))
        rows.add_row(_mark(None if health is None else health == 200), "answers /health",
                     Text("not checked" if health is None else f"http {health}", "dim"))
        rows.add_row(_mark(build), "build", Text(str(build), "dim"))
        rows.add_row(_mark(tests), "tests", Text(str(tests), "dim"))
        rows.add_row(_mark(not errors), "log review", Text(f"{len(errors)} error line(s)" if errors else "clean", "dim"))
        recheck = r.get("recheck")
        if recheck is not None:
            remaining = recheck.get("remaining", [])
            rows.add_row(_mark(not remaining), "problems fixed",
                         Text("all gone" if not remaining else f"{len(remaining)} still there", "dim"))
            for f in remaining:
                rows.add_row("", "", Text(f"still present: {f['check']}", "red"))
            for f in recheck.get("other", []):
                rows.add_row("", "", Text(f"unrelated problem: {f['check']}", "yellow"))
    parts: list[Any] = [rows]
    if plan_status == "rolled_back":
        parts.append(Text("\nVerification failed, so your files were rolled back.", "red"))
    elif verdict == "PARTIALLY VERIFIED":
        parts.append(Text("\nChange kept; something unrelated still needs attention.", "yellow"))
    console.print(Panel(Group(*parts), title=Text(verdict, f"bold {style}"), title_align="left",
                        box=box.ROUNDED, border_style=style, padding=(1, 2)))


def audit_table(events: list[dict]) -> None:
    table = _table(header_style="dim")
    table.add_column("when", style="dim", no_wrap=True)
    table.add_column("event")
    table.add_column("actor", style="dim")
    table.add_column("detail", overflow="fold")
    for e in events:
        when = datetime.fromisoformat(e["created_at"]).astimezone().strftime("%m-%d %H:%M:%S")
        table.add_row(when, e["type"], e["actor"], _audit_detail(e))
    console.print(table)


def _audit_detail(event: dict) -> Text:
    p = event["payload"]
    kind = event["type"]
    if kind == "verification_completed" and p.get("verdict"):
        return Text(str(p["verdict"]), VERDICT.get(p["verdict"], ""))
    if kind == "plan_generated":
        return Text(f"{len(p.get('actions', []))} step(s)", "dim")
    if kind == "diagnosis_run" and isinstance(p.get("findings"), list):
        return Text(f"{len(p['findings'])} finding(s)", "dim")
    return Text("")
