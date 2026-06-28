"""
Monday CLI — command-line interface to the MondayOS public API.

All commands invoke Monday() with zero business logic in this layer.
No module is bypassed: every call flows through the public API.

Usage:
    monday [--project-root PATH] <command> [options]

Commands:
    status              Show system health and module status.
    ask   "<prompt>"    Answer an engineering question from stored knowledge.
    search "<query>"    Search the knowledge base.
    learn               Add a new knowledge entry (interactive or with flags).
    task  <action>      Manage tasks: list, create, get, complete.
    workflow <action>   Manage and run workflows: list, show, run.

Run `monday <command> --help` for per-command help.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point. Returns exit code (0 = success, 1 = error).

    Accepts an optional argv list so callers (tests, wrappers) can invoke
    the CLI without touching sys.argv.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)  # raises SystemExit for --help / bad args

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monday",
        description="MondayOS — AI Operating System command-line interface.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  monday status\n"
            "  monday ask \"Have we seen this Homebrew error before?\"\n"
            "  monday search \"rate limit\"\n"
            "  monday learn --title \"Homebrew fix\" --type bug --content \"Add brew to PATH.\"\n"
            "  monday task list\n"
            "  monday task create --title \"Fix auth\" --objective \"Resolve auth bug.\"\n"
            "  monday task get TASK-0001\n"
            "  monday task complete TASK-0001\n"
        ),
    )
    parser.add_argument(
        "--project-root",
        metavar="PATH",
        default=".",
        help="Path to the MondayOS project root (default: current directory).",
    )

    subparsers = parser.add_subparsers(title="commands", metavar="<command>")

    _register_status(subparsers)
    _register_ask(subparsers)
    _register_search(subparsers)
    _register_learn(subparsers)
    _register_task(subparsers)
    _register_workflow(subparsers)

    return parser


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _register_status(subparsers: Any) -> None:
    p = subparsers.add_parser("status", help="Show system health and module status.")
    p.set_defaults(func=_cmd_status)


def _cmd_status(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.status()

    print(f"MondayOS v{r.version}")
    print(f"Session : {r.session_id}")
    print(f"Uptime  : {r.uptime_seconds:.2f}s")
    print()
    print(f"Status  : {'healthy' if r.healthy else 'DEGRADED'}")
    print()
    print("Modules:")
    for m in r.modules:
        mark = "ok" if (m.available and m.initialized) else "FAIL"
        print(f"  {mark:4}  {m.name}")

    return 0


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------

def _register_ask(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "ask",
        help="Answer an engineering question from stored knowledge.",
        description=(
            "Ask MondayOS an engineering question. The internal reasoning engine\n"
            "searches stored knowledge and active tasks. No external model calls.\n\n"
            "Supported question types:\n"
            "  Have we seen this before?\n"
            "  What do we know about X?\n"
            "  Show related bugs / ADRs / tasks.\n"
            "  What is currently blocked?\n"
            "  What changed recently?\n"
            "  What should I read first to understand X?"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt", help="The question to ask MondayOS.")
    p.set_defaults(func=_cmd_ask)


def _cmd_ask(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.ask(args.prompt)

    _hr()
    print(r.answer)
    _hr()
    print()
    print(f"Confidence : {r.confidence:.0%}")
    print(f"Engine     : {r.model_used}")

    if r.sources:
        print(f"Sources    : {', '.join(r.sources)}")

    if r.supporting_entries:
        print()
        print("Supporting entries:")
        for e in r.supporting_entries[:5]:
            print(f"  [{e['id']}] {e['title']}  ({e['entry_type']})")

    if r.related_decisions:
        print()
        print("Related decisions:")
        for d in r.related_decisions[:5]:
            print(f"  [{d['id']}] {d['title']}")

    if r.related_tasks:
        print()
        print("Related tasks:")
        for t in r.related_tasks[:5]:
            print(f"  {t['id']}: {t['title']}  [{t['status']}]")

    if r.suggested_next_actions:
        print()
        print("Suggested next actions:")
        for i, action in enumerate(r.suggested_next_actions, 1):
            print(f"  {i}. {action}")

    return 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _register_search(subparsers: Any) -> None:
    p = subparsers.add_parser("search", help="Search the knowledge base.")
    p.add_argument("query", help="Search query.")
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of results to return (default: 10).",
    )
    p.set_defaults(func=_cmd_search)


def _cmd_search(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.search(args.query, limit=args.limit)

    if r.total_found == 0:
        print(f'No results for "{args.query}".')
        return 0

    print(f'Results for "{args.query}" ({r.total_found} found)')
    _hr()
    for i, result in enumerate(r.results, 1):
        tags = ", ".join(result.get("tags", [])) or "—"
        summary = (result.get("summary") or "")[:120]
        print(f"  {i}. [{result['id']}] {result['title']}")
        print(f"     Type: {result['entry_type']}  Tags: {tags}")
        if summary:
            print(f"     {summary}")
        print()

    return 0


# ---------------------------------------------------------------------------
# learn
# ---------------------------------------------------------------------------

def _register_learn(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "learn",
        help="Add a new knowledge entry (interactive or with flags).",
        description=(
            "Add a knowledge entry to MondayOS.\n\n"
            "Non-interactive (all flags provided):\n"
            "  monday learn --title \"Fix\" --type bug --content \"Add brew to PATH.\"\n\n"
            "Pipe content from stdin:\n"
            "  echo \"Fix text.\" | monday learn --title \"Fix\" --type bug\n\n"
            "Interactive (no flags):\n"
            "  monday learn\n"
            "  (prompts for each field)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--title", "-t", metavar="TEXT", default="", help="Entry title.")
    p.add_argument(
        "--type", "-T",
        dest="entry_type",
        metavar="TYPE",
        default="pattern",
        help=(
            "Knowledge type (default: pattern). One of: bug, decision, task, sprint, "
            "feature, lesson, pattern, runbook, documentation, research, weather, experiment."
        ),
    )
    p.add_argument(
        "--tags", "-g",
        metavar="TAG,TAG",
        default="",
        help="Comma-separated list of tags.",
    )
    p.add_argument(
        "--components", "-c",
        metavar="COMP,COMP",
        default="",
        help="Comma-separated list of component names.",
    )
    p.add_argument(
        "--content", "-C",
        metavar="TEXT",
        default=None,
        help=(
            "Entry body text. Omit to read from stdin, or run interactively without flags."
        ),
    )
    p.set_defaults(func=_cmd_learn)


def _cmd_learn(args: argparse.Namespace) -> int:
    content = args.content

    if content is None:
        if not sys.stdin.isatty():
            # Read from pipe
            content = sys.stdin.read().strip()
        else:
            # Interactive guided prompts
            if not args.title:
                args.title = input("Title: ").strip()
            entered_type = input(f"Type [{args.entry_type}]: ").strip()
            if entered_type:
                args.entry_type = entered_type
            if not args.tags:
                args.tags = input("Tags (comma-separated, or Enter to skip): ").strip()
            if not args.components:
                args.components = input("Components (comma-separated, or Enter to skip): ").strip()
            print("Content (Ctrl-D on a blank line when done):")
            lines: list[str] = []
            try:
                while True:
                    lines.append(input())
            except EOFError:
                pass
            content = "\n".join(lines).strip()

    if not content:
        print("Error: no content provided.", file=sys.stderr)
        return 1

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    components = [c.strip() for c in args.components.split(",") if c.strip()] if args.components else []

    monday = _monday(args)
    r = monday.learn(
        content=content,
        title=args.title,
        entry_type=args.entry_type,
        tags=tags,
        components=components,
    )

    if r.accepted:
        print(f"Stored as {r.entry_id}  ({r.entry_type})")
    else:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    return 0


# ---------------------------------------------------------------------------
# task
# ---------------------------------------------------------------------------

def _register_task(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "task",
        help="Manage tasks (list, create, get, complete).",
        description=(
            "Manage MondayOS tasks through the public API.\n\n"
            "examples:\n"
            "  monday task list\n"
            "  monday task list --status in-progress\n"
            "  monday task create --title \"Fix auth\" --objective \"Resolve auth bug.\"\n"
            "  monday task get TASK-0001\n"
            "  monday task complete TASK-0001 --reason \"Merged and deployed.\"\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    task_sub = p.add_subparsers(title="task actions", metavar="<action>")
    task_sub.required = True

    # list
    p_list = task_sub.add_parser("list", help="List active tasks.")
    p_list.add_argument(
        "--status", metavar="STATUS",
        help="Filter by status (backlog, assigned, in-progress, blocked, review).",
    )
    p_list.add_argument(
        "--priority", metavar="PRIORITY",
        help="Filter by priority level (P0, P1, P2, P3).",
    )
    p_list.add_argument(
        "--type", dest="task_type", metavar="TYPE",
        help="Filter by task type (feature, fix, refactor, docs, research, ops, review).",
    )
    p_list.set_defaults(func=_cmd_task_list)

    # create
    p_create = task_sub.add_parser("create", help="Create a new task.")
    p_create.add_argument("--title", required=True, metavar="TEXT", help="Task title.")
    p_create.add_argument("--objective", required=True, metavar="TEXT", help="What the task must achieve.")
    p_create.add_argument(
        "--type", dest="task_type", metavar="TYPE", default="feature",
        help="Task type (default: feature). One of: feature, fix, refactor, docs, research, ops, review.",
    )
    p_create.add_argument(
        "--priority", metavar="PRIORITY", default="P2",
        help="Priority level (default: P2). One of: P0, P1, P2, P3.",
    )
    p_create.add_argument(
        "--created-by", metavar="WHO", default="human:cli",
        help="Creator identifier (default: human:cli).",
    )
    p_create.set_defaults(func=_cmd_task_create)

    # get
    p_get = task_sub.add_parser("get", help="Retrieve a task by ID.")
    p_get.add_argument("task_id", metavar="TASK-ID", help="Task ID to retrieve (e.g. TASK-0001).")
    p_get.set_defaults(func=_cmd_task_get)

    # complete
    p_complete = task_sub.add_parser("complete", help="Mark a task as COMPLETED.")
    p_complete.add_argument("task_id", metavar="TASK-ID", help="Task ID to complete.")
    p_complete.add_argument(
        "--reason", metavar="TEXT", default="",
        help="Completion reason or notes (optional).",
    )
    p_complete.add_argument(
        "--changed-by", metavar="WHO", default="human:cli",
        help="Who is completing the task (default: human:cli).",
    )
    p_complete.set_defaults(func=_cmd_task_complete)


def _cmd_task_list(args: argparse.Namespace) -> int:
    kwargs: dict[str, str] = {}
    if args.status:
        kwargs["status"] = args.status
    if args.priority:
        kwargs["priority"] = args.priority
    if getattr(args, "task_type", None):
        kwargs["task_type"] = args.task_type

    monday = _monday(args)
    r = monday.task("list", **kwargs)

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    tasks = r.data.get("tasks", [])
    count = r.data.get("count", 0)

    if count == 0:
        print("No active tasks.")
        return 0

    print(f"Active tasks ({count})")
    _hr()
    for t in tasks:
        print(f"  {t['id']}  [{t['priority']}] [{t['status']}]  {t['title']}")

    return 0


def _cmd_task_create(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.task(
        "create",
        title=args.title,
        objective=args.objective,
        task_type=args.task_type,
        priority=args.priority,
        created_by=args.created_by,
    )

    if r.success:
        print(f"Created {r.task_id}")
        print(f"  Title    : {r.data.get('title', '')}")
        print(f"  Status   : {r.data.get('status', '')}")
        print(f"  Priority : {r.data.get('priority', '')}")
        print(f"  Type     : {r.data.get('task_type', '')}")
    else:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    return 0


def _cmd_task_get(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.task("get", task_id=args.task_id)

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    d = r.data
    print(f"{d['id']} — {d['title']}")
    _hr()
    print(f"  Status    : {d['status']}")
    print(f"  Priority  : {d['priority']}")
    print(f"  Type      : {d['task_type']}")
    print(f"  Created   : {d['created'][:10]}")
    print(f"  By        : {d['created_by']}")
    print()
    print(f"  Objective : {d['objective']}")
    if d.get("context"):
        print(f"  Context   : {d['context']}")

    return 0


def _cmd_task_complete(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.task(
        "complete",
        task_id=args.task_id,
        reason=args.reason,
        changed_by=args.changed_by,
    )

    if r.success:
        print(f"{r.task_id} marked COMPLETED")
    else:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    return 0


# ---------------------------------------------------------------------------
# workflow
# ---------------------------------------------------------------------------

def _register_workflow(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "workflow",
        help="Manage and run workflows (list, show, run).",
        description=(
            "Run predefined multi-step workflows or inspect available workflow definitions.\n\n"
            "examples:\n"
            "  monday workflow list\n"
            "  monday workflow show implement-function\n"
            "  monday workflow run implement-function --var function_name=parse_config\n"
            "  monday workflow run implement-function "
            "--var function_name=validate_input --var component=tasks\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    wf_sub = p.add_subparsers(title="workflow actions", metavar="<action>")
    wf_sub.required = True

    # list
    p_list = wf_sub.add_parser("list", help="List all available workflow definitions.")
    p_list.set_defaults(func=_cmd_workflow_list)

    # show
    p_show = wf_sub.add_parser("show", help="Show steps for a named workflow.")
    p_show.add_argument("name", metavar="NAME", help="Workflow name (e.g. implement-function).")
    p_show.set_defaults(func=_cmd_workflow_show)

    # run
    p_run = wf_sub.add_parser("run", help="Execute a workflow end-to-end.")
    p_run.add_argument("name", metavar="NAME", help="Workflow name to run.")
    p_run.add_argument(
        "--var",
        action="append",
        metavar="KEY=VALUE",
        default=[],
        dest="vars",
        help=(
            "Input variable in KEY=VALUE format. Repeat for multiple variables. "
            "Example: --var function_name=parse_config --var component=tasks"
        ),
    )
    p_run.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help="Auto-approve all human_approval gates (non-interactive).",
    )
    p_run.set_defaults(func=_cmd_workflow_run)


def _cmd_workflow_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.workflow("list")

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    workflows = r.data.get("workflows", [])
    count = r.data.get("count", 0)

    if count == 0:
        print("No workflows found.")
        return 0

    print(f"Available workflows ({count})")
    _hr()
    for wf in workflows:
        steps_label = f"{wf['steps']} steps"
        print(f"  {wf['name']}  v{wf['version']}  ({steps_label})")
        if wf.get("description"):
            desc = wf["description"].strip().replace("\n", " ")[:100]
            print(f"    {desc}")
    return 0


def _cmd_workflow_show(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.workflow("show", name=args.name)

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    d = r.data
    print(f"Workflow: {d['name']}  v{d['version']}")
    if d.get("description"):
        print(f"  {d['description'].strip().replace(chr(10), ' ')[:120]}")
    print()

    if d.get("inputs"):
        print("Inputs:")
        for k, spec in d["inputs"].items():
            req_label = "(required)" if spec["required"] else f"(default: {spec['default'] or 'none'})"
            print(f"  {k}  {req_label}")
            if spec.get("description"):
                print(f"    {spec['description']}")
        print()

    print("Steps:")
    for i, step in enumerate(d.get("steps", []), 1):
        print(f"  {i:2}. [{step['type']:16}] {step['id']}")
        if step.get("description"):
            print(f"        {step['description']}")

    return 0


def _cmd_workflow_run(args: argparse.Namespace) -> int:
    # Parse --var KEY=VALUE pairs
    inputs: dict[str, str] = {}
    for var in (args.vars or []):
        if "=" not in var:
            print(f"Error: --var must be KEY=VALUE, got: {var!r}", file=sys.stderr)
            return 1
        k, _, v = var.partition("=")
        inputs[k.strip()] = v.strip()

    # Build approval handler
    handler = None
    if args.yes:
        handler = lambda msg, ctx: True  # noqa: E731

    monday = _monday(args)
    r = monday.workflow("run", name=args.name, inputs=inputs or None, approval_handler=handler)

    if r.status == "cancelled":
        print(f"Workflow cancelled: {r.message}")
        return 1

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    print(f"Workflow '{r.workflow_name}' completed")
    print(f"  Execution : {r.execution_id}")

    steps = r.data.get("steps", [])
    if steps:
        print()
        print("Steps:")
        for s in steps:
            mark = "ok" if s["status"] == "completed" else s["status"].upper()
            print(f"  {mark:8}  {s['step_id']}  ({s['step_type']})")

    return 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _monday(args: argparse.Namespace) -> Any:
    """Instantiate Monday with the project root from CLI args."""
    from monday import Monday, MondayConfig
    return Monday(MondayConfig(project_root=Path(args.project_root)))


def _hr() -> None:
    """Print a 60-character horizontal rule."""
    print("─" * 60)
