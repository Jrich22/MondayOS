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
  Conversation,
  ConversationSummary,
  ContextSnapshot,
  WorkspaceProject,
} from "./types";

export interface WorkspaceState {
  projects: WorkspaceProject[];
  project: string;
  conversations: ConversationSummary[];
  conversation: Conversation | null;
  context: ContextSnapshot | null;
  loadingProjects: boolean;
  loadingConversation: boolean;
  sending: boolean;
  error: string;
  notice: string;
}

export interface WorkspaceActions {
  selectProject(project: string): void;
  selectConversation(id: string): void;
  newConversation(): Promise<void>;
  send(content: string): Promise<void>;
  retry(): Promise<void>;
  rename(id: string, title: string): Promise<void>;
  archive(id: string): Promise<void>;
  saveToKnowledge(messageId: string): Promise<void>;
  refreshContext(): Promise<void>;
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
      setError("");
      setNotice("");
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
      setError("");
      const res = await client.sendMessage(project, conversation.id, content);
      setSending(false);
      if (!res.ok) {
        setError(res.error.message);
        return;
      }
      setConversation(res.data.conversation);
      if (res.data.context) setContext(res.data.context);
      await loadConversations(project);
    },
    [client, project, conversation, loadConversations],
  );

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

  const refreshContext = useCallback(async () => {
    await loadContext(project);
  }, [loadContext, project]);

  const dismissNotice = useCallback(() => setNotice(""), []);

  return [
    {
      projects,
      project,
      conversations,
      conversation,
      context,
      loadingProjects,
      loadingConversation,
      sending,
      error,
      notice,
    },
    {
      selectProject,
      selectConversation,
      newConversation,
      send,
      retry,
      rename,
      archive,
      saveToKnowledge,
      refreshContext,
      dismissNotice,
    },
  ];
}
