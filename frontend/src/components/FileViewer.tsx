import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { FileContent } from "../api/types";

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

  const lines = data ? data.content.replace(/\n$/, "").split("\n") : [];
  const hl = data && focusKey ? lines.findIndex((l) => new RegExp(`^\\s*${escapeRe(focusKey)}\\s*=`).test(l)) : -1;

  useEffect(() => {
    hlRef.current?.scrollIntoView({ block: "center" });
  }, [hl, data?.path, data?.content]);

  const template = path.endsWith(".env") ? path.replace(/\.env$/, ".env.example") : null;
  const isEnv = data !== null && (data.masked || reveal);

  return (
    <>
      <div className="viewer-head">
        <span className="path">{path}</span>
        {data && <span className="muted">{data.size.toLocaleString()} bytes</span>}
        {isEnv && (
          <button className="btn small" onClick={() => onReveal(!reveal)}>
            {reveal ? "Hide values" : "Reveal values"}
          </button>
        )}
      </div>
      <div className="pane-body">
        {error && <div className="callout bad">{error}</div>}
        {data?.masked && <div className="callout info">Values in this file are hidden. Use “Reveal values” to show them.</div>}
        {data?.truncated && <div className="callout">File is large; showing only the first part.</div>}
        {data && focusKey && hl === -1 && !data.binary && (
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
        {data && !data.binary && (
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
