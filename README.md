# DevOS

Self-healing AI development environment. See [`CLAUDE.md`](CLAUDE.md) for the distilled spec and working conventions; full source specs are in [`docs/`](docs/).

## Layout

| Path | What it is |
|---|---|
| `backend/` | DevOS backend — Python + FastAPI. One module per component under `app/services/`. |
| `frontend/` | DevOS web UI — React + TypeScript (Vite). |
| `cli/` | The `devos` terminal client (`doctor` / `fix` / `up` / `status` / `audit`) — talks to the same REST API as the UI. |
| `demo-app/` | The breakable React + Node + PostgreSQL project DevOS diagnoses (`backend/` has its own compose file and `db/schema.sql`). **Not** part of DevOS's own backend. |
| `sandbox/` | Docker sandbox image (`Dockerfile`) and the fixed action dispatcher (`runner.js`). |
| `docs/` | Synopsis and BRD/TRD. |
| `scripts/` | `dev.py` (one-command start), `inject_failure.py` (break/repair the demo app in 8 ways), `run_scenarios.py` (end-to-end check of every problem), `run_demo.py` |

## Getting started

Requires Python 3.11+, Node 20+, and Docker (Docker Desktop with WSL2 on Windows).

**One command** (from the repo root) starts Postgres, the backend and the UI, and stops them on Ctrl+C:

```bash
python scripts/dev.py        # or: npm run dev
```

UI at http://localhost:5173, API docs at http://localhost:8000/docs. The first run also creates
`backend/.venv`, installs dependencies and creates `.env`. Postgres is left running on exit
(`python scripts/dev.py --stop-db` stops it too).

Or start each part yourself:

```bash
cp .env.example .env
docker compose up -d db                          # Postgres for DevOS itself

cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload                    # http://localhost:8000/docs (creates tables on start)
pytest                                           # DB/Docker-backed tests skip themselves if unavailable

cd ../frontend
npm install && npm run dev                       # http://localhost:5173
```

The first sandbox run pulls `node:20-alpine` and builds the `devos-sandbox` image (needs network once).

## Try it (step by step)

1. **Break the demo app on purpose:**
   ```bash
   python scripts/inject_failure.py missing-env     # DATABASE_URL disappears from demo-app/backend/.env
   ```
2. Open **http://localhost:5173**.
3. Paste the absolute path to `demo-app/backend` into the path field and click **Import & diagnose**.
4. Go to the **Findings** tab — you'll see the missing `DATABASE_URL` listed as the problem.
5. Go to **Plan**, read the one proposed step, and click **Approve & run in sandbox**.
6. Watch the **Run** tab: DevOS sets the variable, starts the app inside a resource-limited,
   network-less Docker container, checks `/health`, and shows a verdict (`VERIFIED`).

The UI has three panes:

- **Files (left):** browse the imported project. Files involved in a problem get a red dot, files a plan
  would change get an amber one. `.env` values are masked until you click *Reveal values*. You can also
  click **Edit** on any non-secret file to change it directly and **Save** — a plain, audited file save,
  not an AI action (see "Editing a file directly" below).
- **Viewer (centre):** the selected file, or the **Proposed changes** tab: a before/after diff of exactly what
  each plan step would write. Clicking a finding jumps to the file and line it is about.
- **Details (right):** *Overview* (project map + env checklist), *Findings* (ranked by priority = severity ×
  confidence), *Plan* (steps with risk and approval status), *Run* (live sandbox log, verdict, re-verify,
  roll back) and *Audit* (every step, with details). The stage bar at the top is clickable.

**Watch a failed fix get rolled back automatically:**

```bash
python scripts/inject_failure.py unfixable       # the .env.example default is invalid too
python scripts/run_demo.py                       # -> verdict FAILED, .env restored
python scripts/inject_failure.py --reset         # healthy demo app again
```

**Other failures to try** (each one undoes the last): `missing-dependency`, `port-conflict`, `db-down`,
`db-auth`, `db-schema`, `combo` (all three at once), `crash` (diagnosis only, no fix). Full list:
`python scripts/inject_failure.py --help`.

## Try it from the terminal instead

Once the backend is running (`python scripts/dev.py`), install the CLI once and point it at any project folder:

```bash
backend/.venv/Scripts/python -m pip install -e cli      # Windows; venv/bin/pip on Linux/macOS
cd demo-app/backend
devos doctor      # list what's wrong, most urgent first
devos fix         # plan a repair, ask for approval, run it, verify it
devos up          # the whole loop on repeat: diagnose -> fix -> verify, until healthy
devos status      # one-screen summary
devos audit       # everything DevOS has done to this project
```

`devos up --yes` skips the confirmation prompt (the plan is still printed first); it still stops and asks
whenever a step is genuinely risky (installing packages, running a migration) unless you pass `--yes`.

## Editing a file directly

Click **Edit** in the Files pane's file viewer, change the text, click **Save**. This writes straight to
disk and logs a `file_edited` audit event — it is **not** an AI action: no plan, no sandbox, no approval
step, because it's just you editing your own project, the same as any other editor. Secret `.env` files
can't be edited this way (their displayed values may be masked); use a plan's `set_env_var` step for those
instead.

## Testing

```bash
cd backend && .venv/Scripts/python -m pytest -q    # ~287 tests, 1 skipped (needs admin on Windows)
```

Needs DevOS's own Postgres running first (`docker compose up -d db`, or just `python scripts/dev.py`
once) — the DB-backed tests connect to it directly. Docker-backed tests skip themselves if the Docker
daemon isn't running.

```bash
cd frontend && npm run build                       # type-checks and builds the UI
python scripts/run_scenarios.py                    # end-to-end, through the real API + real Docker:
                                                     # imports the demo app, breaks it 9 different ways,
                                                     # fixes each one, and checks the app actually works after
```

`run_scenarios.py` is the closest thing to a full regression test: it drives the real pipeline (no
mocks), so it needs Docker and the DevOS Postgres container up, and takes a few minutes.

## What is implemented

The whole loop — Understand → Diagnose → Protect → Execute → Verify → Recover — works for these problems on the demo app:

| Problem | How DevOS fixes it |
|---|---|
| Missing setting in `.env` | Sets it from `.env.example` |
| Packages not installed | `npm install` in the sandbox (scripts off, only what package.json lists) |
| Port already in use | Changes the app's `PORT` to a free one |
| Database not running | Starts the project's compose database service |
| Wrong database login | Sets the login from `.env.example`, only if it was tested and works |
| Database tables missing | Runs `db/schema.sql` in one transaction |
| App crashes on start | Diagnosis only (shows the crash output) |

Each fix is verified (app starts and answers, build, tests, logs, and the Doctor re-checks that the problem is gone) and
rolled back if it fails. Run `python scripts/run_scenarios.py` to watch all of them work against the real system.
Not yet: other problem types, other languages/databases, and code-level fixes.

## LLM explanations (optional)

Set these in the root `.env` and restart the backend. Without a key the built-in rule text is used.

| Provider | `.env` |
|---|---|
| Anthropic (default) | `DEVOS_LLM_API_KEY=<key>` (model defaults to `claude-opus-5`) |
| Google Gemini | `DEVOS_LLM_PROVIDER=gemini` and `DEVOS_LLM_API_KEY=<key from Google AI Studio>` (model defaults to `gemini-2.5-flash`) |

`DEVOS_LLM_MODEL` overrides the model for either provider. A wrong model name is harmless: the call fails and the
rule text is used.

**What the model does, and does not do.** What a plan *does* is always decided by fixed rules in
`services/planner.py`. The model only writes a one-sentence explanation for each step, in a strict JSON schema. It
cannot add, remove, reorder or change a step, and it is never shown setting values, passwords, logs or file contents
(only problem descriptions and what each step acts on). If the call fails, times out, is refused, or answers
off-script, the rules' own wording is used. The Audit tab shows `planner:llm` or `planner:rules` (plus the reason).

**Privacy:** Google's free tier states that content may be used to improve its products. Because only problem
descriptions are sent, this is low-risk, but leave the key empty if you want nothing to leave your machine.
