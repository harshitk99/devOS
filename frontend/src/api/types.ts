// Mirrors backend/app/domain/enums.py, actions.py and schemas/api.py. Keep in sync.

export type Severity = "low" | "medium" | "high" | "critical";
export type RiskLevel = "low" | "medium" | "high";
export type PolicyDecision = "allow" | "require_approval" | "block";
export type Verdict = "VERIFIED" | "PARTIALLY VERIFIED" | "FAILED" | "NOT TESTED";
export type PlanStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "executing"
  | "completed"
  | "rolled_back";

export interface ProposedAction {
  type: string;
  params: Record<string, unknown>;
  rationale: string;
}

// The AI's opinion of a rule-made step, after the rules cross-checked it. Advisory; absent when no review ran.
export interface StepReview {
  verified: boolean;
  checks: string[];
  verdict: "agree" | "doubt";
  ai_note: string;
  ai_score: number;
  rank: number;
}

export interface ClassifiedAction {
  action: ProposedAction;
  risk: RiskLevel;
  decision: PolicyDecision;
  reason: string;
  review?: StepReview | null;
}

export interface Project {
  id: string;
  name: string;
  path: string;
  manifest: Record<string, unknown>;
}

export interface Diagnostic {
  id: string;
  check: string;
  severity: Severity;
  confidence: number;
  root_cause: string;
  evidence: Record<string, unknown>;
  /** severity weight x confidence; `rank` 1 = fix first */
  priority: number;
  rank: number;
  /** false = diagnosis only: DevOS cannot propose an automatic fix */
  fix_available: boolean;
}

export interface FileEntry {
  path: string;
  size: number;
}

export interface FileList {
  files: FileEntry[];
  truncated: boolean;
}

export interface FileContent {
  path: string;
  size: number;
  binary: boolean;
  truncated: boolean;
  /** true when .env values were hidden */
  masked: boolean;
  content: string;
}

export interface FileWriteIn {
  path: string;
  content: string;
}

export interface DiffLine {
  op: "same" | "add" | "del";
  text: string;
}

export interface PreviewItem {
  index: number;
  type: string;
  path: string | null;
  created: boolean;
  diff: DiffLine[];
  summary: string;
  needs_network: boolean;
  /** what a rollback will NOT undo */
  warning: string | null;
}

export interface RecheckProblem {
  check: string;
  root_cause: string;
}

/** What the verification engine measured (see backend services/verification.py). */
export interface VerificationResults {
  startup?: string;
  health?: { status: number | null } | null;
  build?: { status: "passed" | "failed" | "skipped"; log?: string };
  tests?: { status: "passed" | "failed" | "skipped"; log?: string };
  log_errors?: string[];
  recheck?: { targeted: string[]; remaining: RecheckProblem[]; other: RecheckProblem[] };
  recheck_error?: string;
  error?: string;
}

export interface ListeningPort {
  port: number;
  address: string;
  pid: number | null;
  process: string | null;
  relevance: "project" | "dev" | "system";
  project: string | null;
}

export interface ContainerInfo {
  name: string;
  image: string;
  status: string;
  ports: string[];
  service: string | null;
  compose_project: string | null;
  project: string | null;
}

export interface MonitorSnapshot {
  generated_at: string;
  ports: ListeningPort[];
  project_processes: { pid: number; name: string; command: string; project: string }[];
  docker: { available: boolean; error: string | null; items: ContainerInfo[] };
}

export interface Plan {
  id: string;
  project_id: string;
  status: PlanStatus;
  actions: ClassifiedAction[];
}

export interface LogEntry {
  ts: string;
  stage: string;
  message: string;
}

export interface Execution {
  id: string;
  plan_id: string;
  status: "pending" | "running" | "succeeded" | "failed";
  recovery_point: string | null;
  log: LogEntry[];
}

export interface Verification {
  id: string;
  execution_id: string;
  verdict: Verdict;
  results: VerificationResults;
}

export interface AuditEvent {
  id: string;
  type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
}

/** Push notification from /ws/projects/{id}. State is always re-read through the REST API. */
export interface StreamMessage {
  kind: "log" | "done";
  execution_id: string;
  [key: string]: unknown;
}
