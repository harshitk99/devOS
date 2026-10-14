# DevOS — Self-Healing AI Development Environment

DevOS is an AI-powered developer operating environment that sits on top of Linux (or Windows+WSL2). It connects a project's codebase, terminal, containers, services, databases, ports, logs, tests and Git state — together with an AI agent — into a single environment that can understand why a project is broken, propose a safe recovery plan, execute it in an isolated sandbox, and verify the fix actually worked.

It is **not** a new OS: no kernel, bootloader, filesystem, or networking stack. It is an operating *environment* for developers, built on top of the existing OS.

Source specs: `docs/DevOS_Synopsis.docx`, `docs/BRD And TRD.docx` (this file is the authoritative distilled reference for coding work).

## Repository layout

```
backend/            Python + FastAPI (pyproject.toml; run pytest from here)
  app/api/          one router per API area (projects, diagnostics, files, plans, executions,
                    verification, recovery, audit, monitor); /ws for live updates
  app/core/         config (incl. SandboxLimits defaults), db session
  app/domain/       enums (Verdict, RiskLevel, ActionType…) and typed actions the planner may emit
  app/models/       SQLAlchemy models — the six core data models
  app/schemas/      API request/response models
  app/services/     one module per component (see "How it works now")
  tests/            per-component tests (~245)
cli/                `devos` terminal client (typer + rich + httpx): a second client of the REST API, no pipeline logic of its own
frontend/           React + TypeScript (Vite): three-pane UI; dev server proxies /api and /ws to :8000
demo-app/backend/   the intentionally-breakable target: Express + pg, db/schema.sql, own compose (Postgres :5433)
demo-app/frontend/  small React notes page for the demo (optional; port 5174)
sandbox/            Dockerfile + runner.js: the fixed dispatcher that runs inside the container
scripts/            dev.py (one-command start), inject_failure.py, run_scenarios.py, run_demo.py
docker-compose.yml  Postgres for DevOS itself (:5432)
```

## Current state (what exists and works)

The full loop works end to end for **eight problem types** on the demo app, verified by `scripts/run_scenarios.py`
(all 9 scenarios pass against real Docker/Postgres): missing setting, missing packages, port in use, database
stopped, wrong database login, missing tables, all three at once (combo), a startup crash (diagnosis only — no
automatic fix), and a bad fix that fails verification and rolls back. Roughly 245 backend tests pass.

### How it works now

| Stage | Module(s) | What it does |
|---|---|---|
| Understand | `importer` | Manifest: package.json, `.env.example` vs `.env`, install state (declared vs present in node_modules), configured port, `db/*.sql` and the tables they create, compose services, Git. Re-importing a path reuses the newest project. |
| Diagnose | `probes`, `doctor`, `checkup` | `probes` = read-only facts from the real machine (port bind test done like Node does, `psutil` holder, Postgres via `psycopg` with error classification, app start in the sandbox). `doctor` = pure rules over manifest+probes → findings (`env_missing`, `dependency_missing`, `port_in_use`, `database_unreachable`/`_auth`/`_missing`, `schema_missing`, `runtime_crash`). `checkup.run` = import+probe+diagnose, shared with verification. Findings are ranked by priority = severity weight × confidence. If `.env` lacks `DATABASE_URL` the DB is probed via the `.env.example` value so one gap can't hide a second problem. |
| Plan | `planner`, `llm_planner`, `llm_providers` | A deterministic **rule table** decides the actions and their order (settings → packages → port → start DB → login → migration). An optional LLM (Anthropic or Gemini via `DEVOS_LLM_PROVIDER`) only writes the one-line explanation per step, in a strict schema; it never sees values/passwords/logs and cannot add, drop or change a step. Any LLM failure falls back to rule text. Audit records `planner:llm` or `planner:rules`. |
| Review | `plan_review` | With an LLM key, a second LLM call *reviews* the rule steps (agree/doubt, note, 0-100 score, names of problems it thinks are uncovered) in a strict schema. Rules then cross-check every step (its rule in `planner.RULES` reproduces it exactly, policy is not `BLOCK`, `targets_of` covers it); failing steps are dropped before storing. The score reorders steps only **within one rule tier**, so tier order (settings → packages → port → DB → login → migration) always holds. Doubts are shown, never acted on. Result is stored on each action as `review` and in the `plan_generated` audit payload. Any failure → plain rule plan, review `unavailable`. |
| Protect | `policy`, `approval` | Default-deny risk classification; `REQUIRE_APPROVAL` actions run only on an approved plan; `BLOCK` never runs. |
| Execute | `executor`, `sandbox`, `docker_services` | `executor` routes each action. Sandbox actions run in the Docker image via `sandbox/runner.js` (fixed handlers; params never become command-line args). `restart_service` uses the Docker API and only starts existing containers whose compose labels match the project's own folder. |
| Verify | `verification` | Layers: app starts + answers `/health` in the sandbox; project `build`; project `test`; log review; then **the Doctor runs again** and every problem the plan targeted (matched by check *and* setting) must be gone (retries a few seconds for a just-started DB). `FAILED` (→ rollback) = won't start, build fails, or a targeted problem remains. Failing tests / log errors / other unrelated problems = `PARTIALLY VERIFIED` (change kept). Sandbox/infra failure = `NOT TESTED`, never a verdict about the app. |
| Recover | `rollback` | Snapshot of project files (excluding node_modules/.git) before running; restored on failure. It says plainly what a file rollback cannot undo (installed packages, applied migration, started service). |
| Audit | `audit`, `events`, `pipeline` | `pipeline.py` orchestrates and emits an AuditEvent per state change; `events` pushes live progress over `/ws`. |
| Watch | `monitor` | `GET /api/monitor`: listening ports (who holds them, which imported project), project processes, Docker containers. Read-only. |
| Browse | `explorer` | The only code that reads project files for the UI: read-only, confined to the project root (no `..`/symlink escapes), skips heavy dirs, size-capped, `.env` values masked, plan preview diffs. |

**Implemented action handlers** (`ActionType` is a closed enum; every member needs a rule in `services/policy.py`
and, to run, a handler): `set_env_var`, `install_dependency` (npm install of what package.json lists, install
scripts off, no package names accepted), `run_migration` (one `.sql` file, one transaction), `restart_service`,
`read_file`. Not implemented and fail closed: `write_file`, `free_port` (deliberately — a port conflict is fixed by
changing the port setting, which is undoable, not by killing another program), `run_build`, `run_tests`.

**UI** (`frontend/src`): Files tree with problem/change flags → file viewer (line highlight for the problem's
setting, `.env` masked) or **Proposed changes** diff; right tabs Overview (health checks), Findings (ranked, evidence,
"can DevOS fix this?", click → file), Plan (step summaries, network and can't-undo warnings, Approve/Reject), Run
(live log, verdict, verification breakdown, re-verify, roll back files), Audit, Live (monitor). State is always
re-read over REST; the websocket only says *when*.

## Commands

```
python scripts/dev.py                          # Postgres + backend + UI, Ctrl+C stops (or: npm run dev)
python scripts/inject_failure.py <mode>        # missing-env | missing-dependency | port-conflict | db-down |
                                               # db-auth | db-schema | combo | crash | unfixable ; --reset = healthy
python scripts/run_scenarios.py [names…]       # end-to-end through the real API + Docker, checks the app works after
backend/.venv/Scripts/python -m pip install -e cli   # once; then in a project folder: devos doctor | fix | up | status | audit
                                               # `devos up`: diagnose -> plan -> run -> verify, looped until healthy or stuck;
                                               # still asks approval per round whenever a step needs it (unless --yes)
                                               # (needs the backend running: python scripts/dev.py; DEVOS_API overrides the URL)
cd backend && .venv/Scripts/python -m pytest   # ~245 tests; needs DevOS's Postgres for the DB-backed ones
cd frontend && npm run build                   # type-check + build
```

## Known gaps / next steps

- Only the React+Node+Postgres reference stack; no other languages/frameworks (out of scope by design).
- `write_file`/`run_build`/`run_tests` actions and code-level fixes (an LLM proposing code edits) are not built; the
  LLM only explains. Diagnosis-only findings (`runtime_crash`, `database_missing`) have no fix.
- Only the `npm` package manager; only Postgres; only `docker compose` services.
- Rollback restores files only (see above). No multi-step DB rollback.
- No authentication / multi-user (out of scope). The API is meant for localhost use.
- Frontend has no automated tests (verified by hand and with a scripted browser); no syntax highlighting.
- Tables are created with `create_all` on start (no Alembic migrations yet); no background job queue beyond
  FastAPI `BackgroundTasks`.
- Doctor gaps: Git health, code-level checks, dependency version mismatches, docker-compose health, more DB kinds.
- Ideas: report export, diff-aware rollback of DB changes via snapshots, per-project settings, CI run of
  `run_scenarios.py`, richer monitor (CPU/memory per project).

## Core workflow

```
Understand → Diagnose → Protect → Execute → Verify → Recover
```

- **Understand** — build a machine-readable map of a project's code, dependencies, services, ports, and configuration.
- **Diagnose** — compare required vs. actual state to find the most likely root cause (Project Doctor).
- **Protect** — constrain the AI agent to a typed, permissioned set of actions validated by a deterministic policy engine before anything executes. The AI never gets raw/unrestricted shell access.
- **Execute** — run only approved actions inside an isolated Docker sandbox with bounded CPU, memory, time, filesystem, and network limits.
- **Verify** — prove via build, tests, startup, health checks, and log review that a change genuinely improved the project.
- **Recover** — automatically roll back if verification fails.

Every AI-initiated action must stay observable, permissioned, and independently verifiable. **The AI Planner proposes; it never directly controls the terminal.** Execution policy is enforced deterministically outside the model.

## Architecture / components

```
DevOS Web UI → DevOS Controller
                   ├─ Project Importer
                   ├─ Project Doctor (Diagnostics)
                   └─ Process/Port Monitor
                          ↓
                     AI Planner
                          ↓
                     Policy Engine
                          ↓
                     Approval UI
                          ↓
                     Docker Sandbox
                          ↓
                  Verification Engine
                          ↓
                Rollback / Recovery
                          +
                    Audit Trail (cross-cutting, records every stage)
```

| Component | Responsibility |
|---|---|
| Project Importer | Detects project files, framework, runtime, dependencies, config, Docker, database, commands, Git state. |
| Project Doctor | Deterministic checks: code, dependencies, environment, runtime, database, ports, Git. Produces evidence + severity + confidence + root cause. |
| AI Planner | Converts diagnostic findings into a structured, typed recovery plan. Never touches the terminal directly. |
| Policy Engine | Classifies each proposed action by risk → allow / require-approval / block. |
| Approval System | Shows issue, evidence, proposed action, and risk to the developer before execution. |
| Docker Sandbox | Runs only approved actions, isolated, with resource limits. |
| Verification Engine | Checks build, tests, runtime, health, logs → verdict. |
| Rollback System | Creates recovery points; restores previous state if verification fails. |
| Audit Trail | Records imports, diagnoses, plans, approvals, executions, verification, rollback — the whole sequence must be reconstructable. |

## Tech stack (do not substitute without a reason)

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript |
| Backend | Python + FastAPI |
| Live updates | WebSockets |
| AI | External LLM API, accessed only via **typed tool schemas** (never free-form shell/tool access) |
| Containerization / sandbox | Docker |
| Database | PostgreSQL |
| System monitoring | `psutil`, `/proc`, Docker API |
| Version control | Git |
| Testing | Pytest |
| API style | REST / JSON |
| Target OS | Linux, or Windows with WSL2 (required for Docker sandboxing + process/port monitoring) |

**Reference/demo app** (the intentionally-broken project DevOS diagnoses and repairs — *not* part of DevOS's own backend): React + Node.js + PostgreSQL, with injected configuration, dependency, port, database, and runtime failures.

Don't conflate the demo app's Node.js with DevOS's own backend, which is Python/FastAPI.

## Core data models

| Model | Purpose |
|---|---|
| Project | Project details and manifest |
| Diagnostic | Detected issues and evidence |
| RecoveryPlan | AI-generated actions and risk classification |
| Execution | Sandbox execution tracking |
| Verification | Build, test, and runtime results |
| AuditEvent | System activity log |

## API areas

Projects (import, view, diagnose) · Diagnostics (view findings) · Plans (generate, approve, reject) · Execution (start, monitor) · Verification (verify results) · Recovery (rollback) · Audit (view project activity).

## Non-functional requirements

- **Security** — restricted AI access, sandboxing, mandatory approval for risky actions.
- **Reliability** — explicit verification with automatic rollback on failure.
- **Performance** — progress tracking and background execution (don't block the UI on long-running plans).
- **Maintainability** — modular, independently testable components (Importer, Doctor, Planner, Policy Engine, Sandbox, Verification, Rollback, Audit should each be testable in isolation).

## Scope

**MVP includes:** Project Importer, Project Doctor, Process/Port Monitor, AI Planner, Docker Sandbox, Approval UI, Verification Engine, Audit Trail. Reference stack is a single one (React + Node.js + PostgreSQL) — do not generalize to multi-language/multi-framework support in the prototype.

**Out of scope — do not build these:**
- Full IDE replacement
- Kernel-level OS development
- Multi-user collaboration
- Production deployment automation
- Support for every programming language
- Unrestricted host administration
- Advanced cloud management
- Fully autonomous AI execution (approval is always required for impactful actions)

## Verification verdicts

Every recovery attempt ends in one of: `VERIFIED`, `PARTIALLY VERIFIED`, `FAILED`, `NOT TESTED`. A failed verification must trigger automatic rollback.

## Working conventions

- Any new AI-agent action type must go through the Policy Engine's risk classification (allow / approval-required / block) — never wire a new action straight to execution.
- Sandbox execution limits (CPU, memory, timeout, filesystem, network) are non-negotiable defaults for any new sandboxed action.
- Every state-changing operation (import, diagnosis, plan, approval, execution, verification, rollback) must emit an AuditEvent.
- Prefer deterministic checks (Project Doctor) over LLM judgment wherever a check can be expressed as a rule — the LLM is for planning/reasoning, not for deciding ground truth.
- The LLM **explains and reviews, it does not decide**: actions always come from `planner.RULES`; a review is an opinion that deterministic code cross-checks (`plan_review.cross_check`) and bounds (ranking never crosses a rule tier). Never pass secrets, setting values, logs or file contents to a model (`llm_planner._payload` sends problem descriptions and step targets only).
- Sandbox network is `none` except `sandbox.NETWORK_ACTIONS` (`install_dependency`, `run_migration`), both approval-required. Any new exception needs a policy rule, a test in `test_executor.py`, and a mention here.
- `sandbox/runner.js` only spawns fixed npm command lines; a request parameter must never become an argument. Handlers must fail closed on unknown types.
- A new Doctor check needs: a rule in `doctor.CHECKS`, a planner rule *or* an explicit "diagnosis only" decision, an entry in `planner.targets_of` if it can be fixed (so verification re-checks it), a UI title/facts entry in `Panels.tsx`, a `inject_failure.py` mode and a `run_scenarios.py` scenario.
- Anything a rollback cannot undo must be listed in `executor.NOT_UNDONE` so the UI and log say so; never imply a full undo.
- Read-only code paths that touch project files go through `services/explorer.py` (path confinement + masking).

## Gotchas (learned the hard way)

- **Test isolation:** `tests/conftest.py` stubs `probes.collect` for every test (probes look at the real machine). Tests that want real ports/databases use `@pytest.mark.real_probes`. DB-backed tests roll back via a savepoint; Docker tests skip if the daemon is down.
- **Sandbox image is content-tagged** (`devos-sandbox:<hash of Dockerfile+runner.js>`); editing `runner.js` rebuilds it automatically. Before this, edits were silently ignored.
- **Port checks must bind like Node** (dual-stack `::`): an IPv4-only bind wrongly reports a port free on Windows. The port-conflict demo holder binds the same way.
- **Windows process trees:** a venv `python.exe` is a launcher + child; kill with `taskkill /T`. `dev.py` does this on shutdown.
- **Fresh Postgres containers restart once during first-time setup**: wait for several consecutive successful queries, not the first "ready" (done in `dev.py` and `inject_failure.py`).
- `uvicorn --reload` on Windows sometimes stalls after a burst of edits (serves stale code with no error); restart `scripts/dev.py` if an endpoint you just added 404s.
- psycopg gives no error code for rejected logins/unknown DBs — classify from the server message (`probes._classify`).
- Node prints the offending source line before `Error: …` in a crash; `doctor.check_runtime_crash` picks the real error line.
- The demo compose project name is `devos-demo` (container `devos-demo-db-1`, port 5433) so it never clashes with DevOS's own Postgres (5432).
