"""Brain — top-level MondayOS orchestrator."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class BrainConfig:
    """
    Configuration for the MondayOS Brain.

    All paths default to sensible locations relative to project_root.
    API keys are never stored here — they are loaded from environment
    variables at runtime inside the integration layer.

    TODO: Load from config/brain.toml instead of requiring constructor args.
    TODO: Add model cost limits (per-session, per-day).
    TODO: Add approval gate override map for dev vs. prod environments.
    """

    project_root: Path
    knowledge_dir: Path
    memory_dir: Path
    tasks_dir: Path
    logs_dir: Path
    default_model_tier: str = "standard"
    require_human_approval: bool = True

    @classmethod
    def from_project_root(cls, root: Path) -> BrainConfig:
        """Construct a BrainConfig using standard subdirectory layout."""
        return cls(
            project_root=root,
            knowledge_dir=root / "knowledge",
            memory_dir=root / "memory",
            tasks_dir=root / "tasks",
            logs_dir=root / "logs",
        )


class Brain:
    """
    The top-level coordinator for MondayOS.

    Brain is the single entry point for all MondayOS operation. It wires
    together the task system, memory, knowledge, search, and event bus,
    then delegates task execution to the appropriate agent via the Router.

    Callers (CLI, dashboard, workflow engine) interact only with Brain.
    No caller imports TaskManager, KnowledgeStore, or SearchEngine directly.

    The Brain owns the session lifecycle: it starts the session (initializing
    all subsystems), runs tasks, and ends the session (flushing state).

    TODO: Initialize all subsystems from BrainConfig in __init__().
    TODO: Implement start_session() / end_session() with checkpoint.
    TODO: Implement execute_task(): load task → check gate → search → route → run → log.
    TODO: Implement create_task(): validate → TaskManager.create() → publish event.
    TODO: Wire EventBus so all subsystem events flow through one shared instance.
    TODO: Enforce approval gates before any task execution (core/gates.py — future).
    TODO: Phase 2 — load integration layer (Claude, GPT, Ollama clients).
    """

    def __init__(self, config: BrainConfig) -> None:
        self.config = config

        # Subsystem instances — wired here, not created ad-hoc by callers.
        # Commented out until implementations exist.
        #
        # from events import EventBus
        # from knowledge import KnowledgeStore
        # from memory import ProjectMemory
        # from search import SearchEngine
        # from tasks import TaskManager
        # from brain.router import Router
        #
        # self._bus = EventBus()
        # self._knowledge = KnowledgeStore()
        # self._memory = ProjectMemory(config.memory_dir)
        # self._tasks = TaskManager()
        # self._search = SearchEngine()
        # self._router = Router()

    def execute_task(self, task_id: str) -> None:
        """
        Execute an assigned task end-to-end.

        Execution order:
        1. Load task from TaskManager.
        2. Check approval gate — pause and notify human if HUMAN_APPROVAL required.
        3. Search for relevant knowledge and memory context.
        4. Route task to model via Router.
        5. Call model through the integration layer (Phase 1.2).
        6. Apply model output (code write, knowledge entry, memory update).
        7. Update task status and append work log entry.
        8. Emit TASK_COMPLETED event.
        9. Prompt for knowledge entry creation if warranted.

        TODO: Implement each step above.
        """
        raise NotImplementedError("TODO: implement full task execution lifecycle")

    def create_task(
        self,
        title: str,
        objective: str,
        **kwargs: object,
    ) -> str:
        """
        Create a new task and return its ID.

        TODO: Delegate to TaskManager.create().
        TODO: Publish TASK_CREATED event to EventBus.
        """
        raise NotImplementedError("TODO: delegate to TaskManager.create()")

    def query_knowledge(self, query: str) -> list:
        """
        Search the knowledge base for entries relevant to a query.

        TODO: Delegate to SearchEngine with sources=["knowledge"].
        """
        raise NotImplementedError("TODO: delegate to SearchEngine")
