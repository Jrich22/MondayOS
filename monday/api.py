"""
Monday — the stable public API for MondayOS.

This is the only import external consumers need. Internal modules
(brain, events, knowledge, memory, search, tasks) are implementation
details accessed exclusively through this class.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from brain import Brain, BrainConfig, create_provider
from brain.reasoner import ReasoningEngine
from events import EventBus
from events.types import Event, EventType
from knowledge import KnowledgeStore
from knowledge.entry import KnowledgeEntry, KnowledgeType, LifecycleStatus
from memory import SessionMemory
from monday.config import MondayConfig
from monday.types import (
    AdviseResponse,
    AgentResponse,
    AskResponse,
    DoctorResponse,
    ExecuteResponse,
    GrowthResponse,
    TeamResponse,
    LearnResponse,
    MigrateResponse,
    ModuleStatus,
    OnboardResponse,
    ProjectResponse,
    PublishResponse,
    SearchResponse,
    StatusResponse,
    TaskResponse,
    WorkflowResponse,
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

_VERSION = "1.0.0b1"


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
        self.__provider = create_provider(self._config.provider_config)

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
        authored_by: str = "human",
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
            authored_by: Provenance. "human" (default) for curated knowledge;
                        "agent" for machine-generated output such as an
                        orchestrator execution capture. This decides where the
                        entry is stored — see KnowledgeStore._write_file — so
                        generated output stays out of the source-controlled
                        tree. Recording it accurately matters: entries captured
                        by the orchestrator previously claimed "human", which
                        left no way to tell curated knowledge from model output.

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
            authored_by=authored_by,
            created_by=f"{authored_by}:{self._session_id}",
            updated_at=now,
            updated_by=f"{authored_by}:{self._session_id}",
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

        if action == "start":
            return self._task_start(task_id)

        if action == "review":
            return self._task_review(task_id, kwargs)

        if action == "assign":
            return self._task_assign(task_id, kwargs)

        return TaskResponse(
            action=action,
            success=False,
            task_id=task_id or None,
            data={},
            message=(
                f"Unknown action {action!r}. "
                "Valid actions: create, get, list_active, start, review, assign, complete"
            ),
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

    def _task_start(self, task_id: str) -> TaskResponse:
        """Transition a task from BACKLOG → ASSIGNED → IN_PROGRESS in two hops."""
        if not task_id:
            return TaskResponse(
                action="start",
                success=False,
                task_id=None,
                data={},
                message="task_id is required for action 'start'",
            )
        try:
            task = self.__tasks.get(task_id)
            changed_by = f"workflow:{self._session_id}"
            if task.status == TaskStatus.BACKLOG:
                task = self.__tasks.update_status(
                    task_id=task_id,
                    new_status=TaskStatus.ASSIGNED,
                    changed_by=changed_by,
                    reason="workflow start",
                )
            if task.status == TaskStatus.ASSIGNED:
                task = self.__tasks.update_status(
                    task_id=task_id,
                    new_status=TaskStatus.IN_PROGRESS,
                    changed_by=changed_by,
                    reason="workflow start",
                )
            return TaskResponse(
                action="start",
                success=True,
                task_id=task.id,
                data=_task_to_dict(task),
                message=f"{task.id} is now {task.status.value}",
            )
        except TaskNotFoundError as exc:
            return TaskResponse(
                action="start",
                success=False,
                task_id=task_id,
                data={},
                message=str(exc),
            )
        except InvalidTransitionError as exc:
            return TaskResponse(
                action="start",
                success=False,
                task_id=task_id,
                data={},
                message=str(exc),
            )

    def _task_review(self, task_id: str, kwargs: dict[str, Any]) -> TaskResponse:
        """Transition a task IN_PROGRESS → REVIEW (awaiting human review)."""
        if not task_id:
            return TaskResponse(
                action="review",
                success=False,
                task_id=None,
                data={},
                message="task_id is required for action 'review'",
            )
        changed_by = kwargs.get("changed_by", f"workflow:{self._session_id}")
        reason = kwargs.get("reason", "")
        try:
            updated = self.__tasks.update_status(
                task_id=task_id,
                new_status=TaskStatus.REVIEW,
                changed_by=changed_by,
                reason=reason,
            )
            return TaskResponse(
                action="review",
                success=True,
                task_id=updated.id,
                data=_task_to_dict(updated),
                message=f"{updated.id} moved to REVIEW",
            )
        except TaskNotFoundError as exc:
            return TaskResponse(
                action="review", success=False, task_id=task_id, data={}, message=str(exc),
            )
        except InvalidTransitionError as exc:
            return TaskResponse(
                action="review", success=False, task_id=task_id, data={}, message=str(exc),
            )

    def _task_assign(self, task_id: str, kwargs: dict[str, Any]) -> TaskResponse:
        """Assign a task to an assignee (person, model, or 'role:<slug>')."""
        if not task_id:
            return TaskResponse(
                action="assign", success=False, task_id=None, data={},
                message="task_id is required for action 'assign'",
            )
        assignee = kwargs.get("assignee", "")
        if not assignee:
            return TaskResponse(
                action="assign", success=False, task_id=task_id, data={},
                message="assignee is required for action 'assign'",
            )
        assigned_by = kwargs.get("assigned_by", f"workflow:{self._session_id}")
        try:
            updated = self.__tasks.assign(
                task_id=task_id, assignee=assignee, assigned_by=assigned_by,
            )
            return TaskResponse(
                action="assign",
                success=True,
                task_id=updated.id,
                data=_task_to_dict(updated),
                message=f"{updated.id} assigned to {assignee}",
            )
        except TaskNotFoundError as exc:
            return TaskResponse(
                action="assign", success=False, task_id=task_id, data={}, message=str(exc),
            )
        except InvalidTransitionError as exc:
            return TaskResponse(
                action="assign", success=False, task_id=task_id, data={}, message=str(exc),
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

    def workflow(
        self,
        action: str,
        name: str = "",
        inputs: dict[str, str] | None = None,
        approval_handler: Callable | None = None,
    ) -> WorkflowResponse:
        """
        List, inspect, or run predefined workflows.

        Actions:
            list  — return all workflow definitions in the definitions directory.
            show  — return step definitions for a named workflow.
            run   — execute a named workflow end-to-end.

        Args:
            action:           One of: list, show, run.
            name:             Workflow name (required for show and run).
            inputs:           Input variables for run (key=value pairs).
            approval_handler: Callable(message, context) → bool for human_approval
                              steps. Defaults to a terminal prompt. Inject in tests.

        Returns:
            WorkflowResponse with success=True on success or success=False with
            a descriptive message on failure. Does not raise.
        """
        from workflows import WorkflowEngine, WorkflowNotFoundError, WorkflowValidationError

        definitions_dir = self._config.project_root / "workflows" / "definitions"
        logs_dir = self._config.project_root / "logs" / "workflows"
        engine = WorkflowEngine(
            monday=self,
            definitions_dir=definitions_dir,
            logs_dir=logs_dir,
            approval_handler=approval_handler,
        )

        if action == "list":
            try:
                workflows = engine.list_workflows()
                return WorkflowResponse(
                    action="list",
                    success=True,
                    data={
                        "workflows": [
                            {
                                "name": wf.name,
                                "version": wf.version,
                                "description": wf.description,
                                "steps": len(wf.steps),
                                "triggers": wf.triggers,
                            }
                            for wf in workflows
                        ],
                        "count": len(workflows),
                    },
                    message=f"{len(workflows)} workflow(s) available",
                )
            except Exception as exc:
                return WorkflowResponse(
                    action="list",
                    success=False,
                    message=f"Failed to list workflows: {exc}",
                )

        if action == "show":
            if not name:
                return WorkflowResponse(
                    action="show",
                    success=False,
                    message="name is required for action 'show'",
                )
            try:
                wf = engine.get_workflow(name)
                return WorkflowResponse(
                    action="show",
                    success=True,
                    workflow_name=wf.name,
                    data={
                        "name": wf.name,
                        "version": wf.version,
                        "description": wf.description,
                        "triggers": wf.triggers,
                        "inputs": {
                            k: {
                                "description": spec.description,
                                "required": spec.required,
                                "default": spec.default,
                            }
                            for k, spec in wf.inputs.items()
                        },
                        "steps": [
                            {
                                "id": s.id,
                                "type": s.type.value,
                                "description": s.description,
                            }
                            for s in wf.steps
                        ],
                    },
                    message=f"Workflow '{name}' v{wf.version} — {len(wf.steps)} steps",
                )
            except WorkflowNotFoundError as exc:
                return WorkflowResponse(
                    action="show",
                    success=False,
                    workflow_name=name,
                    message=str(exc),
                )

        if action == "run":
            if not name:
                return WorkflowResponse(
                    action="run",
                    success=False,
                    message="name is required for action 'run'",
                )
            try:
                execution = engine.run(name, inputs=inputs, approval_handler=approval_handler)
                return WorkflowResponse(
                    action="run",
                    success=execution.status.value == "completed",
                    workflow_name=execution.workflow_name,
                    execution_id=execution.execution_id,
                    status=execution.status.value,
                    data=execution.to_dict(),
                    message=(
                        f"Workflow '{name}' {execution.status.value}"
                        + (f": {execution.error}" if execution.error else "")
                    ),
                )
            except WorkflowNotFoundError as exc:
                return WorkflowResponse(
                    action="run",
                    success=False,
                    workflow_name=name,
                    message=str(exc),
                )
            except (WorkflowValidationError, Exception) as exc:
                return WorkflowResponse(
                    action="run",
                    success=False,
                    workflow_name=name,
                    message=f"Workflow error: {exc}",
                )

        return WorkflowResponse(
            action=action,
            success=False,
            message=f"Unknown action {action!r}. Valid actions: list, show, run",
        )

    def migrate(
        self,
        action: str = "run",
        sources: list[str] | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
        run_id: str = "",
        progress_callback: Any = None,
    ) -> MigrateResponse:
        """
        Import existing project documentation into the MondayOS knowledge base.

        Actions:
            list-sources — list all registered source documents and their status.
            run          — parse and import knowledge candidates from source documents.
            rollback     — remove entries created in a prior run (requires run_id).

        Args:
            action:            One of: list-sources, run, rollback.
            sources:           Source names to process (None = all). Ignored for
                               list-sources and rollback.
            dry_run:           If True, parse and validate but do not write entries.
            overwrite:         If True, re-import entries already in the index.
            run_id:            Prior run_id for rollback action.
            progress_callback: Optional callable(message: str) for progress output.

        Returns:
            MigrateResponse with success=True on success or success=False with
            a descriptive message on failure. Does not raise.
        """
        from migrate import MigrationEngine, UnknownSourceError
        from migrate.errors import RollbackError

        logs_dir = self._config.project_root / "logs" / "migrations"
        engine = MigrationEngine(
            monday=self,
            project_root=self._config.project_root,
            logs_dir=logs_dir,
        )

        if action == "list-sources":
            source_infos = engine.list_sources()
            return MigrateResponse(
                action="list-sources",
                success=True,
                data={
                    "sources": [
                        {
                            "name": s.name,
                            "source_file": s.source_file,
                            "description": s.description,
                            "entry_types": s.entry_types,
                            "exists": engine.source_exists(s.name),
                        }
                        for s in source_infos
                    ],
                    "count": len(source_infos),
                },
                message=f"{len(source_infos)} source(s) registered",
            )

        if action == "run":
            try:
                report = engine.run(
                    sources=sources,
                    dry_run=dry_run,
                    overwrite=overwrite,
                    progress_callback=progress_callback,
                )
                return MigrateResponse(
                    action="run",
                    success=report.failed_count == 0,
                    dry_run=dry_run,
                    run_id=report.run_id,
                    sources_processed=report.sources,
                    candidates_found=report.candidates_found,
                    imported_count=report.imported_count,
                    skipped_count=report.skipped_count,
                    failed_count=report.failed_count,
                    data=report.to_dict(),
                    message=(
                        f"{'[dry-run] ' if dry_run else ''}"
                        f"{report.imported_count} imported, "
                        f"{report.skipped_count} skipped, "
                        f"{report.failed_count} failed"
                        + (f" — {report.failed_count} error(s)" if report.failed_count else "")
                    ),
                )
            except UnknownSourceError as exc:
                return MigrateResponse(
                    action="run",
                    success=False,
                    dry_run=dry_run,
                    message=str(exc),
                )
            except Exception as exc:
                return MigrateResponse(
                    action="run",
                    success=False,
                    dry_run=dry_run,
                    message=f"Migration failed: {exc}",
                )

        if action == "rollback":
            if not run_id:
                return MigrateResponse(
                    action="rollback",
                    success=False,
                    message="run_id is required for action 'rollback'",
                )
            try:
                rollback_report = engine.rollback(run_id)
                return MigrateResponse(
                    action="rollback",
                    success=len(rollback_report.failed) == 0,
                    run_id=run_id,
                    imported_count=len(rollback_report.removed),
                    failed_count=len(rollback_report.failed),
                    data={
                        "removed": rollback_report.removed,
                        "failed": rollback_report.failed,
                    },
                    message=rollback_report.message,
                )
            except RollbackError as exc:
                return MigrateResponse(
                    action="rollback",
                    success=False,
                    run_id=run_id,
                    message=str(exc),
                )
            except Exception as exc:
                return MigrateResponse(
                    action="rollback",
                    success=False,
                    run_id=run_id,
                    message=f"Rollback failed: {exc}",
                )

        return MigrateResponse(
            action=action,
            success=False,
            message=f"Unknown action {action!r}. Valid actions: list-sources, run, rollback",
        )

    def advise(
        self,
        doctor_report: Any = None,
    ) -> AdviseResponse:
        """
        Produce an engineering advisory for this repository.

        Synthesises repository health (Doctor), knowledge store, active tasks,
        and workflow history into a structured advisory — without calling any
        external AI model.

        The advisory answers:
          - What is the current state of the project?
          - What should we build next?
          - What risks exist?
          - What is the expected impact?
          - What is the recommended sprint goal?

        Args:
            doctor_report: Pre-computed DoctorReport to avoid re-running inspection.
                           Pass None to run the full inspection internally.

        Returns:
            AdviseResponse with confidence, sprint_goal, risks, next_actions,
            repository_summary, and the full Advisory dict in `data`.
            Does not raise.
        """
        from advisor.engine import AdvisorEngine

        try:
            engine = AdvisorEngine(
                monday=self,
                project_root=self._config.project_root,
                provider=self.__provider,
            )
            advisory = engine.analyze(doctor_report=doctor_report)
        except Exception as exc:
            return AdviseResponse(
                action="advise",
                success=False,
                message=f"Advisory failed: {exc}",
            )

        return AdviseResponse(
            action="advise",
            success=True,
            confidence=advisory.confidence,
            sprint_goal=advisory.sprint_goal,
            risks=[r.to_dict() for r in advisory.risks[:10]],
            next_actions=[a.to_dict() for a in advisory.next_actions[:10]],
            repository_summary=advisory.repository_summary,
            data=advisory.to_dict(),
            message=f"Confidence: {advisory.confidence:.0%}",
        )

    def doctor(
        self,
        analyzers: list[str] | None = None,
    ) -> DoctorResponse:
        """
        Inspect the repository and return a comprehensive health report.

        Runs all registered analyzers (or a subset if analyzers is provided)
        and aggregates the results into a health score, grade, and ranked
        recommendations.

        Args:
            analyzers: Optional list of analyzer names to run. None runs all.
                       Available: git, tests, code-quality, knowledge,
                       documentation, tasks, config.

        Returns:
            DoctorResponse with health_score (0–100), grade, recommendations,
            and the full report dict in `data`. Does not raise.
        """
        from doctor import RepositoryInspector
        from doctor.finding import Severity

        try:
            inspector = RepositoryInspector(
                project_root=self._config.project_root,
                monday=self,
                analyzer_names=analyzers,
            )
            report = inspector.run()
        except Exception as exc:
            return DoctorResponse(
                action="inspect",
                success=False,
                message=f"Inspection failed: {exc}",
            )

        fbs = report.findings_by_severity
        n_critical = len(fbs[Severity.CRITICAL])
        n_warning = len(fbs[Severity.WARNING])
        n_info = len(fbs[Severity.INFO])

        parts = []
        if n_critical:
            parts.append(f"{n_critical} critical")
        if n_warning:
            parts.append(f"{n_warning} warning(s)")
        if n_info:
            parts.append(f"{n_info} info")
        summary = ", ".join(parts) if parts else "No issues found"

        return DoctorResponse(
            action="inspect",
            success=True,
            health_score=report.health_score,
            grade=report.grade,
            summary=summary,
            recommendations=report.recommendations,
            data=report.to_dict(),
            message=f"Health: {report.health_score}/100 ({report.grade})",
        )

    def project(
        self,
        action: str,
        name: str = "",
        path: str = "",
        description: str = "",
        overwrite: bool = False,
    ) -> ProjectResponse:
        """
        Register, list, retrieve, or remove external projects.

        Actions:
            register — add a new project (requires name and path).
            list     — return all registered projects.
            get      — return metadata for a named project (requires name).
            remove   — remove a project from the registry (requires name).

        Args:
            action:      One of: register, list, get, remove.
            name:        Project name slug (required for register/get/remove).
            path:        Absolute source path (required for register).
            description: Optional human-readable description (for register).
            overwrite:   If True, replace existing registration (for register).

        Returns:
            ProjectResponse with success=True on success or success=False with
            a descriptive message on failure. Does not raise.
        """
        from monday.project import ProjectAlreadyExistsError, ProjectNotFoundError, ProjectRegistry

        registry = ProjectRegistry(self._config.project_root / "config")

        if action == "register":
            if not name or not path:
                return ProjectResponse(
                    action="register",
                    success=False,
                    message="'name' and 'path' are required for action 'register'",
                )
            try:
                entry = registry.register(name, path, description=description, overwrite=overwrite)
                return ProjectResponse(
                    action="register",
                    success=True,
                    project_name=name,
                    data=entry.to_dict(),
                    message=f"Registered project {name!r} → {entry.source_path}",
                )
            except ProjectAlreadyExistsError as exc:
                return ProjectResponse(action="register", success=False, project_name=name, message=str(exc))
            except ValueError as exc:
                return ProjectResponse(action="register", success=False, project_name=name, message=str(exc))

        if action == "list":
            entries = registry.list()
            return ProjectResponse(
                action="list",
                success=True,
                data={
                    "projects": [e.to_dict() for e in entries],
                    "count": len(entries),
                },
                message=f"{len(entries)} project(s) registered",
            )

        if action == "get":
            if not name:
                return ProjectResponse(action="get", success=False, message="'name' is required for action 'get'")
            try:
                entry = registry.get(name)
                return ProjectResponse(
                    action="get",
                    success=True,
                    project_name=name,
                    data=entry.to_dict(),
                    message=f"Project {name!r} — {entry.source_path}",
                )
            except ProjectNotFoundError as exc:
                return ProjectResponse(action="get", success=False, project_name=name, message=str(exc))

        if action == "remove":
            if not name:
                return ProjectResponse(action="remove", success=False, message="'name' is required for action 'remove'")
            try:
                registry.remove(name)
                return ProjectResponse(
                    action="remove",
                    success=True,
                    project_name=name,
                    message=f"Project {name!r} removed from registry",
                )
            except ProjectNotFoundError as exc:
                return ProjectResponse(action="remove", success=False, project_name=name, message=str(exc))

        return ProjectResponse(
            action=action,
            success=False,
            message=f"Unknown action {action!r}. Valid actions: register, list, get, remove",
        )

    def onboard(
        self,
        project_name: str,
        reports_dir: Path | None = None,
    ) -> OnboardResponse:
        """
        Run a full MondayOS onboarding pipeline against a registered project.

        Steps executed:
            1. Look up project in the registry.
            2. Run migrate against the project's documentation.
            3. Run doctor (health inspection).
            4. Run advise (engineering advisory).
            5. Generate a Markdown onboarding report.
            6. Save report to {reports_dir}/{project_name}/ONBOARDING_REPORT.md.

        Args:
            project_name: Name of a registered project (see Monday.project("register")).
            reports_dir:  Directory for reports. Defaults to
                          {mondayos_root}/projects/{project_name}/.

        Returns:
            OnboardResponse with health_score, sprint_goal, report_path, and
            the composite data payload. Does not raise.
        """
        from monday.project import ProjectNotFoundError, ProjectRegistry
        from monday import Monday, MondayConfig

        registry = ProjectRegistry(self._config.project_root / "config")

        try:
            entry = registry.get(project_name)
        except ProjectNotFoundError as exc:
            return OnboardResponse(
                action="onboard",
                success=False,
                project_name=project_name,
                message=str(exc),
            )

        source_path = entry.path
        if not source_path.exists():
            return OnboardResponse(
                action="onboard",
                success=False,
                project_name=project_name,
                message=f"Project source path no longer exists: {source_path}",
            )

        # Create a Monday instance pointing at the external project
        project_monday = Monday(MondayConfig(project_root=source_path))

        # ── Step 1: Migrate ─────────────────────────────────────────────────
        migrate_resp = project_monday.migrate(action="run")
        migrate_summary = migrate_resp.message

        # ── Step 2: Doctor ───────────────────────────────────────────────────
        doctor_resp = project_monday.doctor()

        # ── Step 3: Advise ───────────────────────────────────────────────────
        advise_resp = project_monday.advise()

        # ── Step 4: Generate report ─────────────────────────────────────────
        if reports_dir is None:
            reports_dir = self._config.project_root / "projects" / project_name
        reports_dir = Path(reports_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "ONBOARDING_REPORT.md"

        report_md = _generate_onboarding_report(
            project_name=project_name,
            entry=entry,
            migrate_resp=migrate_resp,
            doctor_resp=doctor_resp,
            advise_resp=advise_resp,
        )
        report_path.write_text(report_md, encoding="utf-8")

        success = doctor_resp.success and advise_resp.success

        return OnboardResponse(
            action="onboard",
            success=success,
            project_name=project_name,
            migrate_summary=migrate_summary,
            health_score=doctor_resp.health_score,
            grade=doctor_resp.grade,
            sprint_goal=advise_resp.sprint_goal,
            confidence=advise_resp.confidence,
            report_path=str(report_path),
            data={
                "migrate": migrate_resp.data,
                "doctor": doctor_resp.data,
                "advisory": advise_resp.data,
            },
            message=(
                f"Onboarding complete — health {doctor_resp.health_score}/100 ({doctor_resp.grade}), "
                f"confidence {advise_resp.confidence:.0%}. "
                f"Report: {report_path}"
            ),
        )

    def execute(
        self,
        task_id: str,
        mode: str = "review",
        policy: str = "prefer-local",
        provider: str = "",
        providers: Any = None,
        autonomous_enabled: bool = False,
        max_tokens: int = 2048,
        extra_context: str = "",
        update_task: bool = True,
    ) -> ExecuteResponse:
        """
        Execute an engineering task by delegating it to an AI provider.

        The Execution Orchestrator coordinates the full pipeline: the advisor
        prioritises, a deterministic planner builds an execution plan, a provider
        is selected by policy, the provider executes through the AIProvider
        abstraction, the result is validated, knowledge is captured, the task is
        updated, and an execution report is persisted to logs/executions/.

        MondayOS does not implement any AI model here — it coordinates providers.

        Args:
            task_id:            ID of the task to execute (e.g. "TASK-0001").
            mode:               Safety mode: "dry-run" | "review" | "autonomous".
                                Default "review" — execute and capture, but stop
                                the task at REVIEW for a human. No autonomous file
                                modification unless mode is "autonomous" AND
                                autonomous_enabled is True.
            policy:             Provider selection policy: "prefer-local" |
                                "lowest-cost" | "highest-capability" | "manual".
            provider:           Explicit provider name override (manual selection).
            providers:          Optional explicit list of AIProvider instances.
                                Defaults to this instance's configured provider.
            autonomous_enabled: Must be True for "autonomous" mode to act.
            max_tokens:         Max output tokens for the provider execution call.

        Returns:
            ExecuteResponse describing the outcome. Does not raise.
        """
        from orchestrator.executor import ExecutionMode, ExecutionOrchestrator, ProviderSelectionPolicy

        try:
            mode_enum = ExecutionMode.from_str(mode)
        except ValueError as exc:
            return ExecuteResponse(action="execute", success=False, task_id=task_id, message=str(exc))
        try:
            policy_enum = ProviderSelectionPolicy.from_str(policy)
        except ValueError as exc:
            return ExecuteResponse(action="execute", success=False, task_id=task_id, message=str(exc))

        if providers is None:
            available = [self.__provider] if self.__provider is not None else []
        else:
            available = list(providers)

        orchestrator = ExecutionOrchestrator(
            monday=self,
            project_root=self._config.project_root,
            providers=available,
            policy=policy_enum,
            mode=mode_enum,
            bus=self.__bus,
            autonomous_enabled=autonomous_enabled,
            manual_provider=provider,
            max_tokens=max_tokens,
            extra_context=extra_context,
            update_task=update_task,
        )

        report = orchestrator.execute(task_id)
        report_path = str(self._config.project_root / "logs" / "executions" / f"{report.execution_id}.json")

        message = report.error or (
            f"{report.status} via {report.provider_used}" if report.provider_used
            else report.status
        )

        return ExecuteResponse(
            action="execute",
            success=report.success,
            task_id=report.task_id,
            execution_id=report.execution_id,
            mode=report.mode,
            provider_used=report.provider_used,
            status=report.status,
            prompt_summary=report.prompt_summary,
            duration_ms=report.duration_ms,
            files_changed=list(report.files_changed),
            knowledge_captured=list(report.knowledge_captured),
            follow_up_tasks=list(report.follow_up_tasks),
            confidence=report.confidence,
            report_path=report_path,
            data=report.to_dict(),
            message=message,
        )

    def agent(self, action: str, **kwargs: Any) -> AgentResponse:
        """
        Manage and run the role-based Multi-Agent Runtime.

        Actions:
            list      — list registered agents (seeds the six defaults on first
                        use). Optional: role.
            register  — register a new agent. Requires: name, role. Optional:
                        provider, capabilities, is_default, description.
            assign    — assign a task to a role. Requires: task_id, role.
            run       — route a task to a role and run it through the Execution
                        Orchestrator under the approval gate. Requires: task_id,
                        role. Optional: provider, policy, mode, autonomous_enabled,
                        approved, requested_actions.
            review    — record a human decision on a run. Requires: run_id,
                        approve (bool). Optional: by, note.
            history   — list past runs. Optional: role, task_id, limit.

        Returns an AgentResponse. Does not raise for expected failures.
        """
        from agents.runtime import AgentRuntime

        runtime = AgentRuntime(
            monday=self,
            project_root=self._config.project_root,
            require_human_approval=self._config.require_human_approval,
        )

        try:
            if action == "list":
                from agents.adapters import availability_for

                agents = runtime.list_agents(role=kwargs.get("role"))
                rows = []
                for a in agents:
                    d = a.to_dict()
                    av = availability_for(a)
                    d["available"] = av.available
                    d["model"] = av.model
                    d["requires"] = av.env_var
                    d["provider_status"] = av.reason
                    rows.append(d)
                return AgentResponse(
                    action="list", success=True,
                    data={"agents": rows, "count": len(rows)},
                    message=f"{len(rows)} agent(s)",
                )

            if action == "register":
                agent = runtime.register(
                    name=kwargs.get("name", ""),
                    role=kwargs.get("role", ""),
                    provider=kwargs.get("provider", ""),
                    capabilities=kwargs.get("capabilities"),
                    is_default=bool(kwargs.get("is_default", False)),
                    description=kwargs.get("description", ""),
                )
                return AgentResponse(
                    action="register", success=True, agent_id=agent.id, role=agent.role,
                    data={"agent": agent.to_dict()},
                    message=f"Registered {agent.id} ({agent.name}) for role {agent.role}",
                )

            if action == "assign":
                r = runtime.assign(
                    task_id=kwargs.get("task_id", ""),
                    role=kwargs.get("role", ""),
                    assigned_by=kwargs.get("assigned_by", "human:cli"),
                )
                return AgentResponse(
                    action="assign", success=r.success, task_id=kwargs.get("task_id", ""),
                    role=kwargs.get("role", ""), data=r.data, message=r.message,
                )

            if action == "run":
                run = runtime.run(
                    task_id=kwargs.get("task_id", ""),
                    role=kwargs.get("role", ""),
                    provider=kwargs.get("provider", ""),
                    policy=kwargs.get("policy", "manual"),
                    mode=kwargs.get("mode", "review"),
                    autonomous_enabled=bool(kwargs.get("autonomous_enabled", False)),
                    approved=bool(kwargs.get("approved", False)),
                    requested_actions=kwargs.get("requested_actions"),
                )
                return AgentResponse(
                    action="run", success=run.success, run_id=run.run_id, task_id=run.task_id,
                    role=run.role, agent_id=run.agent_id, provider_used=run.provider_used,
                    status=run.status, data=run.to_dict(), message=run.message,
                )

            if action == "review":
                run = runtime.review(
                    run_id=kwargs.get("run_id", ""),
                    approve=bool(kwargs.get("approve", False)),
                    by=kwargs.get("by", "human:cli"),
                    note=kwargs.get("note", ""),
                )
                return AgentResponse(
                    action="review", success=True, run_id=run.run_id, task_id=run.task_id,
                    role=run.role, status=run.status, data=run.to_dict(), message=run.message,
                )

            if action == "history":
                runs = runtime.history(
                    role=kwargs.get("role"),
                    task_id=kwargs.get("task_id"),
                    limit=int(kwargs.get("limit", 20)),
                )
                return AgentResponse(
                    action="history", success=True,
                    data={"runs": [r.to_dict() for r in runs], "count": len(runs)},
                    message=f"{len(runs)} run(s)",
                )

            return AgentResponse(
                action=action, success=False,
                message=(
                    f"Unknown action {action!r}. "
                    "Valid actions: list, register, assign, run, review, history"
                ),
            )
        except FileNotFoundError as exc:
            return AgentResponse(action=action, success=False, message=str(exc))
        except (ValueError, LookupError) as exc:
            # UnknownRoleError, AgentExistsError, AgentNotFoundError, bad input.
            return AgentResponse(action=action, success=False, message=str(exc))

    def team(self, action: str = "run", **kwargs: Any) -> TeamResponse:
        """
        Run the agent team over a task: CPO → Lead Engineer → QA → Security →
        Reviewer → human approval.

        Each stage is a logged role run that receives the prior stages' summaries;
        QA / Security / Reviewer can stop the pipeline early with a block verdict.
        On full pass the task is moved to REVIEW for human approval — nothing is
        committed, pushed, or executed live. Review-required is the default and
        only autonomous mode is not offered.

        Actions:
            run     — run the pipeline. Requires: task_id. Optional: provider,
                      mode ("review" | "dry-run"), stage_providers.
            history — list past team runs. Optional: task_id, limit.

        Returns a TeamResponse. Does not raise for expected failures.
        """
        from agents.team import TeamWorkflow

        team = TeamWorkflow(
            monday=self,
            project_root=self._config.project_root,
            require_human_approval=self._config.require_human_approval,
        )

        try:
            if action == "run":
                tr = team.run(
                    task_id=kwargs.get("task_id", ""),
                    provider=kwargs.get("provider", ""),
                    mode=kwargs.get("mode", "review"),
                    stage_providers=kwargs.get("stage_providers"),
                )
                return TeamResponse(
                    action="run", success=tr.success, message=tr.message,
                    team_run_id=tr.team_run_id, task_id=tr.task_id, status=tr.status,
                    stopped_at=tr.stopped_at, approval_run_id=tr.approval_run_id,
                    stages=list(tr.stages), data=tr.to_dict(),
                )

            if action == "history":
                runs = team.history(
                    task_id=kwargs.get("task_id"), limit=int(kwargs.get("limit", 20))
                )
                return TeamResponse(
                    action="history", success=True,
                    message=f"{len(runs)} team run(s)",
                    data={"runs": [r.to_dict() for r in runs], "count": len(runs)},
                )

            return TeamResponse(
                action=action, success=False,
                message=f"Unknown action {action!r}. Valid actions: run, history",
            )
        except (ValueError, LookupError) as exc:
            return TeamResponse(action=action, success=False, message=str(exc))

    def publish(self, action: str = "confluence", **kwargs: Any) -> PublishResponse:
        """
        Publish a MondayOS document to an external destination (Confluence).

        MondayOS stays the system of record — content is read from a local
        file or knowledge entry and pushed to Confluence as a downstream page.
        The MondayOS-doc-ID → Confluence-page-ID mapping and a publish history
        are stored locally under ``logs/publish/``.

        Actions:
            confluence — publish/preview a document. Provide a source via
                         ``doc_id`` (knowledge entry ID or a docs/ file) or
                         ``file`` (explicit path). Optional: ``space_key``,
                         ``parent_id``, ``update_page``, ``title``,
                         ``dry_run`` (preview only, needs no credentials),
                         ``force``. A ``client`` may be injected for testing.
            history    — list past publish events. Optional: ``limit``.

        Returns a PublishResponse. Does not raise for expected failures such as
        missing credentials or an unresolvable document.
        """
        from integrations.confluence import (
            ConfluenceConfig,
            ConfluencePublisher,
        )

        root = self._config.project_root

        try:
            if action == "history":
                publisher = ConfluencePublisher(root, ConfluenceConfig.from_env())
                events = publisher.history(limit=int(kwargs.get("limit", 20)))
                return PublishResponse(
                    action="history", success=True,
                    message=f"{len(events)} publish event(s)",
                    data={"history": events, "count": len(events)},
                )

            if action != "confluence":
                return PublishResponse(
                    action=action, success=False,
                    message=f"Unknown action {action!r}. Valid actions: confluence, history",
                )

            # Resolve the document from the MondayOS system of record.
            try:
                doc = self._resolve_publish_doc(
                    doc_id=kwargs.get("doc_id", ""),
                    file=kwargs.get("file", ""),
                    title=kwargs.get("title", ""),
                )
            except (FileNotFoundError, ValueError) as exc:
                return PublishResponse(action=action, success=False, message=str(exc))

            config = ConfluenceConfig.from_env()
            dry_run = bool(kwargs.get("dry_run", False))
            client = kwargs.get("client")  # test/demo injection

            # A real publish needs credentials up front; a dry run does not.
            if not dry_run and client is None:
                # A space key is only required when creating a brand-new page.
                needs_space = not (
                    kwargs.get("update_page") or self._publish_has_mapping(root, doc.doc_id)
                )
                check = config.credential_check(require_space=needs_space)
                if not check.ok:
                    return PublishResponse(
                        action=action, success=False, doc_id=doc.doc_id,
                        status="failed", message=check.instructions(),
                    )

            publisher = ConfluencePublisher(root, config, client=client)
            result = publisher.publish(
                doc,
                space_key=kwargs.get("space_key", ""),
                parent_id=kwargs.get("parent_id", ""),
                update_page=kwargs.get("update_page", ""),
                dry_run=dry_run,
                force=bool(kwargs.get("force", False)),
            )
            return PublishResponse(
                action=action, success=result.success, message=result.message,
                doc_id=result.doc_id, page_id=result.page_id, url=result.url,
                status=result.action, dry_run=result.dry_run, data=result.to_dict(),
            )
        except (ValueError, LookupError) as exc:
            return PublishResponse(action=action, success=False, message=str(exc))

    def _publish_has_mapping(self, root: Path, doc_id: str) -> bool:
        from integrations.confluence import PublishStore
        return PublishStore(root).get(doc_id) is not None

    def _resolve_publish_doc(self, doc_id: str, file: str, title: str) -> Any:
        """
        Resolve a publish request to concrete content from the system of record.

        Precedence: an explicit ``file`` path, then a ``doc_id`` interpreted
        first as a knowledge entry ID and then as a docs/ file. The returned
        PublishDoc carries a stable doc_id used for the local page mapping.
        """
        from integrations.confluence import PublishDoc

        root = Path(self._config.project_root)

        if file:
            path = Path(file)
            if not path.is_absolute():
                path = root / file
            if not path.exists():
                raise FileNotFoundError(f"File not found: {file}")
            markdown = path.read_text(encoding="utf-8")
            return PublishDoc(
                doc_id=doc_id or file,
                title=title or _first_heading(markdown) or path.stem,
                markdown=markdown,
                source=file,
            )

        if not doc_id:
            raise ValueError("Provide a document to publish: a DOC_ID or --file PATH.")

        # Try a knowledge entry first (system of record).
        try:
            entry = self.__knowledge.get(doc_id)
        except Exception:
            entry = None
        if entry is not None:
            return PublishDoc(
                doc_id=doc_id,
                title=title or entry.title,
                markdown=entry.body,
                source=doc_id,
            )

        # Fall back to a documentation file identified by DOC_ID.
        for candidate in (root / doc_id, root / "docs" / f"{doc_id}.md", root / f"{doc_id}.md"):
            if candidate.exists():
                markdown = candidate.read_text(encoding="utf-8")
                rel = candidate.relative_to(root) if candidate.is_relative_to(root) else candidate
                return PublishDoc(
                    doc_id=doc_id,
                    title=title or _first_heading(markdown) or candidate.stem,
                    markdown=markdown,
                    source=str(rel),
                )

        raise ValueError(
            f"Cannot resolve document {doc_id!r}. Pass a knowledge entry ID, a "
            "docs/ name, or use --file PATH."
        )

    def _remove_knowledge_entry(self, entry_id: str) -> None:
        """Remove a knowledge entry by ID. Used by migration rollback."""
        self.__knowledge.remove(entry_id)

    def growth(self, action: str, **kwargs: Any) -> GrowthResponse:
        """
        Manage project-isolated marketing workspaces and content approvals.

        Every action addresses exactly one registered project, resolved through the
        MondayOS project registry (see docs/GROWTH_BOT.md, ADR-011). Nothing here
        publishes: no connector exists in this increment, and no lifecycle state
        beyond ``approved`` is reachable.

        Actions:
            workspace-init  — create a workspace. Requires: project.
            workspace-get   — read a workspace. Requires: project.
            workspace-list  — list initialized workspace slugs.
            bind            — bind a publishing account by secret NAME. Requires:
                              project, platform, account_id. Optional:
                              account_handle, secret_name.
            bindings        — list a workspace's bindings (redacted).
            credentials     — report whether each binding's secret is in the env.
            content-create  — create a Draft item. Requires: project. Optional: any
                              content field.
            content-get     — read one item. Requires: project, content_id.
            content-list    — list items. Requires: project. Optional: status.
            content-update  — edit an item; resets a stale approval. Requires:
                              project, content_id.
            review          — Draft -> Ready for Review. Requires: project, content_id.
            approve         — record a human approval. Requires: project, content_id,
                              approved_by.
            request-changes — return an item to the author. Requires: project,
                              content_id. Optional: reason.
            cancel          — terminally cancel an item.

        Returns a GrowthResponse. Does not raise for expected failures.
        """
        from growth.errors import GrowthError
        from growth.service import GrowthService

        service = GrowthService(self._config.project_root)
        project = str(kwargs.get("project", ""))
        # The routing keys are passed positionally; splatting them again would collide.
        fields = {k: v for k, v in kwargs.items() if k not in ("project", "content_id")}

        try:
            if action == "workspace-init":
                data = service.init_workspace(project)
                return GrowthResponse(
                    action=action, success=True, project=data["slug"],
                    data={"workspace": data},
                    message=f"Growth workspace created for {data['slug']}.",
                )

            if action == "workspace-get":
                data = service.get_workspace(project)
                return GrowthResponse(
                    action=action, success=True, project=data["slug"],
                    data={"workspace": data},
                    message=f"Growth workspace for {data['slug']}.",
                )

            if action == "workspace-list":
                slugs = service.list_workspaces()
                return GrowthResponse(
                    action=action, success=True,
                    data={"workspaces": slugs, "count": len(slugs)},
                    message=f"{len(slugs)} growth workspace(s).",
                )

            if action == "bind":
                binding = service.bind(
                    project=project,
                    platform=str(kwargs.get("platform", "")),
                    account_id=str(kwargs.get("account_id", "")),
                    account_handle=str(kwargs.get("account_handle", "")),
                    secret_name=str(kwargs.get("secret_name", "")),
                )
                return GrowthResponse(
                    action=action, success=True, project=project,
                    data={"binding": binding},
                    message=f"Bound {binding['platform']} for {project}.",
                )

            if action == "bindings":
                rows = service.list_bindings(project)
                return GrowthResponse(
                    action=action, success=True, project=project,
                    data={"bindings": rows, "count": len(rows)},
                    message=f"{len(rows)} binding(s).",
                )

            if action == "credentials":
                rows = service.credential_status(project, kwargs.get("environ"))
                ready = sum(1 for r in rows if r["ready"])
                return GrowthResponse(
                    action=action, success=True, project=project,
                    data={"credentials": rows, "ready": ready, "count": len(rows)},
                    message=f"{ready}/{len(rows)} credential(s) present.",
                )

            if action == "content-create":
                item = service.create_content(project, **fields)
                return _growth_content_response(action, project, item, "Created")

            if action == "content-get":
                item = service.get_content(project, str(kwargs.get("content_id", "")))
                return _growth_content_response(action, project, item, "Read")

            if action == "content-list":
                items = service.list_content(project, str(kwargs.get("status", "")))
                return GrowthResponse(
                    action=action, success=True, project=project,
                    data={"content": items, "count": len(items)},
                    message=f"{len(items)} content item(s).",
                )

            if action == "content-update":
                item = service.update_content(
                    project, str(kwargs.get("content_id", "")), **fields
                )
                note = "Updated"
                if item["approval_is_stale"] or (
                    item["status"] == "ready-for-review" and not item["approved_fingerprint"]
                ):
                    note = "Updated; approval reset"
                return _growth_content_response(action, project, item, note)

            if action == "review":
                item = service.submit_for_review(
                    project,
                    str(kwargs.get("content_id", "")),
                    changed_by=str(kwargs.get("changed_by", "human:cli")),
                )
                return _growth_content_response(action, project, item, "Ready for review")

            if action == "approve":
                item = service.approve_content(
                    project,
                    str(kwargs.get("content_id", "")),
                    approved_by=str(kwargs.get("approved_by", "human:cli")),
                    reason=str(kwargs.get("reason", "")),
                )
                return _growth_content_response(action, project, item, "Approved")

            if action == "request-changes":
                item = service.request_changes(
                    project,
                    str(kwargs.get("content_id", "")),
                    changed_by=str(kwargs.get("changed_by", "human:cli")),
                    reason=str(kwargs.get("reason", "")),
                )
                return _growth_content_response(action, project, item, "Changes requested")

            if action == "cancel":
                item = service.cancel_content(
                    project,
                    str(kwargs.get("content_id", "")),
                    changed_by=str(kwargs.get("changed_by", "human:cli")),
                    reason=str(kwargs.get("reason", "")),
                )
                return _growth_content_response(action, project, item, "Cancelled")

            return GrowthResponse(
                action=action, success=False, project=project,
                message=(
                    f"Unknown action {action!r}. Valid actions: workspace-init, "
                    "workspace-get, workspace-list, bind, bindings, credentials, "
                    "content-create, content-get, content-list, content-update, "
                    "review, approve, request-changes, cancel"
                ),
            )
        except (GrowthError, ValueError, LookupError) as exc:
            return GrowthResponse(action=action, success=False, project=project, message=str(exc))

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


def _growth_content_response(
    action: str, project: str, item: dict[str, Any], note: str
) -> GrowthResponse:
    """Build a GrowthResponse around one content item payload."""
    return GrowthResponse(
        action=action,
        success=True,
        project=project,
        content_id=str(item.get("id", "")),
        status=str(item.get("status", "")),
        is_approved=bool(item.get("is_approved", False)),
        data={"content": item},
        message=f"{note}: {item.get('id', '')} ({item.get('status', '')}).",
    )


def _new_session_id() -> str:
    return str(uuid.uuid4())


def _first_heading(markdown: str) -> str:
    """Return the text of the first Markdown ATX heading, or "" if none."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


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


def _generate_onboarding_report(
    project_name: str,
    entry: Any,
    migrate_resp: Any,
    doctor_resp: Any,
    advise_resp: Any,
) -> str:
    """
    Generate a complete Markdown onboarding report.

    Combines migrate, doctor, and advisory data into a structured document
    that answers all onboarding questions defined in Initiative 010.
    """
    from datetime import datetime, timezone
    lines: list[str] = []

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines.append(f"# {project_name.title()} — MondayOS Onboarding Report")
    lines.append("")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Source path:** `{entry.source_path}`  ")
    lines.append(f"**Description:** {entry.description or '(none)'}  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Executive Summary ────────────────────────────────────────────────
    lines.append("## Executive Summary")
    lines.append("")
    advisory = advise_resp.data or {}
    repo_summary = advisory.get("repository_summary", "")
    if repo_summary:
        lines.append(repo_summary)
        lines.append("")

    lines.append(
        f"| Metric | Value |"
    )
    lines.append("| --- | --- |")
    lines.append(f"| Health Score | {doctor_resp.health_score}/100 ({doctor_resp.grade}) |")
    lines.append(f"| Advisory Confidence | {advise_resp.confidence:.0%} |")
    migrate_data = migrate_resp.data or {}
    imported = migrate_resp.imported_count
    skipped_count = migrate_resp.skipped_count
    total_kb = imported + skipped_count  # skipped = already in index
    lines.append(f"| Knowledge Entries in Base | {total_kb} ({imported} new this run) |")
    lines.append(f"| Sprint Recommendation | {advise_resp.sprint_goal or '(none)'} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── What MondayOS Knows About This Project ───────────────────────────
    # Infer knowledge base size from the advisory summary (more reliable than
    # the migration run count, which is 0 on repeat runs due to idempotency).
    repo_summary_lower = advisory.get("repository_summary", "").lower()
    kb_size_from_advisory = 0
    import re as _re
    _kb_match = _re.search(r"(\d+) active entr", repo_summary_lower)
    if _kb_match:
        kb_size_from_advisory = int(_kb_match.group(1))
    total_known = imported + migrate_resp.skipped_count + kb_size_from_advisory

    lines.append("## What MondayOS Knows")
    lines.append("")
    if kb_size_from_advisory > 0:
        lines.append(
            f"**Knowledge base:** {kb_size_from_advisory} active entries "
            f"({imported} newly imported this run, {migrate_resp.skipped_count} already present)."
        )
        lines.append("")
        sources = migrate_resp.sources_processed or []
        if sources:
            lines.append("**Source documents:**")
            for src in sources:
                lines.append(f"- `{src}`")
            lines.append("")
    elif imported > 0:
        lines.append(f"**{imported} knowledge entries** were imported from project documentation.")
        lines.append("")
        sources = migrate_resp.sources_processed or []
        if sources:
            lines.append("**Sources imported:**")
            for src in sources:
                lines.append(f"- `{src}`")
            lines.append("")
        skipped = migrate_resp.skipped_count
        if skipped:
            lines.append(f"*{skipped} candidate(s) were skipped (already imported or below confidence threshold).*")
            lines.append("")
    else:
        lines.append("No knowledge entries found. Run `monday migrate` to import project documentation.")
        lines.append("")

    # ── Documentation Inventory ──────────────────────────────────────────
    lines.append("## Documentation Inventory")
    lines.append("")
    doctor_data = doctor_resp.data or {}
    doc_findings = [
        f for r in doctor_data.get("analyzers", [])
        if r.get("name") == "documentation"
        for f in r.get("findings", [])
    ]
    if doc_findings:
        for finding in doc_findings:
            sev = finding.get("severity", "info").upper()
            title = finding.get("title", "")
            detail = finding.get("detail", "")
            rec = finding.get("recommendation", "")
            lines.append(f"- **[{sev}]** {title}")
            if detail:
                lines.append(f"  - {detail}")
            if rec:
                lines.append(f"  - *Recommendation:* {rec}")
    else:
        lines.append("*No documentation findings recorded.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Knowledge Gaps ───────────────────────────────────────────────────
    lines.append("## Knowledge Gaps")
    lines.append("")
    gaps = advisory.get("knowledge_gaps", [])
    doc_gaps = advisory.get("documentation_gaps", [])
    if gaps:
        lines.append("**Missing knowledge types:**")
        for gap in gaps:
            lines.append(f"- {gap}")
        lines.append("")
    if doc_gaps:
        lines.append("**Documentation gaps:**")
        for gap in doc_gaps:
            lines.append(f"- {gap}")
        lines.append("")
    if not gaps and not doc_gaps:
        lines.append("*No knowledge gaps detected.*")
        lines.append("")
    lines.append("---")
    lines.append("")

    # ── Engineering Risks ────────────────────────────────────────────────
    lines.append("## Engineering Risks")
    lines.append("")
    risks = advisory.get("risks", [])
    if risks:
        for risk in risks:
            sev = risk.get("severity", "").upper()
            title = risk.get("title", "")
            impact = risk.get("impact", "")
            rec = risk.get("recommendation", "")
            lines.append(f"### [{sev}] {title}")
            if impact:
                lines.append(f"**Impact:** {impact}")
                lines.append("")
            if rec:
                lines.append(f"**Recommendation:** {rec}")
                lines.append("")
    else:
        lines.append("*No engineering risks detected.*")
        lines.append("")

    # ── Doctor Findings Detail ───────────────────────────────────────────
    lines.append("## Health Report")
    lines.append("")
    lines.append(f"**Score:** {doctor_resp.health_score}/100 ({doctor_resp.grade})")
    lines.append("")
    summary = doctor_resp.summary
    if summary:
        lines.append(f"**Summary:** {summary}")
        lines.append("")
    recs = doctor_resp.recommendations or []
    if recs:
        lines.append("**Top Recommendations:**")
        for rec in recs[:5]:
            lines.append(f"1. {rec}")
        lines.append("")
    lines.append("---")
    lines.append("")

    # ── What Should Be Built Next ────────────────────────────────────────
    lines.append("## What to Build Next")
    lines.append("")
    sprint_goal = advisory.get("sprint_goal", "")
    sprint_rationale = advisory.get("sprint_rationale", "")
    if sprint_goal:
        lines.append(f"**Recommended Sprint Goal:** {sprint_goal}")
        lines.append("")
    if sprint_rationale:
        lines.append(sprint_rationale)
        lines.append("")

    actions = advisory.get("next_actions", [])
    if actions:
        lines.append("**Next Actions (ranked by value):**")
        lines.append("")
        for action in actions:
            title = action.get("title", "")
            rationale = action.get("rationale", "")
            effort = action.get("effort", "")
            cmd = action.get("command", "")
            priority = action.get("priority", 0)
            lines.append(f"{priority}. **{title}** ({effort})")
            if rationale:
                lines.append(f"   - {rationale}")
            if cmd:
                lines.append(f"   - `$ {cmd}`")
        lines.append("")
    lines.append("---")
    lines.append("")

    # ── Technical Debt ───────────────────────────────────────────────────
    lines.append("## Technical Debt")
    lines.append("")
    debt_summary = advisory.get("technical_debt_summary", "")
    debt_items = advisory.get("debt_items", [])
    if debt_summary:
        lines.append(debt_summary)
        lines.append("")
    if debt_items:
        for item in debt_items:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("---")
    lines.append("")

    # ── Recommended Tasks ────────────────────────────────────────────────
    lines.append("## Recommended Tasks to Create")
    lines.append("")
    lines.append(
        "The following tasks are recommended based on the advisory analysis. "
        "Create them with `monday task create --title \"...\" --objective \"...\"` "
        f"against the {project_name} project root."
    )
    lines.append("")
    tasks: list[tuple[str, str, str]] = []
    if risks:
        for risk in [r for r in risks if r.get("severity") in ("critical", "high")][:3]:
            title = f"Fix: {risk.get('title', '')}"
            obj = risk.get("recommendation", "")
            prio = "P0" if risk.get("severity") == "critical" else "P1"
            tasks.append((prio, title, obj))
    if gaps:
        tasks.append(("P2", "Expand knowledge base", "Add missing knowledge types: " + ", ".join(gaps[:3])))
    if doc_gaps:
        tasks.append(("P2", "Close documentation gaps", "Address: " + "; ".join(doc_gaps[:2])))
    if actions:
        top_action = actions[0]
        cmd = top_action.get("command", "")
        tasks.append(("P1", top_action.get("title", ""), top_action.get("rationale", "")))

    if tasks:
        for prio, title, obj in tasks:
            lines.append(f"- **[{prio}]** {title}")
            if obj:
                lines.append(f"  - *Objective:* {obj}")
        lines.append("")
    else:
        lines.append("*No tasks recommended — project appears healthy.*")
        lines.append("")
    lines.append("---")
    lines.append("")

    # ── Footer ───────────────────────────────────────────────────────────
    lines.append("*Generated by MondayOS. Run `monday onboard " + project_name + "` to refresh.*")
    lines.append("")

    return "\n".join(lines)
