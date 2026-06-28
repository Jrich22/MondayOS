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
from brain.reasoner import ReasoningEngine
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
from tasks import (
    ApprovalLevel,
    InvalidTransitionError,
    TaskManager,
    TaskNotFoundError,
    TaskPriority,
    TaskStatus,
    TaskType,
    TaskValidationError,
)

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
        self.__tasks = TaskManager(self._config.project_root)
        self.__reasoner = ReasoningEngine(self.__knowledge, self.__tasks)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ask(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> AskResponse:
        """
        Answer an engineering question using knowledge already stored in MondayOS.

        The ReasoningEngine searches the knowledge store and active tasks,
        traverses entry relationships, ranks results, and synthesises a
        plain-text answer — all without calling an external model.

        Supported question types:
            "Have we seen this before?"         — HISTORICAL lookup
            "What do we know about X?"          — SUMMARY across all types
            "Show related bugs / ADRs / tasks"  — TYPE_FILTER search
            "What is currently blocked?"        — BLOCKED_TASKS filter
            "What changed recently?"            — RECENT_CHANGES by updated_at
            "What should I read first about X?" — ONBOARDING ordered by connectivity

        Returns an AskResponse with answer text, ranked sources, supporting
        entries, related tasks and decisions, confidence score, and suggested
        next actions the caller can execute immediately.

        When an LLM integration is added in a future sprint, it plugs in
        inside ReasoningEngine — this method signature does not change.
        """
        result = self.__reasoner.answer(prompt, context=context)
        return AskResponse(
            answer=result.answer,
            sources=result.sources,
            model_used=result.model_used,
            confidence=result.confidence,
            task_id=None,
            supporting_entries=result.supporting_entries,
            related_tasks=result.related_tasks,
            related_decisions=result.related_decisions,
            suggested_next_actions=result.suggested_next_actions,
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
        Create, retrieve, update, or list tasks through the TaskManager.

        Actions:
            create      — create a new task (requires title, objective, task_type, priority)
            get         — retrieve a task by task_id
            list        — alias for list_active
            list_active — list all active tasks; optional filters: status, priority, task_type
            complete    — transition a task to COMPLETED (requires task_id)

        Returns a TaskResponse with success=True on success or success=False with
        a descriptive message on failure. Does not raise.
        """
        if action in ("list", "list_active"):
            return self._task_list(action, kwargs)

        if action == "create":
            return self._task_create(title, objective, kwargs)

        if action == "get":
            return self._task_get(task_id)

        if action == "complete":
            return self._task_complete(task_id, kwargs)

        return TaskResponse(
            action=action,
            success=False,
            task_id=task_id or None,
            data={},
            message=f"Unknown action {action!r}. Valid actions: create, get, list_active, complete",
        )

    def _task_create(self, title: str, objective: str, kwargs: dict[str, Any]) -> TaskResponse:
        try:
            task_type_raw = kwargs.get("task_type", "feature")
            priority_raw = kwargs.get("priority", "P2")
            created_by = kwargs.get("created_by", f"human:{self._session_id}")
            approval_raw = kwargs.get("approval_required", "human-review")
            context = kwargs.get("context", "")
            acceptance_criteria = kwargs.get("acceptance_criteria")

            try:
                task_type = TaskType(task_type_raw)
            except ValueError:
                valid = [t.value for t in TaskType]
                return TaskResponse(
                    action="create",
                    success=False,
                    task_id=None,
                    data={},
                    message=f"Unknown task_type {task_type_raw!r}. Valid values: {valid}",
                )

            try:
                priority = TaskPriority(priority_raw)
            except ValueError:
                valid = [p.value for p in TaskPriority]
                return TaskResponse(
                    action="create",
                    success=False,
                    task_id=None,
                    data={},
                    message=f"Unknown priority {priority_raw!r}. Valid values: {valid}",
                )

            try:
                approval = ApprovalLevel(approval_raw)
            except ValueError:
                approval = ApprovalLevel.HUMAN_REVIEW

            created_task = self.__tasks.create(
                title=title,
                task_type=task_type,
                priority=priority,
                objective=objective,
                created_by=created_by,
                approval_required=approval,
                context=context,
                acceptance_criteria=list(acceptance_criteria) if acceptance_criteria else None,
            )

            self.__bus.publish(Event(
                event_type=EventType.TASK_CREATED,
                source="monday",
                timestamp=datetime.now(tz=timezone.utc),
                payload={"task_id": created_task.id, "title": created_task.title},
                session_id=self._session_id,
            ))

            return TaskResponse(
                action="create",
                success=True,
                task_id=created_task.id,
                data=_task_to_dict(created_task),
                message=f"Created {created_task.id}",
            )
        except TaskValidationError as exc:
            return TaskResponse(
                action="create",
                success=False,
                task_id=None,
                data={},
                message=str(exc),
            )

    def _task_get(self, task_id: str) -> TaskResponse:
        if not task_id:
            return TaskResponse(
                action="get",
                success=False,
                task_id=None,
                data={},
                message="task_id is required for action 'get'",
            )
        try:
            found = self.__tasks.get(task_id)
            return TaskResponse(
                action="get",
                success=True,
                task_id=found.id,
                data=_task_to_dict(found),
                message=f"Found {found.id}",
            )
        except TaskNotFoundError as exc:
            return TaskResponse(
                action="get",
                success=False,
                task_id=task_id,
                data={},
                message=str(exc),
            )

    def _task_list(self, action: str, kwargs: dict[str, Any]) -> TaskResponse:
        status_raw = kwargs.get("status")
        priority_raw = kwargs.get("priority")
        task_type_raw = kwargs.get("task_type")
        assigned_to = kwargs.get("assigned_to")

        status = TaskStatus(status_raw) if status_raw else None
        priority = TaskPriority(priority_raw) if priority_raw else None
        task_type = TaskType(task_type_raw) if task_type_raw else None

        tasks = self.__tasks.list_active(
            status=status,
            priority=priority,
            assigned_to=assigned_to,
            task_type=task_type,
        )

        return TaskResponse(
            action=action,
            success=True,
            task_id=None,
            data={"tasks": [_task_to_dict(t) for t in tasks], "count": len(tasks)},
            message=f"{len(tasks)} active task(s)",
        )

    def _task_complete(self, task_id: str, kwargs: dict[str, Any]) -> TaskResponse:
        if not task_id:
            return TaskResponse(
                action="complete",
                success=False,
                task_id=None,
                data={},
                message="task_id is required for action 'complete'",
            )
        changed_by = kwargs.get("changed_by", f"human:{self._session_id}")
        reason = kwargs.get("reason", "")
        try:
            updated = self.__tasks.update_status(
                task_id=task_id,
                new_status=TaskStatus.COMPLETED,
                changed_by=changed_by,
                reason=reason,
            )
            self.__bus.publish(Event(
                event_type=EventType.TASK_COMPLETED,
                source="monday",
                timestamp=datetime.now(tz=timezone.utc),
                payload={"task_id": updated.id},
                session_id=self._session_id,
            ))
            return TaskResponse(
                action="complete",
                success=True,
                task_id=updated.id,
                data=_task_to_dict(updated),
                message=f"{updated.id} marked COMPLETED",
            )
        except TaskNotFoundError as exc:
            return TaskResponse(
                action="complete",
                success=False,
                task_id=task_id,
                data={},
                message=str(exc),
            )
        except InvalidTransitionError as exc:
            return TaskResponse(
                action="complete",
                success=False,
                task_id=task_id,
                data={},
                message=str(exc),
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


def _task_to_dict(task: Any) -> dict[str, Any]:
    """Convert a Task to a plain dict for TaskResponse.data."""
    return {
        "id": task.id,
        "title": task.title,
        "task_type": task.task_type.value,
        "status": task.status.value,
        "priority": task.priority.value,
        "created_by": task.created_by,
        "objective": task.objective,
        "context": task.context,
        "assigned_to": task.assigned_to,
        "acceptance_criteria": list(task.acceptance_criteria),
        "created": task.created.isoformat(),
        "updated": task.updated.isoformat(),
    }


def _extract_summary(content: str) -> str:
    """Extract a single-line summary from Markdown content."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:500]
    return content.replace("\n", " ").strip()[:500]
