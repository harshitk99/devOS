# DevOS

Self-healing AI development environment. See [`CLAUDE.md`](CLAUDE.md) for the distilled spec and working conventions; full source specs are in [`docs/`](docs/).

## Layout

| Path | What it is |
|---|---|
| `backend/` | DevOS backend — Python + FastAPI. One module per component under `app/services/`. |
| `frontend/` | DevOS web UI — React + TypeScript (Vite). |
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

## Try it

```bash
python scripts/inject_failure.py missing-env     # DATABASE_URL disappears from demo-app/backend/.env
```

Open http://localhost:5173, paste the absolute path to `demo-app/backend`, then
**Import & diagnose → Generate recovery plan → Approve & run in sandbox**.
DevOS finds the missing variable, proposes a `set_env_var` action (medium risk, needs your approval),
applies it inside a resource-limited, network-less Docker container, restarts the app there,
checks `/health`, and reports a verdict.

The UI has three panes:

- **Files (left):** browse the imported project. Files involved in a problem get a red dot, files a plan
  would change get an amber one. `.env` values are masked until you click *Reveal values*.
- **Viewer (centre):** the selected file, or the **Proposed changes** tab: a before/after diff of exactly what
  each plan step would write. Clicking a finding jumps to the file and line it is about.
- **Details (right):** *Overview* (project map + env checklist), *Findings* (ranked by priority = severity ×
  confidence), *Plan* (steps with risk and approval status), *Run* (live sandbox log, verdict, re-verify,
  roll back) and *Audit* (every step, with details). The stage bar at the top is clickable.

Watch a failed fix get rolled back automatically:

```bash
python scripts/inject_failure.py unfixable       # the .env.example default is invalid too
python scripts/run_demo.py                       # -> verdict FAILED, .env restored
python scripts/inject_failure.py --reset         # healthy demo app again
```

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
