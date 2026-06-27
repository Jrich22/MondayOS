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
from events.types import Event, EventType
from knowledge import KnowledgeStore
from knowledge.entry import KnowledgeEntry, KnowledgeType, LifecycleStatus
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
        monday.learn("...")     # add knowledge — persisted immediately
        monday.search("...")    # search across all sources

    Configuration:
        from monday import Monday, MondayConfig
        monday = Monday(MondayConfig(project_root=Path("/my/project")))

    Thread safety:
        Monday instances are not thread-safe in Phase 1.
    """

    VERSION: str = _VERSION

    def __init__(self, config: MondayConfig | None = None) -> None:
        self._config = config or MondayConfig()
        self._session_id = self._config.session_id or _new_session_id()
        self._created_at: datetime = datetime.now(tz=timezone.utc)

        brain_config = BrainConfig.from_project_root(self._config.project_root)
        self.__brain = Brain(brain_config)
        self.__bus = EventBus()
        self.__knowledge = KnowledgeStore(self._config.project_root)
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

        Validates the entry type, constructs a Canonical Knowledge Object,
        persists it via KnowledgeStore, publishes a KNOWLEDGE_ENTRY_CREATED
        event, and returns a typed LearnResponse with the assigned entry ID.

        Args:
            content:    Full body of the knowledge entry (Markdown).
            title:      Short title. Auto-extracted from content if empty.
            entry_type: MKS knowledge type string (e.g. "bug", "pattern").
                        See KnowledgeType for all valid values.
            tags:       Searchable tags for this entry.
            components: MondayOS components or domain areas this entry covers.

        Returns:
            LearnResponse with entry_id, accepted=True, and a status message.
            On validation failure, accepted=False and message contains the error.
        """
        try:
            k_type = KnowledgeType(entry_type)
        except ValueError:
            valid = [t.value for t in KnowledgeType]
            return LearnResponse(
                entry_id="",
                accepted=False,
                entry_type=entry_type,
                message=f"Unknown entry_type {entry_type!r}. Valid values: {valid}",
            )

        now = datetime.now(tz=timezone.utc)
        summary = _extract_summary(content)
        effective_title = title if title else summary[:80]

        entry = KnowledgeEntry(
            id="",  # KnowledgeStore assigns the real ID
            entry_type=k_type,
            title=effective_title,
            status=LifecycleStatus.ACTIVE,
            created_at=now,
            components=list(components or []),
            tags=list(tags or []),
            body=content,
            summary=summary,
            created_by=f"human:{self._session_id}",
            updated_at=now,
            updated_by=f"human:{self._session_id}",
        )

        entry_id = self.__knowledge.add(entry)

        self.__bus.publish(Event(
            event_type=EventType.KNOWLEDGE_ENTRY_CREATED,
            source="monday",
            timestamp=datetime.now(tz=timezone.utc),
            payload={"entry_id": entry_id, "entry_type": entry_type},
            session_id=self._session_id,
        ))

        return LearnResponse(
            entry_id=entry_id,
            accepted=True,
            entry_type=entry_type,
            message=f"Stored as {entry_id}",
        )

    def search(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> SearchResponse:
        """
        Search across MondayOS knowledge entries.

        Phase 1 searches only the knowledge base. Future sprints will add
        task and memory sources via the SearchEngine. The `sources` parameter
        is accepted but does not filter results yet — it is echoed back in
        `sources_queried` for API stability.

        Args:
            query:   Full-text search query (keyword-based in Phase 1).
            sources: Limit to specific sources. Echoed in response; does not
                     yet filter results (Phase 1 searches knowledge only).
            limit:   Maximum number of results. Default 10.

        Returns:
            SearchResponse with ranked results and total_found count.
        """
        entries = self.__knowledge.search(query, limit=limit)

        results = [
            {
                "id": e.id,
                "title": e.title,
                "summary": e.summary,
                "entry_type": e.entry_type.value,
                "tags": e.tags,
                "components": e.components,
            }
            for e in entries
        ]

        return SearchResponse(
            query=query,
            results=results,
            total_found=len(results),
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

        TODO: Route each action to the appropriate TaskManager method.
        TODO: Validate action against the supported action set.
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

        Reads only from live instance state — no external I/O.
        """
        now = datetime.now(tz=timezone.utc)
        uptime = (now - self._created_at).total_seconds()

        modules = [
            ModuleStatus("brain",     available=True, initialized=self.__brain     is not None),
            ModuleStatus("events",    available=True, initialized=self.__bus       is not None),
            ModuleStatus("knowledge", available=True, initialized=self.__knowledge is not None),
            ModuleStatus("memory",    available=True, initialized=self.__memory    is not None),
            ModuleStatus("search",    available=True, initialized=self.__search    is not None),
            ModuleStatus("tasks",     available=True, initialized=self.__tasks     is not None),
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


def _extract_summary(content: str) -> str:
    """Extract a single-line summary from Markdown content."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:500]
    return content.replace("\n", " ").strip()[:500]
