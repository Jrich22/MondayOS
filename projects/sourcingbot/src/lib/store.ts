/**
 * sourcingBOT store — the single seam every surface reads and writes through.
 *
 * Mirrors Cue's `lib/store.ts`: no backend in this increment, so "persisted"
 * means localStorage behind a `useSyncExternalStore` subscription. The four
 * collections are kept SEPARATE and normalized — candidates are not nested
 * inside reqs, and reqCandidates hold only ids — so the persistent-person model
 * survives serialization rather than being flattened on save.
 *
 * Swapping localStorage for a real backend later means reimplementing `load`
 * and `persist`; no surface imports storage directly.
 */
import { useSyncExternalStore } from "react";
import type {
  Candidate,
  Req,
  ReqCandidate,
  SourcingBrief,
  SourcingSession,
} from "./types";
import { isAlreadyOnReq } from "./req-candidate";
import { seedState } from "./seed";

const STORAGE_KEY = "sourcingbot.workspace.v1";

export interface WorkspaceState {
  reqs: Req[];
  briefs: SourcingBrief[];
  candidates: Candidate[];
  reqCandidates: ReqCandidate[];
  sessions: SourcingSession[];
}

const EMPTY: WorkspaceState = {
  reqs: [],
  briefs: [],
  candidates: [],
  reqCandidates: [],
  sessions: [],
};

let state: WorkspaceState = load();
let snapshot: WorkspaceState = state;
const listeners = new Set<() => void>();

function load(): WorkspaceState {
  if (typeof localStorage === "undefined") return seedState();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return seedState();
    const parsed = JSON.parse(raw) as Partial<WorkspaceState>;
    if (!parsed || typeof parsed !== "object") return seedState();
    return {
      reqs: parsed.reqs ?? [],
      briefs: parsed.briefs ?? [],
      candidates: parsed.candidates ?? [],
      reqCandidates: parsed.reqCandidates ?? [],
      sessions: parsed.sessions ?? [],
    };
  } catch {
    return seedState();
  }
}

function persist(): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage full or blocked — the in-memory copy still works this session */
  }
}

function emit(): void {
  snapshot = { ...state };
  persist();
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): WorkspaceState {
  return snapshot;
}

export function useWorkspace(): WorkspaceState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function getState(): WorkspaceState {
  return state;
}

// ---------------------------------------------------------------------------
// Reqs
// ---------------------------------------------------------------------------

export function addReq(req: Req): void {
  state = { ...state, reqs: [...state.reqs, req] };
  emit();
}

export function updateReq(req: Req): void {
  state = { ...state, reqs: state.reqs.map((r) => (r.id === req.id ? req : r)) };
  emit();
}

export function getReq(reqId: string): Req | undefined {
  return state.reqs.find((r) => r.id === reqId);
}

// ---------------------------------------------------------------------------
// Briefs
// ---------------------------------------------------------------------------

export function addBrief(brief: SourcingBrief): void {
  state = { ...state, briefs: [...state.briefs, brief] };
  emit();
}

export function updateBrief(brief: SourcingBrief): void {
  state = { ...state, briefs: state.briefs.map((b) => (b.id === brief.id ? brief : b)) };
  emit();
}

export function briefForReq(reqId: string): SourcingBrief | undefined {
  return state.briefs.find((b) => b.reqId === reqId);
}

// ---------------------------------------------------------------------------
// Candidates (persistent people)
// ---------------------------------------------------------------------------

export function addCandidate(candidate: Candidate): void {
  state = { ...state, candidates: [...state.candidates, candidate] };
  emit();
}

export function updateCandidateRecord(candidate: Candidate): void {
  state = {
    ...state,
    candidates: state.candidates.map((c) => (c.id === candidate.id ? candidate : c)),
  };
  emit();
}

export function getCandidate(candidateId: string): Candidate | undefined {
  return state.candidates.find((c) => c.id === candidateId);
}

// ---------------------------------------------------------------------------
// ReqCandidates (req-scoped evaluations)
// ---------------------------------------------------------------------------

export class DuplicateReqCandidateError extends Error {
  constructor() {
    super("This person is already on this requisition.");
    this.name = "DuplicateReqCandidateError";
  }
}

/**
 * Attach an existing person to a requisition.
 *
 * Refuses a second row for the same person+req: duplicates would split one
 * evaluation across two records and corrupt the pipeline counts.
 */
export function addReqCandidate(rc: ReqCandidate): void {
  if (isAlreadyOnReq(rc.candidateId, rc.reqId, state.reqCandidates)) {
    throw new DuplicateReqCandidateError();
  }
  state = { ...state, reqCandidates: [...state.reqCandidates, rc] };
  emit();
}

export function updateReqCandidate(rc: ReqCandidate): void {
  state = {
    ...state,
    reqCandidates: state.reqCandidates.map((x) => (x.id === rc.id ? rc : x)),
  };
  emit();
}

// ---------------------------------------------------------------------------
// Supervised sessions
// ---------------------------------------------------------------------------

export function addSession(session: SourcingSession): void {
  state = { ...state, sessions: [...state.sessions, session] };
  emit();
}

export function updateSession(session: SourcingSession): void {
  state = {
    ...state,
    sessions: state.sessions.map((s) => (s.id === session.id ? session : s)),
  };
  emit();
}

// ---------------------------------------------------------------------------
// Test seams
// ---------------------------------------------------------------------------

export function __resetStore(next: Partial<WorkspaceState> = {}): void {
  state = { ...EMPTY, ...next };
  emit();
}

export function __seedStore(): void {
  state = seedState();
  emit();
}
