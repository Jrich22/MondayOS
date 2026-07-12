/**
 * Typed demo dataset for MondayOS Mission Control.
 *
 * Deterministic (no clocks/random at module load) and shaped to exercise every
 * Brain state and workspace. This is the ONLY place demo values live; the demo
 * adapter reads from here and the real adapter never touches it. Presented in
 * the UI under a visible "DEMO DATA" badge — never as live.
 */

import type {
  Agent,
  Approval,
  ActivityEvent,
  KnowledgeItem,
  Product,
  PublishRecord,
  PullRequest,
  SystemStatus,
  Task,
  TeamRun,
} from "./types";

export const SYSTEM: SystemStatus = {
  version: "3.0.0-alpha",
  healthy: true,
  sessionId: "a3f91c",
  uptimeSeconds: 42_930,
  provider: "anthropic",
  model: "claude-opus-4-8",
};

export const PRODUCTS: Product[] = [
  {
    key: "cue",
    name: "Cue App",
    summary: "AI-native event operations for VC firms",
    status: "operational",
    openTasks: 6,
    sprint: { name: "Sprint 2", done: 5, total: 8 },
    metrics: [
      { label: "Tests", value: "231 passing", tone: "good" },
      { label: "Open PRs", value: "1" },
      { label: "Build", value: "passing", tone: "good" },
    ],
    recommendation:
      "Start Vendor Workspace — it completes the core event-operations loop and has no blocking dependencies.",
  },
  {
    key: "storm-edge",
    name: "Storm Edge",
    summary: "Recorder health, data capture & research agent",
    status: "building",
    openTasks: 4,
    sprint: { name: "Sprint 1", done: 2, total: 6 },
    metrics: [
      { label: "Recorders", value: "3 healthy", tone: "good" },
      { label: "Data points", value: "1.2M" },
      { label: "Safety", value: "nominal", tone: "good" },
      { label: "Research", value: "2 open" },
    ],
    recommendation: "Land the recorder-health heartbeat before expanding capture coverage.",
  },
  {
    key: "mondayos",
    name: "MondayOS",
    summary: "The operating system itself — platform, agents, knowledge",
    status: "operational",
    openTasks: 5,
    sprint: { name: "v3.0 Initiative", done: 1, total: 3 },
    metrics: [
      { label: "Agents", value: "8" },
      { label: "Integrations", value: "2 live", tone: "good" },
      { label: "Knowledge", value: "24 items" },
      { label: "Releases", value: "v2.9" },
    ],
    recommendation: "Ship the interactive Brain interface (Phase 1) to unlock in-Brain operation.",
  },
];

export const AGENTS: Agent[] = [
  { id: "orchestrator", name: "Orchestrator", role: "Plans & routes work", activity: "thinking", task: "TASK-0046" },
  { id: "advisor", name: "Advisor", role: "Strategy & review", activity: "idle" },
  { id: "doctor", name: "Doctor", role: "Health & diagnostics", activity: "executing", task: "System sweep" },
  { id: "migrate", name: "Migrate", role: "Schema & data moves", activity: "idle" },
  { id: "brain", name: "Brain", role: "Knowledge & recall", activity: "learning", task: "Indexing DOC-0014" },
  { id: "search", name: "Search", role: "Retrieval", activity: "idle" },
  { id: "events", name: "Events", role: "Event bus", activity: "executing", task: "Dispatching" },
  { id: "publish", name: "Publish", role: "Confluence sync", activity: "awaiting", task: "DEC-0007 approval" },
];

export const TASKS: Task[] = [
  { id: "TASK-0046", title: "Vendor Workspace for Cue", status: "active", product: "cue", agent: "Orchestrator", objective: "Add a vendor-management workspace to Cue's event ops." },
  { id: "TASK-0031", title: "Wire agent telemetry into Mission Control", status: "active", product: "mondayos", agent: "Orchestrator" },
  { id: "TASK-0030", title: "MondayOS dashboard shell", status: "review", product: "mondayos", agent: "Advisor" },
  { id: "TASK-0028", title: "Knowledge auto-indexing pipeline", status: "active", product: "mondayos", agent: "Brain" },
  { id: "TASK-0025", title: "Confluence publish mapping", status: "blocked", product: "mondayos", agent: "Publish", blockedReason: "Missing Confluence space mapping for DEC-0007." },
  { id: "TASK-0022", title: "Doctor: dependency graph audit", status: "active", product: "mondayos", agent: "Doctor" },
  { id: "SE-0007", title: "Recorder-health heartbeat", status: "active", product: "storm-edge" },
  { id: "SE-0005", title: "Capture backpressure guard", status: "blocked", product: "storm-edge", blockedReason: "Awaiting safety review sign-off." },
  { id: "TASK-0041", title: "Cue product onboarding report", status: "completed", product: "cue" },
  { id: "TASK-0037", title: "Agent team runner", status: "completed", product: "mondayos" },
];

export const TEAM_RUNS: TeamRun[] = [
  {
    id: "TR-0012",
    taskId: "TASK-0046",
    mode: "review-required",
    status: "awaiting",
    startedAt: "2026-07-12T15:40:00Z",
    stages: [
      { id: "AR-1", teamRunId: "TR-0012", stage: "CPO", agent: "Advisor", status: "completed", provider: "anthropic", model: "claude-opus-4-8", summary: "Scoped Vendor Workspace: CRUD + assignment + status.", verdict: "pass", elapsedMs: 18400 },
      { id: "AR-2", teamRunId: "TR-0012", stage: "Lead Engineer", agent: "Orchestrator", status: "completed", provider: "anthropic", model: "claude-opus-4-8", summary: "Implemented vendor store, list + detail views.", verdict: "pass", elapsedMs: 40120 },
      { id: "AR-3", teamRunId: "TR-0012", stage: "QA", agent: "Doctor", status: "completed", provider: "anthropic", model: "claude-sonnet-5", summary: "12 tests added, all green.", verdict: "pass", elapsedMs: 15230 },
      { id: "AR-4", teamRunId: "TR-0012", stage: "Security", agent: "Advisor", status: "completed", provider: "anthropic", model: "claude-sonnet-5", summary: "No secrets, no destructive ops. Review-only.", verdict: "pass", elapsedMs: 9110 },
      { id: "AR-5", teamRunId: "TR-0012", stage: "Reviewer", agent: "Advisor", status: "awaiting", summary: "Awaiting human approval — no commits/pushes performed.", elapsedMs: 0 },
    ],
  },
];

export const APPROVALS: Approval[] = [
  {
    id: "AP-0003",
    taskId: "TASK-0046",
    teamRunId: "TR-0012",
    summary: "Vendor Workspace implementation ready for review (review-required mode, no commits).",
    status: "open",
    verdicts: [
      { role: "QA", verdict: "pass", note: "12 tests added, all green." },
      { role: "Security", verdict: "pass", note: "Review-only, no gated actions." },
      { role: "Reviewer", verdict: "concerns", note: "Confirm vendor delete is soft-delete." },
    ],
    affected: ["cue-app/src/pages/Vendors.tsx", "cue-app/src/lib/vendors.ts", "cue-app/src/lib/vendors.test.ts"],
  },
];

export const KNOWLEDGE: KnowledgeItem[] = [
  { id: "DOC-0014", kind: "research", title: "Event-ops competitive landscape", product: "cue", summary: "How incumbents handle check-in + comms." },
  { id: "DEC-0007", kind: "decision", title: "Adopt Confluence for external docs", product: "mondayos", summary: "Publish curated docs to Confluence via the publish agent." },
  { id: "DOC-0011", kind: "doc", title: "Cue check-in kiosk spec", product: "cue" },
  { id: "SPR-0003", kind: "sprint", title: "Cue Sprint 2 plan", product: "cue" },
  { id: "DEC-0004", kind: "decision", title: "Dashboard is the visual client only", product: "mondayos", summary: "No business logic in the dashboard; adapter boundary is mandatory." },
];

export const ACTIVITY: ActivityEvent[] = [
  { id: "a1", at: "just now", agent: "Doctor", message: "Ran system sweep — 0 critical findings", kind: "executing" },
  { id: "a2", at: "1m", agent: "Brain", message: "Learning from DOC-0014 (research)", kind: "learning" },
  { id: "a3", at: "3m", agent: "Reviewer", message: "TR-0012 awaiting human approval", kind: "awaiting" },
  { id: "a4", at: "6m", agent: "Orchestrator", message: "Planned TASK-0046 across 5 stages", kind: "thinking" },
  { id: "a5", at: "9m", agent: "Publish", message: "Blocked: missing Confluence mapping for TASK-0025", kind: "blocked" },
  { id: "a6", at: "12m", agent: "Events", message: "Completed onboarding report for Cue", kind: "completed" },
];

export const PUBLISH_HISTORY: PublishRecord[] = [
  { id: "PB-2", docId: "DOC-0011", target: "Confluence / Cue", at: "yesterday", status: "published" },
  { id: "PB-1", docId: "DEC-0007", target: "Confluence / MondayOS", at: "3d", status: "pending" },
];

export const PULL_REQUESTS: PullRequest[] = [
  { number: 8, title: "MondayOS: Mission Control Dashboard & Living Brain", state: "open", branch: "mondayos-mission-control-dashboard", product: "mondayos" },
  { number: 7, title: "Cue App Sprint 2: QR and Badge Check-In", state: "merged", branch: "cue-sprint-2-qr-badge-checkin", product: "cue" },
];
