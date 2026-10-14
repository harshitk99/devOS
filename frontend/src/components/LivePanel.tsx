import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MonitorSnapshot } from "../api/types";
import type { Workspace } from "../hooks/useWorkspace";

/** What is running on this computer right now: who holds which port, and which containers are up. */
export function LivePanel({ ws }: { ws: Workspace }) {
  const [snap, setSnap] = useState<MonitorSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSystem, setShowSystem] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<MonitorSnapshot>("/monitor")
        .then((s) => alive && (setSnap(s), setError(null)))
        .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    load();
    const timer = setInterval(load, 3000); // refreshes only while this tab is open
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const manifest = (ws.project?.manifest ?? {}) as { config?: { port?: number | null } };
  const appPort = manifest.config?.port ?? null;

  if (error) return <div className="callout bad" style={{ margin: 12 }}>Could not read the live state: {error}</div>;
  if (!snap) return <div className="empty">Reading what is running…</div>;

  const ports = snap.ports.filter((p) => showSystem || p.relevance !== "system");
  const hidden = snap.ports.length - ports.length;

  return (
    <div className="pad">
      <div className="row" style={{ marginBottom: 8 }}>
        <span className="muted">Updated {new Date(snap.generated_at).toLocaleTimeString()} · refreshes every 3s</span>
      </div>

      <h3>Listening ports</h3>
      <table className="table">
        <thead>
          <tr><th>Port</th><th>Program</th><th>Belongs to</th></tr>
        </thead>
        <tbody>
          {ports.map((p) => {
            const conflict = appPort === p.port && p.relevance !== "project";
            return (
              <tr key={`${p.port}-${p.pid}`} className={p.relevance === "project" ? "mine" : ""}>
                <td><code>{p.port}</code></td>
                <td>{p.process ?? "unknown"}{p.pid ? <span className="muted"> · {p.pid}</span> : null}</td>
                <td>
                  {p.project ?? <span className="muted">{p.relevance === "dev" ? "dev tool" : "system"}</span>}
                  {conflict && <span className="badge bad" style={{ marginLeft: 6 }} title="This is the port the open project wants">port conflict</span>}
                  {appPort === p.port && p.relevance === "project" && <span className="badge good" style={{ marginLeft: 6 }}>app port</span>}
                </td>
              </tr>
            );
          })}
          {ports.length === 0 && <tr><td colSpan={3} className="muted">Nothing relevant is listening.</td></tr>}
        </tbody>
      </table>
      <label className="muted" style={{ display: "block", marginTop: 6 }}>
        <input type="checkbox" checked={showSystem} onChange={(e) => setShowSystem(e.target.checked)} /> Show system ports
        {!showSystem && hidden > 0 && ` (${hidden} hidden)`}
      </label>
      {appPort !== null && !snap.ports.some((p) => p.port === appPort) && (
        <div className="muted" style={{ marginTop: 6 }}>Port <code>{appPort}</code> (the open project’s) is free.</div>
      )}

      <h3>Docker containers</h3>
      {!snap.docker.available ? (
        <div className="callout" style={{ margin: 0 }}>Docker is not reachable: {snap.docker.error}</div>
      ) : snap.docker.items.length === 0 ? (
        <div className="muted">No containers.</div>
      ) : (
        <table className="table">
          <thead>
            <tr><th>Name</th><th>State</th><th>Ports</th></tr>
          </thead>
          <tbody>
            {snap.docker.items.map((c) => (
              <tr key={c.name} className={c.project ? "mine" : ""}>
                <td>
                  {c.name}
                  <div className="muted">{c.image}{c.service ? ` · service ${c.service}` : ""}</div>
                </td>
                <td><span className={`badge ${c.status === "running" ? "good" : "warn"}`}>{c.status}</span></td>
                <td>{c.ports.length ? c.ports.map((x) => <code key={x} style={{ display: "block" }}>{x}</code>) : <span className="muted">none</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Processes running in your projects</h3>
      {snap.project_processes.length === 0 ? (
        <div className="muted">None.</div>
      ) : (
        <table className="table">
          <thead>
            <tr><th>PID</th><th>Program</th><th>Project</th></tr>
          </thead>
          <tbody>
            {snap.project_processes.map((p) => (
              <tr key={p.pid}>
                <td><code>{p.pid}</code></td>
                <td>{p.name}<div className="muted" style={{ overflowWrap: "anywhere" }}>{p.command}</div></td>
                <td>{p.project}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
