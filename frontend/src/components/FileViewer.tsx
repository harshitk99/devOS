import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { FileContent } from "../api/types";

const EDIT_UNAVAILABLE = "Secret .env files can't be edited here; use a plan's ‘Set env var’ step instead.";

interface Props {
  projectId: string;
  path: string;
  /** Variable to point at: its line is highlighted, or a warning is shown if it is absent. */
  focusKey: string | null;
  reveal: boolean;
  onReveal: (on: boolean) => void;
  /** Change to force a re-fetch (e.g. after the sandbox modified the file). */
  version: number;
  onOpen: (path: string, key?: string | null) => void;
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function FileViewer({ projectId, path, focusKey, reveal, onReveal, version, onOpen }: Props) {
  const [data, setData] = useState<FileContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const hlRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api<FileContent>(`/projects/${projectId}/files/content?path=${encodeURIComponent(path)}&reveal=${reveal}`)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && (setData(null), setError(e instanceof Error ? e.message : String(e))));
    return () => {
      cancelled = true;
    };
  }, [projectId, path, reveal, version]);

  // Leaving the file (or it changing under us) always drops any unsaved edit.
  useEffect(() => setEditing(false), [projectId, path, version]);

  const startEditing = () => {
    if (!data) return;
    setDraft(data.content);
    setEditing(true);
    setError(null);
  };

  const save = () => {
    setSaving(true);
    setError(null);
    api<FileContent>(`/projects/${projectId}/files/content`, {
      method: "PUT",
      body: JSON.stringify({ path, content: draft }),
    })
      .then((d) => {
        setData(d);
        setEditing(false);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false));
  };

  const lines = data ? data.content.replace(/\n$/, "").split("\n") : [];
  const hl = data && focusKey ? lines.findIndex((l) => new RegExp(`^\\s*${escapeRe(focusKey)}\\s*=`).test(l)) : -1;

  useEffect(() => {
    hlRef.current?.scrollIntoView({ block: "center" });
  }, [hl, data?.path, data?.content]);

  const template = path.endsWith(".env") ? path.replace(/\.env$/, ".env.example") : null;
  const isEnv = data !== null && (data.masked || reveal);
  const editable = data !== null && !data.binary && !isEnv;

  return (
    <>
      <div className="viewer-head">
        <span className="path">{path}</span>
        {data && !editing && <span className="muted">{data.size.toLocaleString()} bytes</span>}
        {isEnv && !editing && (
          <button className="btn small" onClick={() => onReveal(!reveal)}>
            {reveal ? "Hide values" : "Reveal values"}
          </button>
        )}
        {data && !data.binary && !editing && (
          <button className="btn small" disabled={!editable} title={editable ? undefined : EDIT_UNAVAILABLE} onClick={startEditing}>
            Edit
          </button>
        )}
        {editing && (
          <>
            <button className="btn small primary" disabled={saving} onClick={save}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button className="btn small" disabled={saving} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </>
        )}
      </div>
      <div className="pane-body">
        {error && <div className="callout bad">{error}</div>}
        {data?.masked && !editing && <div className="callout info">Values in this file are hidden. Use “Reveal values” to show them.</div>}
        {data?.truncated && <div className="callout">File is large; showing only the first part.</div>}
        {data && focusKey && hl === -1 && !data.binary && !editing && (
          <div className="callout bad">
            <strong>{focusKey}</strong> is not set in this file.{" "}
            {template && (
              <button className="linkish" onClick={() => onOpen(template, focusKey)}>
                See its expected default in {template}
              </button>
            )}
          </div>
        )}
        {data?.binary && <div className="empty">Binary file, not shown.</div>}
        {editing && (
          <textarea
            className="editor"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            autoFocus
          />
        )}
        {data && !data.binary && !editing && (
          <pre className="code">
            {lines.map((text, i) => (
              <div key={i} className={`ln${i === hl ? " hl" : ""}`} ref={i === hl ? hlRef : undefined}>
                <span className="no">{i + 1}</span>
                <span className="tx">{text || " "}</span>
              </div>
            ))}
          </pre>
        )}
      </div>
    </>
  );
}
