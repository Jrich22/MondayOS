/**
 * AI Workspace client state.
 *
 * The browser is a cache, never the record (ADR-015). Every piece of state here
 * is reconstructed from MondayOS on load, and nothing is written to
 * localStorage — losing the tab loses nothing.
 *
 * Project switching clears conversation state before loading the new project's,
 * so one project's messages can never be on screen while another is selected.
 * That is a display-level echo of the server-side isolation rule, not a
 * substitute for it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createWorkspaceClient, type WorkspaceClient } from "./client";
import type {
  ActivityEvent,
  Briefing,
  Conversation,
  ConversationSummary,
  ContextSnapshot,
  SearchResult,
  WorkspaceProject,
} from "./types";

/** What Monday is doing, mapped onto the Brain's existing visual states. */
export type MondayState =
  | "idle"
  | "thinking"
  | "learning"
  | "executing"
  | "completed"
  | "blocked";

export interface WorkspaceState {
  projects: WorkspaceProject[];
  project: string;
  conversations: ConversationSummary[];
  conversation: Conversation | null;
  context: ContextSnapshot | null;
  briefing: Briefing | null;
  activity: ActivityEvent[];
  searchResult: SearchResult | null;
  loadingProjects: boolean;
  loadingConversation: boolean;
  sending: boolean;
  /** Text arriving from the current stream. Rendered as a live assistant turn. */
  streaming: string;
  /** Set while switching projects, so the transition is visible rather than a flicker. */
  switchingTo: string;
  mondayState: MondayState;
  error: string;
  notice: string;
}

export interface WorkspaceActions {
  selectProject(project: string): void;
  selectConversation(id: string): void;
  newConversation(): Promise<void>;
  send(content: string): Promise<void>;
  stop(): void;
  retry(): Promise<void>;
  rename(id: string, title: string): Promise<void>;
  archive(id: string): Promise<void>;
  saveToKnowledge(messageId: string): Promise<void>;
  createTask(messageId: string): Promise<void>;
  search(query: string, scope: "project" | "all"): Promise<void>;
  clearSearch(): void;
  refreshContext(): Promise<void>;
  refreshBriefing(): Promise<void>;
  continueWorking(): void;
  dismissNotice(): void;
}

export function useWorkspace(
  baseUrl: string | undefined,
  clientOverride?: WorkspaceClient,
): [WorkspaceState, WorkspaceActions] {
  const client = useMemo(
    () => clientOverride ?? (baseUrl ? createWorkspaceClient({ baseUrl }) : null),
    [baseUrl, clientOverride],
  );

  const [projects, setProjects] = useState<WorkspaceProject[]>([]);
  const [project, setProject] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [context, setContext] = useState<ContextSnapshot | null>(null);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [streaming, setStreaming] = useState("");
  const [switchingTo, setSwitchingTo] = useState("");
  const [mondayState, setMondayState] = useState<MondayState>("idle");
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // Guards a late response from a project the operator has already left. Without
  // it, a slow fetch for project A can land after a switch to B and paint A's
  // conversations under B's name.
  const activeProject = useRef("");
  activeProject.current = project;

  // Held so Stop can abort the in-flight request. Cleared when a turn settles.
  const stopRef = useRef<(() => void) | null>(null);

  // Continue Working may need to switch project first; the conversation opens
  // once that project's list has loaded, so it is never opened under the wrong one.
  const [pendingConversation, setPendingConversation] = useState("");

  // ------------------------------------------------------------------ loads

  useEffect(() => {
    if (!client) return;
    let cancelled = false;
    setLoadingProjects(true);
    void client.listProjects().then((res) => {
      if (cancelled) return;
      setLoadingProjects(false);
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setProjects(res.data);
      setProject((current) => current || res.data[0]?.name || "");
    });
    return () => {
      cancelled = true;
    };
  }, [client]);

  const loadConversations = useCallback(
    async (target: string) => {
      if (!client || !target) return;
      const res = await client.listConversations(target);
      if (activeProject.current !== target) return;
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setConversations(res.data);
    },
    [client],
  );

  const loadContext = useCallback(
    async (target: string) => {
      if (!client || !target) return;
      const res = await client.getContext(target);
      if (activeProject.current !== target) return;
      if (!res.ok) {
        setContext(null);
        setError(res.error.message);
        return;
      }
      setContext(res.data);
      setSwitchingTo("");
      setMondayState("idle");
    },
    [client],
  );

  useEffect(() => {
    if (!project) return;
    setError("");
    void loadConversations(project);
    void loadContext(project);
  }, [project, loadConversations, loadContext]);


  // ---------------------------------------------------------------- actions

  const selectProject = useCallback(
    (next: string) => {
      if (next === project) return;
      // Clear before loading: nothing from the previous project may remain on
      // screen under the new project's name, even for one frame.
      activeProject.current = next;
      setConversation(null);
      setConversations([]);
      setContext(null);
      setSearchResult(null);
      setError("");
      setNotice("");
      setSwitchingTo(next);
      setMondayState("learning");
      setProject(next);
    },
    [project],
  );

  const selectConversation = useCallback(
    (id: string) => {
      if (!client || !project) return;
      setLoadingConversation(true);
      const target = project;
      void client.getConversation(target, id).then((res) => {
        if (activeProject.current !== target) return;
        setLoadingConversation(false);
        if (!res.ok) {
          setError(res.error.message);
          return;
        }
        setConversation(res.data);
      });
    },
    [client, project],
  );

  const newConversation = useCallback(async () => {
    if (!client || !project) return;
    const res = await client.createConversation(project);
    if (!res.ok) {
      setError(res.error.message);
      return;
    }
    setConversation(res.data);
    await loadConversations(project);
  }, [client, project, loadConversations]);

  const send = useCallback(
    async (content: string) => {
      if (!client || !project || !conversation || !content.trim()) return;
      setSending(true);
      setStreaming("");
      setError("");
      setMondayState("learning"); // reading project context first

      const { events, stop } = client.streamMessage(project, conversation.id, content);
      stopRef.current = stop;

      let accumulated = "";
      try {
        for await (const event of events) {
          if (event.type === "context") {
            setContext(event.context);
            setMondayState("thinking");
          } else if (event.type === "user") {
            // Show the user's turn immediately; the server has already stored it.
            setConversation((c) =>
              c ? { ...c, messages: [...c.messages, event.message] } : c,
            );
          } else if (event.type === "delta") {
            accumulated += event.text;
            setStreaming(accumulated);
          } else if (event.type === "done") {
            setConversation(event.conversation);
            setStreaming("");
            setMondayState(event.message.error ? "blocked" : "completed");
          } else if (event.type === "error") {
            setError(event.message);
            setMondayState("blocked");
          }
        }
      } finally {
        stopRef.current = null;
        setSending(false);
        setStreaming("");
        // Re-read: on a stop the server persisted a partial that never arrived
        // as a `done` frame, so the client's view would otherwise be missing it.
        const fresh = await client.getConversation(project, conversation.id);
        if (fresh.ok && activeProject.current === project) setConversation(fresh.data);
        await loadConversations(project);
        void client.activity().then((r) => {
          if (r.ok) setActivity(r.data.events);
        });
        setTimeout(() => setMondayState("idle"), 1200);
      }
    },
    [client, project, conversation, loadConversations],
  );

  const stop = useCallback(() => {
    stopRef.current?.();
  }, []);

  const retry = useCallback(async () => {
    if (!client || !project || !conversation) return;
    setSending(true);
    setError("");
    const res = await client.retry(project, conversation.id);
    setSending(false);
    if (!res.ok) {
      setError(res.error.message);
      return;
    }
    setConversation(res.data.conversation);
    if (res.data.context) setContext(res.data.context);
  }, [client, project, conversation]);

  const rename = useCallback(
    async (id: string, title: string) => {
      if (!client || !project || !title.trim()) return;
      const res = await client.rename(project, id, title);
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setConversation((c) => (c && c.id === id ? { ...c, title: res.data.title } : c));
      await loadConversations(project);
    },
    [client, project, loadConversations],
  );

  const archive = useCallback(
    async (id: string) => {
      if (!client || !project) return;
      const res = await client.archive(project, id);
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setConversation((c) => (c && c.id === id ? null : c));
      await loadConversations(project);
    },
    [client, project, loadConversations],
  );

  const saveToKnowledge = useCallback(
    async (messageId: string) => {
      if (!client || !project || !conversation) return;
      const res = await client.saveToKnowledge(project, conversation.id, messageId);
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setNotice(`Saved to project knowledge as ${res.data.knowledge_id}.`);
      // Re-read: capture appends an event to the transcript so the knowledge is
      // traceable from the conversation as well as from the entry.
      selectConversation(conversation.id);
    },
    [client, project, conversation, selectConversation],
  );

  const createTask = useCallback(
    async (messageId: string) => {
      if (!client || !project || !conversation) return;
      setMondayState("executing");
      const res = await client.createTask(project, conversation.id, messageId);
      setMondayState("idle");
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setNotice(`Created task ${res.data.task.id}: ${res.data.task.title}`);
      selectConversation(conversation.id);
    },
    [client, project, conversation, selectConversation],
  );

  const search = useCallback(
    async (query: string, scope: "project" | "all") => {
      if (!client || !query.trim()) return;
      const res = await client.search(query, project, scope);
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setSearchResult(res.data);
    },
    [client, project],
  );

  const clearSearch = useCallback(() => setSearchResult(null), []);

  const refreshBriefing = useCallback(async () => {
    if (!client) return;
    const res = await client.briefing("");
    if (res.ok) setBriefing(res.data);
  }, [client]);

  const continueWorking = useCallback(() => {
    // Only ever reopens what the briefing actually found. With nothing recorded
    // there is nothing to continue, and the button is not offered.
    if (!briefing?.can_continue) return;
    if (briefing.project !== project) selectProject(briefing.project);
    setPendingConversation(briefing.conversation_id);
  }, [briefing, project, selectProject]);

  const refreshContext = useCallback(async () => {
    await loadContext(project);
  }, [loadContext, project]);

  // Declared after the callbacks they use: these effects close over
  // `refreshBriefing` and `selectConversation`, which are defined below the
  // loaders. Placing them here keeps declaration order honest rather than
  // reaching forward.
  useEffect(() => {
    void refreshBriefing();
  }, [refreshBriefing]);

  useEffect(() => {
    if (!pendingConversation || !conversations.length) return;
    if (conversations.some((c) => c.id === pendingConversation)) {
      selectConversation(pendingConversation);
      setPendingConversation("");
    }
  }, [pendingConversation, conversations, selectConversation]);

  const dismissNotice = useCallback(() => setNotice(""), []);

  return [
    {
      projects,
      project,
      conversations,
      conversation,
      context,
      briefing,
      activity,
      searchResult,
      loadingProjects,
      loadingConversation,
      sending,
      streaming,
      switchingTo,
      mondayState,
      error,
      notice,
    },
    {
      selectProject,
      selectConversation,
      newConversation,
      send,
      stop,
      retry,
      rename,
      archive,
      saveToKnowledge,
      createTask,
      search,
      clearSearch,
      refreshContext,
      refreshBriefing,
      continueWorking,
      dismissNotice,
    },
  ];
}
