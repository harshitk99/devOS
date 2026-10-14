import { useEffect, useRef } from "react";
import type { Diagnostic, Verdict, Verification } from "../api/types";
import type { Workspace } from "../hooks/useWorkspace";

const VERDICT_CLASS: Record<Verdict, string> = {
  VERIFIED: "good",
  "PARTIALLY VERIFIED": "warn",
  FAILED: "bad",
  "NOT TESTED": "",
};
const PLAN_CLASS: Record<string, string> = {
  proposed: "info",
  approved: "info",
  executing: "warn",
  completed: "good",
  rejected: "",
  rolled_back: "bad",
};
const CHECK_TITLE: Record<string, string> = {
  env_missing: "Missing setting",
  dependency_missing: "Packages not installed",
  port_in_use: "Port already in use",
  database_unreachable: "Database not running",
  database_auth: "Database login rejected",
  database_missing: "Database does not exist",
  schema_missing: "Database tables missing",
  runtime_crash: "App crashes on start",
};

interface Manifest {
  name?: string;
  path?: string;
  runtime?: string | null;
  framework?: string | null;
  start_command?: string | null;
  scripts?: Record<string, string>;
  dependencies?: Record<string, string>;
  env?: { required?: string[]; present?: string[]; env_file_exists?: boolean };
  install?: { declared?: string[]; missing?: string[]; node_modules_exists?: boolean };
  config?: { port?: number | null };
  database?: { kind?: string | null; schema_files?: string[]; expected_tables?: string[] };
  docker?: { files?: string[]; file?: string | null; postgres_service?: string | null };
  git?: { is_repo?: boolean; branch?: string | null; dirty?: boolean };
  probes?: {
    port?: { port: number; in_use: boolean; holder?: { name?: string; pid?: number; same_project?: boolean } | null };
    database?: { state: string; host?: string; port?: number; database?: string; tables?: string[] };
    runtime?: { ran: boolean; startup?: string; reason?: string };
  };
}

/** Which file (and setting) to open to look at a problem. */
function targetFor(f: Diagnostic, m: Manifest): { path: string; key: string | null } | null {
  const e = f.evidence;
  const str = (v: unknown) => (typeof v === "string" ? v : null);
  switch (f.check) {
    case "env_missing":
    case "port_in_use":
    case "database_auth":
      return { path: str(e.env_file) ?? ".env", key: str(e.key) };
    case "dependency_missing":
      return { path: "package.json", key: null };
    case "schema_missing":
      return str(e.migration) ? { path: str(e.migration)!, key: null } : null;
    case "database_unreachable":
      return m.docker?.file ? { path: m.docker.file, key: null } : null;
    default:
      return null;
  }
}

/** The facts behind a finding, in words. */
function facts(f: Diagnostic): string[] {
  const e = f.evidence as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  switch (f.check) {
    case "dependency_missing":
      return [`Missing: ${(e.missing ?? []).slice(0, 6).join(", ")}${(e.missing ?? []).length > 6 ? "…" : ""}`];
    case "port_in_use":
      return [
        e.holder?.name ? `Held by ${e.holder.name}${e.holder.pid ? ` (process ${e.holder.pid})` : ""}` : "Held by another program",
        e.suggested_port ? `Free port available: ${e.suggested_port}` : "No free port found nearby",
      ];
    case "database_unreachable":
      return [`Server: ${e.host}:${e.port}`, e.service ? `Compose service to start: ${e.service}` : "No compose service found to start"];
    case "database_auth":
      return [e.example_works ? "The login in .env.example was tested and works" : "No working login was found to suggest"];
    case "schema_missing":
      return [`Missing tables: ${(e.missing_tables ?? []).join(", ")}`, `Migration file: ${e.migration ?? "none found"}`];
    default:
      return [];
  }
}

export function Overview({ ws }: { ws: Workspace }) {
  const m = (ws.project?.manifest ?? {}) as Manifest;
  const present = new Set(m.env?.present ?? []);
  const deps = Object.entries(m.dependencies ?? {});
  const scripts = Object.entries(m.scripts ?? {});
  const probes = m.probes;

  const checks: { label: string; ok: boolean | null; detail: string }[] = [];
  if (m.env?.required?.length) {
    const missing = m.env.required.filter((k) => !present.has(k));
    checks.push({ label: "Settings", ok: missing.length === 0, detail: missing.length ? `missing ${missing.join(", ")}` : "all set" });
  }
  if (m.install && (m.install.declared?.length ?? 0) > 0) {
    const n = m.install.missing?.length ?? 0;
    checks.push({ label: "Packages", ok: n === 0, detail: n ? `${n} not installed` : "all installed" });
  }
  if (probes?.port) {
    const p = probes.port;
    checks.push({
      label: `Port ${p.port}`,
      ok: !p.in_use || !!p.holder?.same_project,
      detail: !p.in_use ? "free" : p.holder?.same_project ? "used by this project" : `in use by ${p.holder?.name ?? "another program"}`,
    });
  }
  if (probes?.database) {
    const d = probes.database;
    const label = { ok: "reachable", unreachable: "not accepting connections", auth_failed: "login rejected", missing_database: "database does not exist", error: "error" }[d.state] ?? d.state;
    checks.push({ label: "Database", ok: d.state === "ok", detail: label });
    const expected = m.database?.expected_tables ?? [];
    if (d.state === "ok" && expected.length) {
      const gone = expected.filter((t) => !(d.tables ?? []).includes(t));
      checks.push({ label: "Tables", ok: gone.length === 0, detail: gone.length ? `missing ${gone.join(", ")}` : `${expected.join(", ")} present` });
    }
  }
  if (probes?.runtime?.ran) {
    checks.push({ label: "Starts up", ok: probes.runtime.startup !== "crashed", detail: probes.runtime.startup === "crashed" ? "crashes on start" : "starts fine" });
  }

  return (
    <div className="pad">
      <h3>Health checks</h3>
      {checks.length === 0 ? (
        <div className="muted">Run a scan to see them.</div>
      ) : (
        <ul className="checklist">
          {checks.map((c) => (
            <li key={c.label}>
              <span className={`tick ${c.ok ? "ok" : "no"}`}>{c.ok ? "✓" : "✗"}</span>
              <strong>{c.label}</strong>
              <span className="muted">{c.detail}</span>
            </li>
          ))}
        </ul>
      )}

      <h3>Project</h3>
      <dl className="kv">
        <dt>Name</dt><dd>{m.name}</dd>
        <dt>Runtime</dt><dd>{m.runtime ?? "unknown"}{m.framework ? ` · ${m.framework}` : ""}</dd>
        <dt>Start</dt><dd><code>{m.start_command ?? "none"}</code></dd>
        <dt>Database</dt><dd>{m.database?.kind ?? "none detected"}</dd>
        <dt>Docker</dt><dd>{m.docker?.files?.length ? m.docker.files.join(", ") : "none"}</dd>
        <dt>Git</dt>
        <dd>{m.git?.is_repo ? `${m.git.branch ?? "detached"}${m.git.dirty ? " (uncommitted changes)" : ""}` : "not a repository"}</dd>
      </dl>

      {(m.env?.required ?? []).length > 0 && (
        <>
          <h3>Settings the app needs</h3>
          <ul className="checklist">
            {m.env!.required!.map((key) => (
              <li key={key}>
                <span className={`tick ${present.has(key) ? "ok" : "no"}`}>{present.has(key) ? "✓" : "✗"}</span>
                <button className="linkish" onClick={() => ws.openFile(".env", key)}>{key}</button>
                <span className="muted">{present.has(key) ? "set" : "missing from .env"}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {scripts.length > 0 && (
        <>
          <h3>Scripts</h3>
          <dl className="kv">{scripts.map(([k, v]) => [<dt key={k}>{k}</dt>, <dd key={k + "v"}><code>{v}</code></dd>])}</dl>
        </>
      )}
      <h3>Dependencies ({deps.length})</h3>
      {deps.length === 0 ? <div className="muted">None</div> : <div>{deps.map(([k, v]) => <div key={k}><code>{k}</code> <span className="muted">{v}</span></div>)}</div>}
    </div>
  );
}

export function Findings({ ws }: { ws: Workspace }) {
  const findings = ws.findings ?? [];
  const m = (ws.project?.manifest ?? {}) as Manifest;
  const canPlan =
    findings.some((f) => f.fix_available) && (!ws.plan || ["completed", "rejected", "rolled_back"].includes(ws.plan.status));
  return (
    <div className="pad">
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="muted">Ranked by priority = severity × confidence</span>
        <span style={{ flex: 1 }} />
        <button className="btn small" disabled={ws.busy} onClick={ws.rescan}>Re-scan</button>
      </div>
      {ws.rechecking && (
        <div className="callout info" style={{ margin: "0 0 10px" }}>Re-checking the project after the run… the list below is from before it.</div>
      )}
      {findings.length === 0 && !ws.rechecking && <div className="empty">No problems found ✓</div>}
      {findings.map((f) => {
        const target = targetFor(f, m);
        const crash = f.check === "runtime_crash" ? (f.evidence.log_tail as string | undefined) : undefined;
        return (
          <div
            key={f.id}
            className={`card${target ? " clickable" : ""}`}
            onClick={() => target && ws.openFile(target.path, target.key)}
            title={target ? `Open ${target.path}` : undefined}
          >
            <div className="row">
              <span className="rank">{f.rank}</span>
              <strong>{CHECK_TITLE[f.check] ?? f.check}</strong>
              <span className={`badge ${f.severity}`}>{f.severity}</span>
              <span className="badge info">{Math.round(f.confidence * 100)}% sure</span>
              <span style={{ flex: 1 }} />
              <span className="muted" title="severity weight (1–4) × confidence">priority {f.priority.toFixed(2)}</span>
            </div>
            <div className="bar"><i style={{ width: `${Math.min(100, (f.priority / 4) * 100)}%` }} /></div>
            <div style={{ marginTop: 6 }}>{f.root_cause}</div>
            {facts(f).map((line) => <div key={line} className="muted" style={{ marginTop: 2 }}>{line}</div>)}
            <div className="row" style={{ marginTop: 6 }}>
              <span className={`badge ${f.fix_available ? "good" : "warn"}`}>
                {f.fix_available ? "DevOS can fix this" : "Diagnosis only: no automatic fix"}
              </span>
              {target && <span className="muted">Click to open <code>{target.path}</code></span>}
            </div>
            {crash && (
              <details onClick={(e) => e.stopPropagation()}>
                <summary className="muted">Crash output</summary>
                <pre className="crash">{crash}</pre>
              </details>
            )}
          </div>
        );
      })}
      {canPlan && (
        <button className="btn primary" disabled={ws.busy} onClick={ws.generatePlan}>Generate recovery plan</button>
      )}
      {findings.length > 0 && !findings.some((f) => f.fix_available) && (
        <div className="callout" style={{ margin: 0 }}>None of these can be fixed automatically. The details above show where to look.</div>
      )}
    </div>
  );
}

export function PlanPanel({ ws }: { ws: Workspace }) {
  const { plan, preview } = ws;
  if (!plan) return <div className="empty">No plan yet. Go to Findings and generate one.</div>;
  const usesNetwork = preview.some((p) => p.needs_network);
  const irreversible = preview.filter((p) => p.warning);
  return (
    <div className="pad">
      <div className="row" style={{ marginBottom: 10 }}>
        <strong>Recovery plan</strong>
        <span className={`badge ${PLAN_CLASS[plan.status] ?? ""}`}>{plan.status.replace("_", " ")}</span>
      </div>
      {plan.actions.map((a, i) => {
        const p = preview[i];
        return (
          <div key={i} className="card clickable" onClick={() => ws.setCenterTab("changes")} title="Show the change">
            <div className="row">
              <span className="rank">{i + 1}</span>
              <strong>{p?.summary || a.action.type}</strong>
            </div>
            <div className="row" style={{ marginTop: 6 }}>
              <span className={`badge ${a.risk}`}>{a.risk} risk</span>
              <span className={`badge ${a.decision === "block" ? "bad" : a.decision === "allow" ? "good" : "warn"}`}>
                {a.decision === "require_approval" ? "needs approval" : a.decision}
              </span>
              {p?.needs_network && <span className="badge warn" title="This step runs with network access">uses the network</span>}
              {p?.warning && <span className="badge bad" title={p.warning}>can’t be undone</span>}
            </div>
            <div style={{ marginTop: 6 }}>{a.action.rationale}</div>
            {a.review && (
              <div style={{ marginTop: 6 }}>
                <span className={`badge ${a.review.verdict === "doubt" ? "warn" : "good"}`} title={a.review.checks.join("\n")}>
                  {a.review.verdict === "doubt" ? "AI doubts this" : "AI agrees"}
                </span>{" "}
                {a.review.verified && <span className="badge good" title={a.review.checks.join("\n")}>checked by rules</span>}
                {a.review.ai_note && <div className="muted" style={{ marginTop: 4 }}>AI review: {a.review.ai_note}</div>}
              </div>
            )}
          </div>
        );
      })}
      {plan.status === "proposed" ? (
        <>
          {usesNetwork && (
            <div className="callout" style={{ margin: "0 0 10px" }}>
              Some steps need the network: installing packages downloads them from the package registry, and a migration connects to your database. The sandbox has no network otherwise.
            </div>
          )}
          {irreversible.length > 0 && (
            <div className="callout bad" style={{ margin: "0 0 10px" }}>
              <strong>Not everything can be rolled back.</strong> Files are restored if a check fails, but:
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {irreversible.map((p) => <li key={p.index}>{p.warning}</li>)}
              </ul>
            </div>
          )}
          <div className="callout info" style={{ margin: "0 0 10px" }}>
            Nothing runs until you approve. Approved steps run in order, then everything is verified. If the checks fail, your files are restored.
          </div>
          <div className="row">
            <button className="btn primary" disabled={ws.busy} onClick={ws.approveAndRun}>Approve &amp; run</button>
            <button className="btn danger" disabled={ws.busy} onClick={ws.reject}>Reject</button>
          </div>
        </>
      ) : (
        <div className="muted">Plan is {plan.status.replace("_", " ")}.</div>
      )}
    </div>
  );
}

type Mark = "ok" | "no" | "warn" | "skip";
const ICON: Record<Mark, string> = { ok: "✓", no: "✗", warn: "!", skip: "–" };

/** The verification layers one by one, so “VERIFIED” is never a bare claim. */
function VerificationDetail({ v }: { v: Verification }) {
  const r = v.results;
  if (r.error) return <div className="callout" style={{ margin: "10px 0 0" }}>Could not verify: {r.error}</div>;
  const rows: { mark: Mark; label: string; detail: string }[] = [];

  const up = r.startup === "ok" && r.health?.status === 200;
  rows.push({ mark: r.startup === "not_run" ? "skip" : up ? "ok" : "no", label: "Starts and answers", detail: r.startup === "not_run" ? "no start script" : up ? "answered /health" : `startup ${r.startup ?? "unknown"}` });
  const step = (name: string, s?: { status: string }) =>
    rows.push({ mark: !s || s.status === "skipped" ? "skip" : s.status === "passed" ? "ok" : name === "Build" ? "no" : "warn", label: name, detail: !s || s.status === "skipped" ? "no script" : s.status });
  step("Build", r.build);
  step("Tests", r.tests);
  const errs = r.log_errors?.length ?? 0;
  rows.push({ mark: errs ? "warn" : "ok", label: "Log review", detail: errs ? `${errs} error line${errs > 1 ? "s" : ""}` : "clean" });
  if (r.recheck) {
    const left = r.recheck.remaining.length;
    rows.push({ mark: left ? "no" : "ok", label: "Problems fixed", detail: left ? `${left} still there` : `all ${r.recheck.targeted.length} targeted gone` });
  } else if (r.recheck_error) {
    rows.push({ mark: "warn", label: "Problems fixed", detail: "re-check failed to run" });
  }

  return (
    <div style={{ marginTop: 10 }}>
      <h3>Verification</h3>
      <ul className="checklist">
        {rows.map((x) => (
          <li key={x.label}>
            <span className={`tick ${x.mark}`}>{ICON[x.mark]}</span>
            <strong>{x.label}</strong>
            <span className="muted">{x.detail}</span>
          </li>
        ))}
      </ul>
      {(r.recheck?.remaining ?? []).map((p) => <div key={p.root_cause} className="callout bad" style={{ margin: "6px 0 0" }}>Still present: {p.root_cause}</div>)}
      {(r.recheck?.other ?? []).length > 0 && (
        <div className="callout" style={{ margin: "6px 0 0" }}>
          Other problems remain that this plan did not target:
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{r.recheck!.other.map((p) => <li key={p.root_cause}>{p.root_cause}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

export function RunPanel({ ws }: { ws: Workspace }) {
  const { execution, verification, plan } = ws;
  const logEnd = useRef<HTMLDivElement>(null);
  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: "end" });
  }, [execution?.log.length]);

  if (!execution) return <div className="empty">Nothing has run yet. Approve a plan to start.</div>;
  return (
    <div className="pad">
      <div className="row" style={{ marginBottom: 10 }}>
        <strong>Execution</strong>
        {/* "succeeded" only means the steps were applied; the verdict says whether that was good enough. */}
        <span className={`badge ${execution.status === "failed" ? "bad" : execution.status === "succeeded" ? "good" : "warn"}`}>
          {{ succeeded: "steps applied", failed: "step failed", running: "running", pending: "starting" }[execution.status]}
        </span>
        {verification && (
          <span className={`badge verdict ${VERDICT_CLASS[verification.verdict]}`}>{verification.verdict}</span>
        )}
      </div>
      <div className="log">
        {execution.log.length === 0 && <div className="muted">starting…</div>}
        {execution.log.map((l, i) => (
          <div key={i} className={l.message.startsWith("NOT undone") || l.message.startsWith("ROLLBACK FAILED") ? "warnline" : ""}>
            <span className="stage">[{l.stage}]</span> {l.message}
          </div>
        ))}
        <div ref={logEnd} />
      </div>
      {verification && <VerificationDetail v={verification} />}
      {plan?.status === "rolled_back" && (
        <div className="callout bad" style={{ margin: "10px 0 0" }}>Files were rolled back to the recovery point taken before the run. Anything listed as “NOT undone” above stays as it is.</div>
      )}
      {plan?.status === "completed" && (
        <div className="callout info" style={{ margin: "10px 0 0" }}>The project was changed and verified. Open the file explorer to see the result.</div>
      )}
      {plan && plan.status !== "executing" && (
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn small" disabled={ws.busy} onClick={ws.reverify}>Re-verify</button>
          <button className="btn small danger" disabled={ws.busy || plan.status === "rolled_back"} onClick={ws.rollback}>Roll back files</button>
        </div>
      )}
    </div>
  );
}

export function AuditPanel({ ws }: { ws: Workspace }) {
  if (ws.audit.length === 0) return <div className="empty">No activity yet.</div>;
  return (
    <div className="pad">
      <ul className="timeline">
        {ws.audit.map((e) => (
          <li key={e.id}>
            <div className="row">
              <strong>{e.type.replace(/_/g, " ")}</strong>
              <span className="badge">{e.actor}</span>
              <span className="muted">{new Date(e.created_at).toLocaleTimeString()}</span>
            </div>
            <details>
              <summary className="muted">details</summary>
              <pre>{JSON.stringify(e.payload, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
