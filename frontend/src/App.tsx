import { useMemo, useState } from "react";
import { DiffView } from "./components/DiffView";
import { FileTree, type Flag } from "./components/FileTree";
import { FileViewer } from "./components/FileViewer";
import { LivePanel } from "./components/LivePanel";
import { AuditPanel, Findings, Overview, PlanPanel, RunPanel } from "./components/Panels";
import { Stepper } from "./components/Stepper";
import { useWorkspace, type RightTab } from "./hooks/useWorkspace";

const RIGHT_TABS: { id: RightTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "findings", label: "Findings" },
  { id: "plan", label: "Plan" },
  { id: "run", label: "Run" },
  { id: "audit", label: "Audit" },
  { id: "live", label: "Live" },
];

export default function App() {
  const ws = useWorkspace();
  const [path, setPath] = useState("");
  const [selectedStep, setSelectedStep] = useState<number | null>(null);

  // Files to mark in the tree: ones involved in findings, and ones the plan would change.
  const flags = useMemo(() => {
    const out: Record<string, Flag> = {};
    for (const f of ws.findings ?? []) {
      if (typeof f.evidence.env_file === "string") out[f.evidence.env_file] = "finding";
    }
    if (ws.plan?.status === "proposed" || ws.plan?.status === "approved" || ws.plan?.status === "executing") {
      for (const item of ws.preview) if (item.path) out[item.path] = "change";
    }
    return out;
  }, [ws.findings, ws.plan?.status, ws.preview]);

  const counts: Partial<Record<RightTab, number>> = {
    findings: ws.findings?.length ?? 0,
    plan: ws.plan?.actions.length ?? 0,
    audit: ws.audit.length,
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>DevOS</h1>
        <select
          value={ws.project?.id ?? ""}
          onChange={(e) => e.target.value && ws.openProject(e.target.value)}
          aria-label="Open project"
        >
          <option value="">{ws.projects.length ? "Open a project…" : "No projects yet"}</option>
          {ws.projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name} — {p.path}</option>
          ))}
        </select>
        <span className="spacer" />
        <input
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && path && ws.importProject(path)}
          placeholder="Absolute path to a project folder, e.g. C:\code\demo-app\backend"
          aria-label="Project path"
        />
        <button className="btn primary" disabled={ws.busy || !path} onClick={() => ws.importProject(path)}>
          Import &amp; diagnose
        </button>
      </header>

      {ws.error && (
        <div className="banner" role="alert">
          <span>{ws.error}</span>
          <button onClick={() => ws.setError(null)} aria-label="Dismiss">✕</button>
        </div>
      )}

      <Stepper ws={ws} />

      <main className="workspace">
        <section className="pane" aria-label="Files">
          <div className="pane-head">
            Files
            {ws.files.truncated && <span className="badge warn">list truncated</span>}
          </div>
          <div className="pane-body">
            {ws.project ? (
              <FileTree files={ws.files.files} selected={ws.focus.path} flags={flags} onSelect={(p) => ws.openFile(p)} />
            ) : (
              <div className="empty">Import a project to browse its code.</div>
            )}
          </div>
        </section>

        <section className="pane" aria-label="Viewer">
          <div className="tabs">
            <button className={`tab${ws.centerTab === "file" ? " on" : ""}`} onClick={() => ws.setCenterTab("file")}>
              File
            </button>
            <button className={`tab${ws.centerTab === "changes" ? " on" : ""}`} onClick={() => ws.setCenterTab("changes")}>
              Proposed changes{ws.preview.length > 0 && <span className="count">{ws.preview.length}</span>}
            </button>
          </div>
          {!ws.project && (
            <div className="empty">
              <p><strong>Welcome to DevOS.</strong></p>
              <p>Import a project folder above. DevOS maps it, ranks what is broken, proposes a fix, and only changes anything after you approve.</p>
              <p className="muted">Try the demo: run <code>python scripts/inject_failure.py missing-env</code>, then import <code>demo-app/backend</code>.</p>
            </div>
          )}
          {ws.project && ws.centerTab === "file" &&
            (ws.focus.path ? (
              <FileViewer
                projectId={ws.project.id}
                path={ws.focus.path}
                focusKey={ws.focus.key}
                reveal={ws.reveal}
                onReveal={ws.setReveal}
                version={ws.fileVersion}
                onOpen={ws.openFile}
              />
            ) : (
              <div className="empty">Select a file on the left.</div>
            ))}
          {ws.project && ws.centerTab === "changes" && (
            <DiffView
              items={ws.preview}
              actions={ws.plan?.actions ?? []}
              planStatus={ws.plan?.status ?? null}
              selected={selectedStep}
              onSelect={setSelectedStep}
            />
          )}
        </section>

        <section className="pane" aria-label="Details">
          <div className="tabs" role="tablist">
            {RIGHT_TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={ws.rightTab === t.id}
                className={`tab${ws.rightTab === t.id ? " on" : ""}`}
                onClick={() => ws.setRightTab(t.id)}
              >
                {t.label}
                {counts[t.id] ? <span className="count">{counts[t.id]}</span> : null}
              </button>
            ))}
          </div>
          <div className="pane-body">
            {!ws.project ? (
              <div className="empty">Nothing open yet.</div>
            ) : (
              <>
                {ws.rightTab === "overview" && <Overview ws={ws} />}
                {ws.rightTab === "findings" && <Findings ws={ws} />}
                {ws.rightTab === "plan" && <PlanPanel ws={ws} />}
                {ws.rightTab === "run" && <RunPanel ws={ws} />}
                {ws.rightTab === "audit" && <AuditPanel ws={ws} />}
                {ws.rightTab === "live" && <LivePanel ws={ws} />}
              </>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
