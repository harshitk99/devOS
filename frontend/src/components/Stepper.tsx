import type { RightTab, Workspace } from "../hooks/useWorkspace";

const STAGES: { name: string; tab: RightTab; hint: string }[] = [
  { name: "Understand", tab: "overview", hint: "Project imported and mapped" },
  { name: "Diagnose", tab: "findings", hint: "Problems found and ranked" },
  { name: "Protect", tab: "plan", hint: "Plan proposed and policy-checked" },
  { name: "Execute", tab: "run", hint: "Approved steps run in the sandbox" },
  { name: "Verify", tab: "run", hint: "Build, startup and health checked" },
  { name: "Recover", tab: "run", hint: "Rolled back if verification failed" },
];

/** Six-stage progress bar. Clicking a stage jumps to the panel for it. */
export function Stepper({ ws }: { ws: Workspace }) {
  const done = [
    !!ws.project,
    ws.findings !== null || (!!ws.project && ws.audit.some((e) => e.type === "diagnosis_run")),
    !!ws.plan,
    !!ws.execution && ws.execution.log.some((l) => l.stage === "execute"),
    !!ws.verification,
    // Recover is finished either way: rolled back, or verified so no rollback was needed.
    ws.plan?.status === "rolled_back" || ws.plan?.status === "completed",
  ];
  const active = done.findIndex((d) => !d);
  return (
    <nav className="stepper" aria-label="Workflow">
      {STAGES.map((s, i) => (
        <button
          key={s.name}
          className={`step${done[i] ? " done" : ""}${i === active ? " active" : ""}${i === 5 && ws.plan?.status === "rolled_back" ? " bad" : ""}`}
          title={s.hint}
          onClick={() => ws.project && ws.setRightTab(s.tab)}
        >
          <span className="dot" />
          {s.name}
        </button>
      ))}
    </nav>
  );
}
