"""
Monday — the stable public API for MondayOS.

This is the only import external consumers need. Internal modules
(brain, events, knowledge, memory, search, tasks) are implementation
details accessed exclusively through this class.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from brain import Brain, BrainConfig
from events import EventBus
from knowledge import KnowledgeStore
from memory import SessionMemory
from monday.config import MondayConfig
from monday.types import (
    AskResponse,
    LearnResponse,
    ModuleStatus,
    SearchResponse,
    StatusResponse,
    TaskResponse,
)
from search import SearchEngine
from tasks import TaskManager

_VERSION = "0.1.0"


class Monday:
    """
    The public interface for MondayOS.

    Monday is the stable entry point for all external interaction with the
    platform. It composes the internal subsystems and exposes a narrow,
    versioned surface that will not change as internals are refactored.

    External code imports and uses only this class. Internal module classes
    (Brain, KnowledgeStore, etc.) are not part of the public contract.

    Usage:
        from monday import Monday

        monday = Monday()
        monday.status()        # system health check
        monday.ask("...")       # query the system
        monday.learn("...")     # add knowledge
        monday.search("...")    # search across all sources
        monday.task("create")  # manage tasks

    Configuration:
        Pass a MondayConfig to customize the project root, model tier,
        approval settings, and logging level:

            from monday import Monday, MondayConfig
            monday = Monday(MondayConfig(model_tier="high"))

    Thread safety:
        Monday instances are not thread-safe in Phase 1. Use one instance
        per thread or add external synchronization.
    """

    VERSION: str = _VERSION

    def __init__(self, config: MondayConfig | None = None) -> None:
        self._config = config or MondayConfig()
        self._session_id = self._config.session_id or _new_session_id()
        self._created_at: datetime = datetime.now(tz=timezone.utc)

        # Compose all internal subsystems.
        # Internal modules are not exposed as public attributes.
        brain_config = BrainConfig.from_project_root(self._config.project_root)
        self.__brain = Brain(brain_config)
        self.__bus = EventBus()
        self.__knowledge = KnowledgeStore()
        self.__memory = SessionMemory(session_id=self._session_id)
        self.__search = SearchEngine()
        self.__tasks = TaskManager()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ask(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> AskResponse:
        """
        Submit a natural language prompt to MondayOS and receive a response.

        Ask is the primary conversational interface. It will eventually route
        to the Brain, which searches for relevant knowledge, selects a model,
        and returns a grounded answer with source attribution.

        Args:
            prompt:  The question, instruction, or statement to process.
            context: Optional key/value pairs that constrain the query scope
                     (e.g. {"component": "memory", "task_id": "TASK-0042"}).

        Returns:
            AskResponse with the answer, sources used, and model attribution.

        TODO: Delegate to Brain.execute_task() with a RESEARCH task type.
        TODO: Populate sources from SearchEngine results used to ground the answer.
        TODO: Populate model_used and confidence from the RoutingDecision log.
        """
        return AskResponse(
            answer="",
            sources=[],
            model_used="",
            confidence=0.0,
            task_id=None,
        )

    def learn(
        self,
        content: str,
        title: str = "",
        entry_type: str = "pattern",
        tags: list[str] | None = None,
        components: list[str] | None = None,
    ) -> LearnResponse:
        """
        Teach MondayOS something new by adding a knowledge entry.

        Learn persists structured knowledge to the knowledge base so the
        system can retrieve and apply it in future tasks. This is the human-
        facing path for adding bug resolutions, decisions, patterns, or
        runbooks.

        Args:
            content:    The full body of the knowledge entry (Markdown).
            title:      Short summary title. Inferred from content if empty.
            entry_type: One of "bug", "decision", "pattern", "runbook".
                        Defaults to "pattern".
            tags:       Searchable tags for this entry.
            components: MondayOS components this entry applies to.

        Returns:
            LearnResponse with the new entry ID and acceptance status.

        TODO: Validate entry_type against EntryType enum.
        TODO: Delegate to KnowledgeStore.add() with a constructed KnowledgeEntry.
        TODO: Publish KNOWLEDGE_ENTRY_CREATED event to EventBus.
        TODO: Return the real entry ID from KnowledgeStore.add().
        """
        return LearnResponse(
            entry_id="",
            accepted=False,
            entry_type=entry_type,
            message="",
        )

    def search(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> SearchResponse:
        """
        Search across MondayOS knowledge, tasks, and memory.

        Returns ranked results from all registered data sources, or only the
        sources specified. Results are ordered by relevance score, descending.

        Args:
            query:   Full-text search query.
            sources: Limit to specific sources: "knowledge", "tasks", "memory".
                     Searches all sources if None or empty.
            limit:   Maximum number of results to return. Default 10.

        Returns:
            SearchResponse with ranked results and source attribution.

        TODO: Delegate to SearchEngine.search() with a constructed SearchQuery.
        TODO: Convert SearchResult objects to dicts for the public response.
        TODO: Populate total_found from the engine's untruncated result count.
        """
        return SearchResponse(
            query=query,
            results=[],
            total_found=0,
            sources_queried=sources or [],
        )

    def task(
        self,
        action: str,
        title: str = "",
        objective: str = "",
        task_id: str = "",
        **kwargs: Any,
    ) -> TaskResponse:
        """
        Create, retrieve, update, or list tasks.

        Task is the operational interface to the task system. The `action`
        parameter determines what operation is performed.

        Args:
            action:    Operation to perform. Valid values:
                           "create"   — create a new task (requires title, objective)
                           "get"      — retrieve a task by ID (requires task_id)
                           "list"     — list active tasks
                           "update"   — update task status (requires task_id)
                           "complete" — mark a task complete (requires task_id)
            title:     Task title (used with action="create").
            objective: Task objective (used with action="create").
            task_id:   Target task ID (used with get, update, complete).
            **kwargs:  Additional action-specific parameters (priority, status, etc.).

        Returns:
            TaskResponse with action result, affected task ID, and payload.

        TODO: Route each action to the appropriate TaskManager method.
        TODO: Validate action against the supported action set.
        TODO: Convert Task dataclass to dict for the response data field.
        TODO: Publish task lifecycle events to EventBus.
        """
        return TaskResponse(
            action=action,
            success=False,
            task_id=task_id or None,
            data={},
            message="",
        )

    def status(self) -> StatusResponse:
        """
        Return the current health and configuration status of this Monday instance.

        Status is the one method that is substantially implemented in Phase 1.
        It reads only from live instance state, requiring no external I/O.

        Returns:
            StatusResponse with system health, version, session ID, per-module
            status, and uptime in seconds.
        """
        now = datetime.now(tz=timezone.utc)
        uptime = (now - self._created_at).total_seconds()

        modules = [
            ModuleStatus("brain",     available=True, initialized=self.__brain    is not None),
            ModuleStatus("events",    available=True, initialized=self.__bus      is not None),
            ModuleStatus("knowledge", available=True, initialized=self.__knowledge is not None),
            ModuleStatus("memory",    available=True, initialized=self.__memory   is not None),
            ModuleStatus("search",    available=True, initialized=self.__search   is not None),
            ModuleStatus("tasks",     available=True, initialized=self.__tasks    is not None),
        ]
        healthy = all(m.initialized for m in modules)

        return StatusResponse(
            healthy=healthy,
            version=self.VERSION,
            session_id=self._session_id,
            modules=modules,
            uptime_seconds=uptime,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"Monday(version={self.VERSION!r}, session_id={self._session_id!r})"


def _new_session_id() -> str:
    return str(uuid.uuid4())
