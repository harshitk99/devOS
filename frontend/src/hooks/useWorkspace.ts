import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  AuditEvent,
  Diagnostic,
  Execution,
  FileList,
  Plan,
  PreviewItem,
  Project,
  Verification,
} from "../api/types";
import { useProjectStream } from "./useProjectStream";

export type RightTab = "overview" | "findings" | "plan" | "run" | "audit" | "live";
export type CenterTab = "file" | "changes";

const post = <T,>(path: string) => api<T>(path, { method: "POST" });

/** A plan still in play (as opposed to one whose outcome is already in the past). */
export const isActivePlan = (plan: Plan | null) =>
  !!plan && ["proposed", "approved", "executing"].includes(plan.status);

/** Where the reader's attention is: which file, and which variable in it to point at. */
export interface Focus {
  path: string | null;
  key: string | null;
}

/**
 * All server state for the open project plus the actions that change it. The websocket only tells
 * us *when* to re-read; the REST API is always the source of truth, so a dropped socket can't
 * leave the screen stale.
 */
export function useWorkspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [files, setFiles] = useState<FileList>({ files: [], truncated: false });
  const [findings, setFindings] = useState<Diagnostic[] | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);

  const [focus, setFocus] = useState<Focus>({ path: null, key: null });
  const [centerTab, setCenterTab] = useState<CenterTab>("file");
  const [rightTab, setRightTab] = useState<RightTab>("overview");
  const [reveal, setReveal] = useState(false);
  const [fileVersion, setFileVersion] = useState(0); // bump to make the viewer re-fetch

  const [busy, setBusy] = useState(false);
  const [rechecking, setRechecking] = useState(false); // the automatic re-scan after a run is in progress
  const [error, setError] = useState<string | null>(null);

  const stream = useProjectStream(project?.id ?? null);
  const projectId = project?.id ?? null;
  const selecting = useRef(0); // guards against out-of-order responses when switching projects
  const prevStatus = useRef<string | null>(null); // last seen plan status, to spot "run just ended"

  const guard = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const loadProjects = useCallback(async () => {
    setProjects(await api<Project[]>("/projects"));
  }, []);

  const loadRun = useCallback(async (planId: string) => {
    const executions = await api<Execution[]>(`/plans/${planId}/executions`);
    const latest = executions[0] ?? null;
    setExecution(latest);
    if (latest) {
      const v = await api<Verification[]>(`/executions/${latest.id}/verifications`);
      setVerification(v.at(-1) ?? null);
    } else {
      setVerification(null);
    }
  }, []);

  const loadFiles = useCallback(async (id: string) => {
    setFiles(await api<FileList>(`/projects/${id}/files`));
    setFileVersion((v) => v + 1);
  }, []);

  /** Load everything known about a project (used on open and after each change). */
  const selectProject = useCallback(
    async (p: Project) => {
      const ticket = ++selecting.current;
      setProject(p);
      setPlan(null);
      setPreview([]);
      setExecution(null);
      setVerification(null);
      setFindings(null);
      const [fl, diags, plans, events] = await Promise.all([
        api<FileList>(`/projects/${p.id}/files`),
        api<Diagnostic[]>(`/projects/${p.id}/diagnostics`),
        api<Plan[]>(`/projects/${p.id}/plans`),
        api<AuditEvent[]>(`/projects/${p.id}/audit`),
      ]);
      if (ticket !== selecting.current) return;
      setFiles(fl);
      setFindings(diags.length ? diags : null);
      setAudit(events);
      setFocus({ path: fl.files.find((f) => f.path === "package.json")?.path ?? fl.files[0]?.path ?? null, key: null });
      setCenterTab("file");
      setRightTab("overview");
      // Show the latest plan only while it is still in play, or when it is the result of a run that
      // left the project healthy. A finished plan from an earlier session must not hide a new problem.
      const latest = plans[0] && (isActivePlan(plans[0]) || diags.length === 0) ? plans[0] : null;
      setPlan(latest);
      prevStatus.current = latest?.status ?? null;
      if (latest) {
        if (isActivePlan(latest)) setPreview(await api<PreviewItem[]>(`/plans/${latest.id}/preview`));
        await loadRun(latest.id);
      }
      setFileVersion((v) => v + 1);
    },
    [loadRun],
  );

  useEffect(() => {
    loadProjects().catch((e) => setError(String(e)));
  }, [loadProjects]);

  /** Re-read the parts that change while a plan runs. */
  const refresh = useCallback(async () => {
    if (!projectId) return;
    setAudit(await api<AuditEvent[]>(`/projects/${projectId}/audit`));
    if (plan) {
      const fresh = await api<Plan>(`/plans/${plan.id}`);
      // Detect the moment a run ends from the status change itself (the websocket replays old
      // history on reconnect, so messages alone would re-trigger this).
      const justFinished = prevStatus.current === "executing" && fresh.status !== "executing";
      prevStatus.current = fresh.status;
      setPlan(fresh);
      await loadRun(plan.id);
      if (justFinished) {
        setPreview([]); // the change was applied or undone; the "proposed" diff no longer applies
        setRechecking(true); // the re-scan starts the app in Docker, so it takes a few seconds
        try {
          await loadFiles(projectId); // the sandbox changed files on disk
          const diags = await post<Diagnostic[]>(`/projects/${projectId}/diagnose`); // re-check health
          setFindings(diags.length ? diags : null);
          setAudit(await api<AuditEvent[]>(`/projects/${projectId}/audit`));
        } finally {
          setRechecking(false);
        }
      }
    }
  }, [projectId, plan?.id, loadRun, loadFiles]); // eslint-disable-line react-hooks/exhaustive-deps

  const lastMessage = stream.at(-1);
  useEffect(() => {
    if (!lastMessage) return;
    refresh().catch(() => {});
  }, [stream.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (plan?.status !== "executing") return;
    const timer = setInterval(() => refresh().catch(() => {}), 2000); // fallback if the socket drops
    return () => clearInterval(timer);
  }, [plan?.status, refresh]);

  // --- actions ---

  const importProject = (path: string) =>
    guard(async () => {
      const p = await api<Project>("/projects", { method: "POST", body: JSON.stringify({ path }) });
      await post<Diagnostic[]>(`/projects/${p.id}/diagnose`);
      await loadProjects();
      await selectProject(p);
      setRightTab("findings");
    });

  const openProject = (id: string) =>
    guard(async () => {
      const p = projects.find((x) => x.id === id);
      if (p) await selectProject(p);
    });

  const rescan = () =>
    guard(async () => {
      if (!project) return;
      const p = await api<Project>(`/projects/${project.id}`);
      const diags = await post<Diagnostic[]>(`/projects/${project.id}/diagnose`);
      setProject(p);
      setFindings(diags.length ? diags : null);
      if (diags.length && plan && !isActivePlan(plan)) {
        // New problems after a finished attempt: start a fresh cycle.
        setPlan(null);
        setPreview([]);
        setExecution(null);
        setVerification(null);
        prevStatus.current = null;
      }
      await loadFiles(project.id);
      setAudit(await api<AuditEvent[]>(`/projects/${project.id}/audit`));
    });

  const generatePlan = () =>
    guard(async () => {
      if (!project) return;
      const p = await post<Plan>(`/projects/${project.id}/plans`);
      setPlan(p);
      setExecution(null);
      setVerification(null);
      const items = await api<PreviewItem[]>(`/plans/${p.id}/preview`);
      setPreview(items);
      setRightTab("plan");
      setCenterTab("changes");
      setAudit(await api<AuditEvent[]>(`/projects/${project.id}/audit`));
    });

  const approveAndRun = () =>
    guard(async () => {
      if (!plan) return;
      await post<Plan>(`/plans/${plan.id}/approve`);
      const e = await post<Execution>(`/plans/${plan.id}/execute`);
      setExecution(e);
      setVerification(null);
      const running = await api<Plan>(`/plans/${plan.id}`);
      prevStatus.current = "executing"; // so the end of this run is noticed even if it is instant
      setPlan(running);
      setRightTab("run");
    });

  const reject = () =>
    guard(async () => {
      if (plan) setPlan(await post<Plan>(`/plans/${plan.id}/reject`));
      setPreview([]);
      await refresh();
    });

  const rollback = () =>
    guard(async () => {
      if (!execution || !projectId) return;
      await post<Execution>(`/executions/${execution.id}/rollback`);
      await refresh();
      await loadFiles(projectId);
    });

  const reverify = () =>
    guard(async () => {
      if (!execution || !projectId) return;
      await post<Verification>(`/executions/${execution.id}/verify`);
      await refresh();
      await loadFiles(projectId);
    });

  /** Point the centre pane at a file (and optionally at one variable in it). */
  const openFile = (path: string, key: string | null = null) => {
    setFocus({ path, key });
    setCenterTab("file");
  };

  return {
    projects, project, files, findings, plan, preview, execution, verification, audit,
    focus, centerTab, rightTab, reveal, fileVersion, busy, rechecking, error,
    setCenterTab, setRightTab, setReveal, setError,
    importProject, openProject, rescan, generatePlan, approveAndRun, reject, rollback, reverify, openFile,
  };
}

export type Workspace = ReturnType<typeof useWorkspace>;
