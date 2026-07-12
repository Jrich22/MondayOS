/**
 * Dashboard state store.
 *
 * Orchestrates *client* state only — section, transcript, busy/connection flags,
 * and a read cache of adapter snapshots used to render panels and derive the
 * Brain state. It holds NO business logic: commands are parsed by
 * `command/parser` and executed by `command/execute` against the adapter, and
 * writes/approvals go straight through the adapter to MondayOS (the system of
 * record). Phase 2 adds: a live connection status (LIVE / DEGRADED / DEMO), a
 * live refresh loop (SSE with polling fallback), real approve/reject, and Brain
 * state transitions driven by real actions.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import {
  selectAdapter,
  type ActionResult,
  type Agent,
  type Approval,
  type ActivityEvent,
  type DataMode,
  type MondayAdapter,
  type Product,
  type SystemStatus,
  type Task,
} from "@/adapter";
import type { BrainState } from "@/components/monday";
import type { ParsedCommand, Section } from "@/command/intents";
import { classifyCommand } from "@/command/parser";
import { runCommand, type CommandOutcome, type ResultAction } from "@/command/execute";
import { deriveBrainState } from "./brain";
import { deriveConnection, type Connection } from "./connection";

export interface Turn {
  id: string;
  role: "user" | "monday";
  text: string;
  outcome?: CommandOutcome;
}

export type { Connection };

interface Snapshots {
  system?: SystemStatus;
  agents: Agent[];
  tasks: Task[];
  approvals: Approval[];
  products: Product[];
  activity: ActivityEvent[];
}

interface State extends Snapshots {
  mode: DataMode | "loading";
  adapter?: MondayAdapter;
  baseUrl?: string;
  demoReason?: string;
  healthOk: boolean;
  section: Section;
  activeProduct?: string;
  commandOpen: boolean;
  busy: boolean;
  transcript: Turn[];
  brainOverride: BrainState | "auto";
  transient: BrainState | null;
  justCompleted: boolean;
}

type Action =
  | { type: "READY"; mode: DataMode; adapter: MondayAdapter; baseUrl?: string; demoReason?: string; snap: Snapshots }
  | { type: "REFRESH"; snap: Partial<Snapshots> }
  | { type: "HEALTH"; ok: boolean }
  | { type: "OPEN_COMMAND" }
  | { type: "CLOSE_COMMAND" }
  | { type: "SET_SECTION"; section: Section; product?: string }
  | { type: "SET_OVERRIDE"; value: BrainState | "auto" }
  | { type: "SET_TRANSIENT"; value: BrainState | null }
  | { type: "PUSH_TURN"; turn: Turn }
  | { type: "BUSY"; busy: boolean }
  | { type: "COMPLETED"; value: boolean };

const initial: State = {
  mode: "loading",
  healthOk: true,
  section: "home",
  commandOpen: false,
  busy: false,
  transcript: [],
  brainOverride: "auto",
  transient: null,
  justCompleted: false,
  agents: [],
  tasks: [],
  approvals: [],
  products: [],
  activity: [],
};

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "READY":
      return { ...s, mode: a.mode, adapter: a.adapter, baseUrl: a.baseUrl, demoReason: a.demoReason, ...a.snap };
    case "REFRESH":
      return { ...s, ...a.snap };
    case "HEALTH":
      return { ...s, healthOk: a.ok };
    case "OPEN_COMMAND":
      return { ...s, commandOpen: true };
    case "CLOSE_COMMAND":
      return { ...s, commandOpen: false };
    case "SET_SECTION":
      return { ...s, section: a.section, activeProduct: a.product ?? s.activeProduct };
    case "SET_OVERRIDE":
      return { ...s, brainOverride: a.value };
    case "SET_TRANSIENT":
      return { ...s, transient: a.value };
    case "PUSH_TURN":
      return { ...s, transcript: [...s.transcript, a.turn] };
    case "BUSY":
      return { ...s, busy: a.busy };
    case "COMPLETED":
      return { ...s, justCompleted: a.value };
    default:
      return s;
  }
}

interface AppContextValue {
  state: State;
  brainState: BrainState;
  connection: Connection;
  adapter: MondayAdapter | null;
  submitCommand: (text: string) => Promise<void>;
  confirmAction: (parsed: ParsedCommand) => Promise<void>;
  approve: (runId: string) => Promise<ActionResult<Approval>>;
  reject: (runId: string, reason: string) => Promise<ActionResult<Approval>>;
  refresh: () => Promise<void>;
  runAction: (action: ResultAction) => void;
  navigate: (section: Section, product?: string) => void;
  openCommand: () => void;
  closeCommand: () => void;
  setOverride: (value: BrainState | "auto") => void;
}

const AppContext = createContext<AppContextValue | null>(null);

let turnSeq = 0;
const nextTurnId = () => `t${++turnSeq}`;

async function loadSnapshots(adapter: MondayAdapter): Promise<Partial<Snapshots>> {
  const [system, agents, tasks, approvals, products, activity] = await Promise.all([
    adapter.getSystemStatus(),
    adapter.listAgents(),
    adapter.listTasks(),
    adapter.listApprovals(),
    adapter.listProducts(),
    adapter.getRecentActivity(),
  ]);
  return {
    system: system.ok ? system.data : undefined,
    agents: agents.ok ? agents.data : [],
    tasks: tasks.ok ? tasks.data : [],
    approvals: approvals.ok ? approvals.data : [],
    products: products.ok ? products.data : [],
    activity: activity.ok ? activity.data : [],
  };
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const adapterRef = useRef<MondayAdapter | null>(null);
  const completeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transientTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Boot: select adapter (live vs demo, with a health callback) + first load.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const sel = await selectAdapter(undefined, { onHealth: (ok) => dispatch({ type: "HEALTH", ok }) });
      adapterRef.current = sel.adapter;
      const snap = await loadSnapshots(sel.adapter);
      if (cancelled) return;
      dispatch({
        type: "READY",
        mode: sel.mode,
        adapter: sel.adapter,
        baseUrl: sel.baseUrl,
        demoReason: sel.reason,
        snap: {
          system: snap.system,
          agents: snap.agents ?? [],
          tasks: snap.tasks ?? [],
          approvals: snap.approvals ?? [],
          products: snap.products ?? [],
          activity: snap.activity ?? [],
        },
      });
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = useCallback(async () => {
    const adapter = adapterRef.current;
    if (!adapter) return;
    const snap = await loadSnapshots(adapter);
    dispatch({ type: "REFRESH", snap });
  }, []);

  // Live sync (live mode only): prefer SSE, fall back to polling.
  useEffect(() => {
    if (state.mode !== "live" || !state.baseUrl) return;
    let poll: ReturnType<typeof setInterval> | null = null;
    let es: EventSource | null = null;
    const startPolling = () => {
      if (poll) return;
      poll = setInterval(() => void refresh(), 6000);
    };
    if (typeof EventSource !== "undefined") {
      try {
        es = new EventSource(`${state.baseUrl}/events`);
        es.addEventListener("revision", () => void refresh());
        es.onerror = () => {
          es?.close();
          es = null;
          startPolling(); // documented fallback
        };
      } catch {
        startPolling();
      }
    } else {
      startPolling();
    }
    return () => {
      es?.close();
      if (poll) clearInterval(poll);
    };
  }, [state.mode, state.baseUrl, refresh]);

  const setTransient = useCallback((value: BrainState, ms = 1500) => {
    dispatch({ type: "SET_TRANSIENT", value });
    if (transientTimer.current) clearTimeout(transientTimer.current);
    transientTimer.current = setTimeout(() => dispatch({ type: "SET_TRANSIENT", value: null }), ms);
  }, []);

  const flashCompleted = useCallback(() => {
    dispatch({ type: "COMPLETED", value: true });
    if (completeTimer.current) clearTimeout(completeTimer.current);
    completeTimer.current = setTimeout(() => dispatch({ type: "COMPLETED", value: false }), 1600);
  }, []);

  const navigate = useCallback((section: Section, product?: string) => {
    dispatch({ type: "SET_SECTION", section, product });
  }, []);

  const submitCommand = useCallback(async (text: string) => {
    const adapter = adapterRef.current;
    if (!adapter || !text.trim()) return;
    dispatch({ type: "PUSH_TURN", turn: { id: nextTurnId(), role: "user", text } });
    dispatch({ type: "BUSY", busy: true });

    const parsed = classifyCommand(text);
    const outcome = await runCommand(parsed, adapter);

    dispatch({ type: "PUSH_TURN", turn: { id: nextTurnId(), role: "monday", text: outcome.speech, outcome } });
    dispatch({ type: "BUSY", busy: false });
    if (outcome.kind === "blocked") setTransient("blocked");
    if (outcome.kind === "answer" && outcome.section) {
      dispatch({ type: "SET_SECTION", section: outcome.section, product: outcome.product });
    }
  }, [setTransient]);

  // Execute a confirmed WRITE action through the adapter → MondayOS. On success
  // the cache is refreshed and the Brain transitions; a failure is reported and
  // never silently downgraded to demo.
  const confirmAction = useCallback(async (parsed: ParsedCommand) => {
    const adapter = adapterRef.current;
    if (!adapter) return;
    dispatch({ type: "BUSY", busy: true });
    if (parsed.intent === "team.run") setTransient("executing", 2000);
    let r: ActionResult<unknown>;
    switch (parsed.intent) {
      case "team.run":
        r = await adapter.runTeam({ taskId: parsed.entities.taskId ?? "" });
        break;
      case "task.create":
        r = await adapter.createTask({ title: parsed.rawText, objective: parsed.rawText, product: parsed.entities.product });
        break;
      case "task.assign":
        r = await adapter.assignTask(parsed.entities.taskId ?? "", parsed.entities.agent ?? "");
        break;
      default:
        r = { ok: false, mode: adapter.mode, error: { code: "unsupported", message: "Not a write action." } };
    }
    const speech = r.ok
      ? "Done — MondayOS executed the action."
      : r.error.code === "not-implemented"
        ? "Confirmed. Write execution is available against a live MondayOS API; in demo mode nothing was changed."
        : `I couldn't complete that: ${r.error.message}`;
    dispatch({ type: "PUSH_TURN", turn: { id: nextTurnId(), role: "monday", text: speech } });
    dispatch({ type: "BUSY", busy: false });
    if (r.ok) {
      await refresh();
      flashCompleted();
    } else if (r.error.code !== "not-implemented") {
      setTransient("blocked");
    }
  }, [refresh, flashCompleted, setTransient]);

  const approve = useCallback(async (runId: string): Promise<ActionResult<Approval>> => {
    const adapter = adapterRef.current;
    if (!adapter) return { ok: false, mode: "demo", error: { code: "no-adapter", message: "not ready" } };
    const r = await adapter.approveRun(runId);
    if (r.ok) {
      await refresh();
      flashCompleted();
    } else if (r.error.code !== "not-implemented") {
      setTransient("blocked");
    }
    return r;
  }, [refresh, flashCompleted, setTransient]);

  const reject = useCallback(async (runId: string, reason: string): Promise<ActionResult<Approval>> => {
    const adapter = adapterRef.current;
    if (!adapter) return { ok: false, mode: "demo", error: { code: "no-adapter", message: "not ready" } };
    const r = await adapter.rejectRun(runId, reason);
    if (r.ok) await refresh();
    return r;
  }, [refresh]);

  const runAction = useCallback(
    (action: ResultAction) => {
      if (action.command) {
        void submitCommand(action.command);
        return;
      }
      if (action.section) navigate(action.section, action.product);
    },
    [navigate, submitCommand],
  );

  const openCommand = useCallback(() => dispatch({ type: "OPEN_COMMAND" }), []);
  const closeCommand = useCallback(() => dispatch({ type: "CLOSE_COMMAND" }), []);
  const setOverride = useCallback((value: BrainState | "auto") => dispatch({ type: "SET_OVERRIDE", value }), []);

  useEffect(() => () => {
    if (completeTimer.current) clearTimeout(completeTimer.current);
    if (transientTimer.current) clearTimeout(transientTimer.current);
  }, []);

  const derived = deriveBrainState({
    agents: state.agents,
    approvals: state.approvals,
    tasks: state.tasks,
    busy: state.busy,
    justCompleted: state.justCompleted,
  });
  const brainState: BrainState =
    state.brainOverride !== "auto" ? state.brainOverride : (state.transient ?? derived);

  const connection = deriveConnection(state.mode, state.healthOk);

  const value = useMemo<AppContextValue>(
    () => ({
      state,
      brainState,
      connection,
      adapter: state.adapter ?? null,
      submitCommand,
      confirmAction,
      approve,
      reject,
      refresh,
      runAction,
      navigate,
      openCommand,
      closeCommand,
      setOverride,
    }),
    [state, brainState, connection, submitCommand, confirmAction, approve, reject, refresh, runAction, navigate, openCommand, closeCommand, setOverride],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within <AppProvider>");
  return ctx;
}
