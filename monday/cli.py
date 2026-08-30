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
    _register_migrate(subparsers)
    _register_doctor(subparsers)
    _register_advise(subparsers)
    _register_project(subparsers)
    _register_onboard(subparsers)
    _register_execute(subparsers)
    _register_agent(subparsers)
    _register_team(subparsers)
    _register_publish(subparsers)
    _register_growth(subparsers)

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
# migrate
# ---------------------------------------------------------------------------

def _register_migrate(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "migrate",
        help="Import existing project documentation into the knowledge base.",
        description=(
            "Convert existing project documents into MondayOS Knowledge Objects.\n\n"
            "examples:\n"
            "  monday migrate                        # import all sources\n"
            "  monday migrate --dry-run              # preview without writing\n"
            "  monday migrate changelog              # import CHANGELOG.md only\n"
            "  monday migrate session-log decisions  # import two sources\n"
            "  monday migrate list                   # list registered sources\n"
            "  monday migrate rollback <run-id>      # undo a prior run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "sources",
        nargs="*",
        metavar="SOURCE",
        help=(
            "Source name(s) to import. Special values: 'list' (show sources), "
            "'rollback' (undo a run, requires --run-id). "
            "Omit to import all sources."
        ),
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        default=False,
        help="Parse and validate but do not write any entries.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Re-import entries that are already in the knowledge base.",
    )
    p.add_argument(
        "--run-id",
        metavar="ID",
        default="",
        help="Run ID prefix for rollback (first 8 characters is sufficient).",
    )
    p.add_argument(
        "--quiet", "-q",
        action="store_true",
        default=False,
        help="Suppress per-candidate progress output.",
    )
    p.set_defaults(func=_cmd_migrate)


def _cmd_migrate(args: argparse.Namespace) -> int:
    sources = list(args.sources) if args.sources else []

    # Detect special subcommands passed as positional args
    if sources == ["list"]:
        return _cmd_migrate_list(args)
    if sources and sources[0] == "rollback":
        return _cmd_migrate_rollback(args)

    # Normal run
    source_names = sources or None
    monday = _monday(args)

    progress = None if args.quiet else lambda msg: print(msg)

    r = monday.migrate(
        action="run",
        sources=source_names,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        progress_callback=progress,
    )

    print()
    dry_label = "[dry-run] " if args.dry_run else ""
    print(f"{dry_label}Migration complete  (run: {r.run_id[:8] if r.run_id else '—'})")
    _hr()
    print(f"  Candidates found : {r.candidates_found}")
    print(f"  Imported         : {r.imported_count}")
    print(f"  Skipped          : {r.skipped_count}")
    if r.failed_count:
        print(f"  Failed           : {r.failed_count}")
        # Show failed entries from the report
        failed = r.data.get("failed", [])
        for f in failed[:10]:
            print(f"    ✗ {f.get('source_ref', '?')}: {f.get('error', '')[:80]}")

    if not r.success and r.failed_count > 0:
        return 1
    return 0


def _cmd_migrate_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.migrate(action="list-sources")

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    sources = r.data.get("sources", [])
    print(f"Registered sources ({r.data.get('count', 0)})")
    _hr()
    for s in sources:
        exists_mark = "ok  " if s["exists"] else "MISS"
        types = ", ".join(s["entry_types"])
        print(f"  {exists_mark}  {s['name']:20}  {s['source_file']}")
        print(f"          types: {types}")
        if s.get("description"):
            print(f"          {s['description'][:100]}")
    return 0


def _cmd_migrate_rollback(args: argparse.Namespace) -> int:
    run_id = args.run_id
    if not run_id:
        # Try to get it from positional args: migrate rollback <id>
        sources = list(args.sources) if args.sources else []
        if len(sources) >= 2:
            run_id = sources[1]
    if not run_id:
        print("Error: --run-id is required for rollback", file=sys.stderr)
        return 1

    monday = _monday(args)
    r = monday.migrate(action="rollback", run_id=run_id)

    if r.success:
        print(f"Rollback complete: removed {r.imported_count} entries")
    else:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def _register_doctor(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "doctor",
        help="Inspect repository health and surface engineering risks.",
        description=(
            "Run a comprehensive repository health check.\n\n"
            "Analyzers: git, tests, code-quality, knowledge, documentation, tasks, config\n\n"
            "examples:\n"
            "  monday doctor\n"
            "  monday doctor --json\n"
            "  monday doctor --verbose\n"
            "  monday doctor --only git tests\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--json", "-j",
        action="store_true",
        default=False,
        help="Output machine-readable JSON instead of the human-readable report.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Show details for every finding, including passing checks.",
    )
    p.add_argument(
        "--only",
        nargs="+",
        metavar="ANALYZER",
        default=None,
        help=(
            "Run only the specified analyzer(s). "
            "Choices: git tests code-quality knowledge documentation tasks config"
        ),
    )
    p.set_defaults(func=_cmd_doctor)


def _cmd_doctor(args: argparse.Namespace) -> int:
    import json as _json

    monday = _monday(args)
    r = monday.doctor(analyzers=args.only)

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    if args.json:
        print(_json.dumps(r.data, indent=2))
        return 0 if r.health_score >= 60 else 1

    return _print_doctor_report(r, verbose=args.verbose)


def _print_doctor_report(r: Any, *, verbose: bool) -> int:
    from doctor.finding import Severity

    report = r.data
    fbs_raw = {
        "critical": [f for f in _all_findings(report) if f["severity"] == "critical"],
        "warning":  [f for f in _all_findings(report) if f["severity"] == "warning"],
        "info":     [f for f in _all_findings(report) if f["severity"] == "info"],
        "ok":       [f for f in _all_findings(report) if f["severity"] == "ok"],
    }

    # Header
    print()
    print("MondayOS Doctor — Repository Health Report")
    _hr()
    score = report["health_score"]
    grade = report["grade"]
    bar = _score_bar(score)
    print(f"Health Score : {score}/100  {bar}  ({grade})")
    print(f"Generated    : {report.get('generated_at', '')[:19].replace('T', ' ')} UTC")
    elapsed = report.get("total_duration_ms", 0)
    print(f"Duration     : {elapsed:.0f}ms")
    print()

    # Sections by severity
    for sev_key, label, icon in [
        ("critical", "CRITICAL", "✗"),
        ("warning",  "WARNINGS", "⚠"),
        ("info",     "INFO",     "·"),
    ]:
        findings = fbs_raw[sev_key]
        if not findings:
            continue
        print(f"{label} ({len(findings)})")
        _hr()
        for f in findings:
            cat = f["category"].upper()
            print(f"  {icon} [{cat}] {f['title']}")
            if verbose and f.get("detail"):
                for line in f["detail"].splitlines():
                    print(f"      {line}")
            if f.get("recommendation"):
                print(f"      → {f['recommendation']}")
        print()

    if verbose:
        ok_findings = fbs_raw["ok"]
        if ok_findings:
            print(f"PASSING ({len(ok_findings)})")
            _hr()
            for f in ok_findings:
                cat = f["category"].upper()
                print(f"  ✓ [{cat}] {f['title']}")
            print()

    # Recommendations
    recs = report.get("recommendations", [])
    if recs:
        print(f"RECOMMENDATIONS (top {min(len(recs), 5)})")
        _hr()
        for i, rec in enumerate(recs[:5], 1):
            print(f"  {i}. {rec}")
        print()

    # Footer
    n_crit = len(fbs_raw["critical"])
    n_warn = len(fbs_raw["warning"])
    if n_crit == 0 and n_warn == 0:
        print("  All checks passing. Repository looks healthy.")
    elif n_crit > 0:
        print(f"  {n_crit} critical issue(s) require attention.")
    else:
        print(f"  {n_warn} warning(s) should be addressed.")
    print()

    return 0 if score >= 60 else 1


def _all_findings(report: Any) -> list[Any]:
    """Flatten findings from all analyzers in the report dict."""
    out = []
    for analyzer in report.get("analyzers", []):
        out.extend(analyzer.get("findings", []))
    return out


def _score_bar(score: int, width: int = 20) -> str:
    filled = round(score / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


# ---------------------------------------------------------------------------
# advise
# ---------------------------------------------------------------------------

_WIDE = 64  # report column width


def _register_advise(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "advise",
        help="Engineering advisory: risks, next actions, sprint goal.",
        description=(
            "Produce an engineering advisory by synthesising repository health,\n"
            "knowledge, tasks, and workflow history.\n\n"
            "examples:\n"
            "  monday advise\n"
            "  monday advise --json\n"
            "  monday advise --brief\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--json", "-j",
        action="store_true",
        default=False,
        help="Output machine-readable JSON instead of the human-readable advisory.",
    )
    p.add_argument(
        "--brief", "-b",
        action="store_true",
        default=False,
        help="Print a condensed single-screen summary (risks + top action only).",
    )
    p.set_defaults(func=_cmd_advise)


def _cmd_advise(args: argparse.Namespace) -> int:
    import json as _json

    monday = _monday(args)
    r = monday.advise()

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    if args.json:
        print(_json.dumps(r.data, indent=2))
        return 0

    if args.brief:
        return _print_brief(r)

    return _print_advisory(r)


def _print_advisory(r: Any) -> int:
    advisory = r.data
    conf_pct = f"{advisory.get('confidence', 0):.0%}"
    score = advisory.get("health_score", 0)
    grade = advisory.get("health_grade", "")

    # ══ Header ══════════════════════════════════════════════════════════
    print()
    print("═" * _WIDE)
    import datetime as _dt
    date_str = _dt.datetime.now().strftime("%Y-%m-%d")
    print(f"  ENGINEERING ADVISORY  —  {date_str}")
    print("═" * _WIDE)
    print(f"  Confidence {conf_pct}  ·  Health {score}/100 ({grade})")
    print("═" * _WIDE)

    # ── Repository Summary ───────────────────────────────────────────────
    summary = advisory.get("repository_summary", "")
    if summary:
        print()
        print("REPOSITORY SUMMARY")
        _thin()
        _wrap(summary, indent=2)

    # ── Top Risks ────────────────────────────────────────────────────────
    risks = advisory.get("risks", [])
    if risks:
        print()
        print("TOP RISKS")
        _thin()
        for risk in risks[:5]:
            sev = risk["severity"].upper()
            bullet = "●" if risk["severity"] == "critical" else "○"
            print(f"  {bullet} [{sev}] {risk['title']}")
            if risk.get("impact"):
                _wrap(risk["impact"], indent=6)
            if risk.get("recommendation"):
                cmd = risk.get("recommendation", "")
                print(f"      → {cmd}")

    # ── Next Actions ─────────────────────────────────────────────────────
    actions = advisory.get("next_actions", [])
    if actions:
        print()
        print("NEXT ACTIONS  (ranked by value)")
        _thin()
        for action in actions[:6]:
            effort = action.get("effort", "")
            cmd = action.get("command", "")
            cat = action.get("category", "")
            pri = action.get("priority", "")
            print(f"  {pri}. {action['title']:<40} [{cat}]  ~{effort}")
            if cmd:
                print(f"     $ {cmd}")

    # ── Sprint Goal ──────────────────────────────────────────────────────
    sprint_goal = advisory.get("sprint_goal", "")
    sprint_rationale = advisory.get("sprint_rationale", "")
    if sprint_goal:
        print()
        print("RECOMMENDED SPRINT GOAL")
        _thin()
        print(f'  "{sprint_goal}"')
        if sprint_rationale:
            print()
            _wrap(sprint_rationale, indent=2)

    # ── Debt + Gaps (two-column) ─────────────────────────────────────────
    debt_items = advisory.get("debt_items", [])
    knowledge_gaps = advisory.get("knowledge_gaps", [])
    doc_gaps = advisory.get("documentation_gaps", [])

    has_debt = bool(debt_items)
    has_gaps = bool(knowledge_gaps or doc_gaps)

    if has_debt or has_gaps:
        print()
        left_header = "TECHNICAL DEBT" if has_debt else ""
        right_header = "KNOWLEDGE & DOC GAPS" if has_gaps else ""

        col = (_WIDE // 2) - 2

        def _pad(s: str) -> str:
            return s[:col].ljust(col)

        print(f"  {_pad(left_header)}  {right_header}")
        _thin()

        gap_items = knowledge_gaps[:4] + [f"[doc] {g}" for g in doc_gaps[:3]]
        max_rows = max(len(debt_items[:5]), len(gap_items))
        for i in range(max_rows):
            left = f"• {debt_items[i][:col - 2]}" if i < len(debt_items) else ""
            right = f"• {gap_items[i][:col - 2]}" if i < len(gap_items) else ""
            print(f"  {_pad(left)}  {right}")

    # ── Footer ───────────────────────────────────────────────────────────
    sources = advisory.get("data_sources", advisory.get("data_sources", []))
    print()
    print("═" * _WIDE)
    sources_label = " + ".join(sources) if sources else "doctor"
    print(f"  Confidence: {conf_pct}  ·  Sources: {sources_label}")
    print(f"  Run `monday advise --json` for machine-readable output.")
    print("═" * _WIDE)
    print()

    return 0


def _print_brief(r: Any) -> int:
    advisory = r.data
    conf_pct = f"{advisory.get('confidence', 0):.0%}"
    score = advisory.get("health_score", 0)
    grade = advisory.get("health_grade", "")

    print(f"\nHealth {score}/100 ({grade})  ·  Confidence {conf_pct}\n")

    risks = advisory.get("risks", [])
    if risks:
        print("Risks:")
        for risk in risks[:3]:
            sev = risk["severity"].upper()
            print(f"  [{sev}] {risk['title']}")

    sprint_goal = advisory.get("sprint_goal", "")
    if sprint_goal:
        print(f'\nSprint: "{sprint_goal}"')

    actions = advisory.get("next_actions", [])
    if actions:
        top = actions[0]
        cmd = top.get("command", "")
        print(f"\nTop action: {top['title']}")
        if cmd:
            print(f"  $ {cmd}")

    print()
    return 0


def _wrap(text: str, indent: int = 0, width: int = _WIDE) -> None:
    """Word-wrap text to width, printing with the given indent."""
    prefix = " " * indent
    avail = width - indent
    words = text.split()
    line: list[str] = []
    for word in words:
        if sum(len(w) + 1 for w in line) + len(word) > avail:
            print(prefix + " ".join(line))
            line = [word]
        else:
            line.append(word)
    if line:
        print(prefix + " ".join(line))


def _thin() -> None:
    print("─" * _WIDE)


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------

def _register_project(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "project",
        help="Register and manage external projects.",
        description=(
            "Register external repositories so MondayOS can run doctor,\n"
            "migrate, and advise against them.\n\n"
            "examples:\n"
            "  monday project register weatherbot /path/to/WeatherBot --description \"Weather CLI\"\n"
            "  monday project list\n"
            "  monday project get weatherbot\n"
            "  monday project remove weatherbot\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    project_sub = p.add_subparsers(title="project actions", metavar="<action>")

    # register
    reg = project_sub.add_parser("register", help="Register a new project.")
    reg.add_argument("name", help="Unique project name (slug).")
    reg.add_argument("path", help="Absolute path to the project source directory.")
    reg.add_argument("--description", default="", help="Human-readable description.")
    reg.add_argument("--overwrite", action="store_true", help="Replace existing entry.")
    reg.set_defaults(func=_cmd_project_register)

    # list
    ls = project_sub.add_parser("list", help="List all registered projects.")
    ls.set_defaults(func=_cmd_project_list)

    # get
    get = project_sub.add_parser("get", help="Show details for a registered project.")
    get.add_argument("name", help="Project name.")
    get.set_defaults(func=_cmd_project_get)

    # remove
    rm = project_sub.add_parser("remove", help="Remove a project from the registry.")
    rm.add_argument("name", help="Project name.")
    rm.set_defaults(func=_cmd_project_remove)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def _cmd_project_register(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.project(
        "register",
        name=args.name,
        path=args.path,
        description=args.description,
        overwrite=args.overwrite,
    )
    if r.success:
        print(f"Registered: {r.project_name}")
        print(f"  Path: {r.data.get('source_path', '')}")
        if r.data.get("description"):
            print(f"  Desc: {r.data['description']}")
        print(f"  At:   {r.data.get('registered_at', '')}")
    else:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return 0


def _cmd_project_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.project("list")
    projects = r.data.get("projects", [])
    if not projects:
        print("No projects registered.")
        print("Use: monday project register <name> <path>")
        return 0
    print(f"\n{'Name':<20}  {'Path':<50}  Description")
    print("─" * 90)
    for p in projects:
        name = p.get("name", "")
        path = p.get("source_path", "")
        desc = p.get("description", "")
        # Truncate for display
        if len(path) > 48:
            path = "…" + path[-47:]
        print(f"{name:<20}  {path:<50}  {desc}")
    print()
    return 0


def _cmd_project_get(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.project("get", name=args.name)
    if r.success:
        d = r.data
        print(f"\nProject: {d.get('name', '')}")
        print(f"  Path       : {d.get('source_path', '')}")
        print(f"  Description: {d.get('description', '') or '(none)'}")
        print(f"  Registered : {d.get('registered_at', '')}")
        print()
    else:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return 0


def _cmd_project_remove(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.project("remove", name=args.name)
    if r.success:
        print(r.message)
    else:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------

def _register_onboard(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "onboard",
        help="Run a full onboarding pipeline against a registered project.",
        description=(
            "Runs migrate, doctor, and advise against a registered project,\n"
            "then generates a comprehensive Markdown onboarding report.\n\n"
            "examples:\n"
            "  monday onboard weatherbot\n"
            "  monday onboard weatherbot --output ./reports\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("project_name", help="Registered project name.")
    p.add_argument(
        "--output",
        metavar="DIR",
        default="",
        help="Directory for the onboarding report (default: projects/<name>/ inside MondayOS).",
    )
    p.set_defaults(func=_cmd_onboard)


def _cmd_onboard(args: argparse.Namespace) -> int:
    monday = _monday(args)

    print(f"\nOnboarding project: {args.project_name}")
    print("  Step 1/3: Migrating documentation …")
    sys.stdout.flush()

    reports_dir = Path(args.output) if args.output else None
    r = monday.onboard(args.project_name, reports_dir=reports_dir)

    if not r.success and not r.report_path:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    print(f"  Step 2/3: Migration — {r.migrate_summary}")
    print(f"  Step 3/3: Analysis complete")
    print()
    print("═" * 64)
    print(f"  ONBOARDING COMPLETE — {r.project_name.upper()}")
    print("═" * 64)
    print(f"  Health Score  : {r.health_score}/100 ({r.grade})")
    print(f"  Confidence    : {r.confidence:.0%}")
    print(f"  Sprint Goal   : {r.sprint_goal}")
    print("─" * 64)
    print(f"  Report        : {r.report_path}")
    print("═" * 64)
    print()

    return 0 if r.success else 1


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

def _register_execute(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "execute",
        help="Execute a task by delegating it to an AI provider.",
        description=(
            "Run the Execution Orchestrator against a task. The advisor\n"
            "prioritises, a plan is built, a provider is selected by policy,\n"
            "the provider executes through the abstraction, the result is\n"
            "validated, knowledge is captured, the task is updated, and an\n"
            "execution report is persisted.\n\n"
            "Safety modes:\n"
            "  --dry-run        plan + select provider, no provider call, no changes\n"
            "  (default)        review-required: execute, capture, stop at REVIEW\n"
            "  --enable-autonomous --mode autonomous   complete the task automatically\n\n"
            "examples:\n"
            "  monday execute TASK-0001\n"
            "  monday execute TASK-0001 --dry-run\n"
            "  monday execute TASK-0001 --policy highest-capability\n"
            "  monday execute TASK-0001 --provider ollama\n"
            "  monday execute TASK-0001 --mode autonomous --enable-autonomous\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("task_id", metavar="TASK-ID", help="Task ID to execute (e.g. TASK-0001).")
    p.add_argument(
        "--mode",
        metavar="MODE",
        default="review",
        help="Execution mode: dry-run | review | autonomous (default: review).",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        default=False,
        help="Shortcut for --mode dry-run (plan only, no provider call, no changes).",
    )
    p.add_argument(
        "--policy",
        metavar="POLICY",
        default="prefer-local",
        help=(
            "Provider selection policy: prefer-local | lowest-cost | "
            "highest-capability | manual (default: prefer-local)."
        ),
    )
    p.add_argument(
        "--provider",
        metavar="NAME",
        default="",
        help="Explicit provider override (e.g. anthropic, openai, ollama).",
    )
    p.add_argument(
        "--enable-autonomous",
        action="store_true",
        default=False,
        help="Explicitly permit autonomous mode to complete tasks. Required for --mode autonomous.",
    )
    p.add_argument(
        "--json", "-j",
        action="store_true",
        default=False,
        help="Output the execution report as JSON.",
    )
    p.set_defaults(func=_cmd_execute)


def _cmd_execute(args: argparse.Namespace) -> int:
    import json as _json

    mode = "dry-run" if args.dry_run else args.mode

    monday = _monday(args)
    r = monday.execute(
        args.task_id,
        mode=mode,
        policy=args.policy,
        provider=args.provider,
        autonomous_enabled=args.enable_autonomous,
    )

    if args.json:
        print(_json.dumps(r.data, indent=2))
        return 0 if r.success else 1

    print()
    print("═" * 64)
    print(f"  EXECUTION — {r.task_id}")
    print("═" * 64)
    print(f"  Mode        : {r.mode}")
    print(f"  Status      : {r.status}")
    print(f"  Provider    : {r.provider_used or '(none)'}")
    if r.prompt_summary:
        print(f"  Prompt      : {r.prompt_summary}")
    print(f"  Duration    : {r.duration_ms:.0f}ms")
    if r.status not in ("dry-run",):
        print(f"  Confidence  : {r.confidence:.0%}")
    if r.knowledge_captured:
        print(f"  Knowledge   : {', '.join(r.knowledge_captured)}")
    if r.follow_up_tasks:
        print(f"  Follow-ups  : {', '.join(r.follow_up_tasks)}")
    print("─" * 64)
    if not r.success and r.message:
        print(f"  {r.message}")
        print("─" * 64)
    print(f"  Report      : {r.report_path}")
    print("═" * 64)
    print()

    return 0 if r.success else 1


# ---------------------------------------------------------------------------
# agent — the Multi-Agent Runtime
# ---------------------------------------------------------------------------

def _register_agent(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "agent",
        help="Role-based agent runtime: registry, routing, runs, review.",
        description=(
            "Route work to a ROLE (cpo, lead-engineer, qa, security, research,\n"
            "reviewer), not a specific model. The registry resolves the role to\n"
            "an agent + provider; runs go through the Execution Orchestrator under\n"
            "a review-required approval gate; every run is logged and reviewable.\n\n"
            "examples:\n"
            "  monday agent list\n"
            "  monday agent register --name \"Claude Code\" --role lead-engineer\n"
            "  monday agent assign TASK-0001 --role qa\n"
            "  monday agent run TASK-0001 --role lead-engineer\n"
            "  monday agent run TASK-0001 --role lead-engineer --provider fake\n"
            "  monday agent review run-abc123 --approve\n"
            "  monday agent history --role qa\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    agent_sub = p.add_subparsers(title="agent actions", metavar="<action>")
    agent_sub.required = True

    # list
    p_list = agent_sub.add_parser("list", help="List registered agents.")
    p_list.add_argument("--role", metavar="ROLE", help="Filter by role slug.")
    p_list.set_defaults(func=_cmd_agent_list)

    # register
    p_reg = agent_sub.add_parser("register", help="Register a new agent.")
    p_reg.add_argument("--name", required=True, metavar="TEXT", help="Agent name.")
    p_reg.add_argument("--role", required=True, metavar="ROLE", help="Role slug.")
    p_reg.add_argument("--provider", metavar="NAME", default="", help="Provider (default: role default).")
    p_reg.add_argument("--default", dest="is_default", action="store_true", help="Make this the role's default agent.")
    p_reg.add_argument("--description", metavar="TEXT", default="", help="Optional description.")
    p_reg.set_defaults(func=_cmd_agent_register)

    # assign
    p_assign = agent_sub.add_parser("assign", help="Assign a task to a role.")
    p_assign.add_argument("task_id", metavar="TASK-ID", help="Task to assign.")
    p_assign.add_argument("--role", required=True, metavar="ROLE", help="Role slug to assign to.")
    p_assign.add_argument("--assigned-by", metavar="WHO", default="human:cli", help="Who is assigning (default: human:cli).")
    p_assign.set_defaults(func=_cmd_agent_assign)

    # run
    p_run = agent_sub.add_parser("run", help="Run a task via a role.")
    p_run.add_argument("task_id", metavar="TASK-ID", help="Task to run.")
    p_run.add_argument("--role", required=True, metavar="ROLE", help="Role slug to route to.")
    p_run.add_argument("--provider", metavar="NAME", default="", help="Provider override (e.g. fake, anthropic).")
    p_run.add_argument("--policy", metavar="POLICY", default="manual", help="Provider selection policy (default: manual).")
    p_run.add_argument("--mode", metavar="MODE", default="review", help="dry-run | review | autonomous (default: review).")
    p_run.add_argument("--dry-run", "-n", action="store_true", help="Shortcut for --mode dry-run.")
    p_run.add_argument("--autonomous", action="store_true", help="Shortcut for --mode autonomous.")
    p_run.add_argument("--enable-autonomous", action="store_true", help="Explicitly permit autonomous mode.")
    p_run.add_argument("--approve", action="store_true", help="Provide human approval for this run.")
    p_run.add_argument(
        "--action", dest="actions", metavar="ACTION", action="append", default=None,
        help="Declare an intended action (commit, push, secrets, live_trade, destructive). Repeatable; gated ones require --approve.",
    )
    p_run.add_argument("--json", "-j", action="store_true", help="Output the run record as JSON.")
    p_run.set_defaults(func=_cmd_agent_run)

    # review
    p_review = agent_sub.add_parser("review", help="Approve or reject a run.")
    p_review.add_argument("run_id", metavar="RUN-ID", help="Run to review (e.g. run-abc123).")
    g = p_review.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", dest="approve", action="store_true", help="Approve the run.")
    g.add_argument("--reject", dest="approve", action="store_false", help="Reject the run.")
    p_review.add_argument("--by", metavar="WHO", default="human:cli", help="Reviewer (default: human:cli).")
    p_review.add_argument("--note", metavar="TEXT", default="", help="Optional review note.")
    p_review.set_defaults(func=_cmd_agent_review)

    # history
    p_hist = agent_sub.add_parser("history", help="List past agent runs.")
    p_hist.add_argument("--role", metavar="ROLE", help="Filter by role.")
    p_hist.add_argument("--task", dest="task_id", metavar="TASK-ID", help="Filter by task.")
    p_hist.add_argument("--limit", type=int, default=20, metavar="N", help="Max runs to show (default: 20).")
    p_hist.set_defaults(func=_cmd_agent_history)


def _cmd_agent_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.agent("list", role=getattr(args, "role", None))
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    agents = r.data.get("agents", [])
    if not agents:
        print("No agents registered.")
        return 0
    print(f"Agents ({r.data.get('count', len(agents))})")
    _hr()
    for a in agents:
        mark = "★" if a.get("is_default") else " "
        if "available" in a:
            status = "✓ ready" if a.get("available") else f"needs {a.get('requires') or 'setup'}"
        else:
            status = ""
        print(f"  {mark} {a['id']}  {a['role']:<14} {a['provider']:<10} {status:<18} {a['name']}")
    return 0


def _cmd_agent_register(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.agent(
        "register", name=args.name, role=args.role, provider=args.provider,
        is_default=args.is_default, description=args.description,
    )
    if r.success:
        a = r.data.get("agent", {})
        print(f"Registered {a.get('id', '')}")
        print(f"  Name     : {a.get('name', '')}")
        print(f"  Role     : {a.get('role', '')}")
        print(f"  Provider : {a.get('provider', '')}")
        print(f"  Default  : {a.get('is_default', False)}")
        return 0
    print(f"Error: {r.message}", file=sys.stderr)
    return 1


def _cmd_agent_assign(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.agent("assign", task_id=args.task_id, role=args.role, assigned_by=args.assigned_by)
    if r.success:
        print(f"{args.task_id} assigned to role:{args.role}")
        return 0
    print(f"Error: {r.message}", file=sys.stderr)
    return 1


def _cmd_agent_run(args: argparse.Namespace) -> int:
    import json as _json

    mode = "dry-run" if args.dry_run else ("autonomous" if args.autonomous else args.mode)
    monday = _monday(args)
    r = monday.agent(
        "run",
        task_id=args.task_id,
        role=args.role,
        provider=args.provider,
        policy=args.policy,
        mode=mode,
        autonomous_enabled=args.enable_autonomous,
        approved=args.approve,
        requested_actions=args.actions,
    )

    if args.json:
        print(_json.dumps(r.data, indent=2, sort_keys=True))
        return 0 if r.success else 1

    print()
    print("═" * 64)
    print(f"  AGENT RUN — {r.run_id}")
    print("═" * 64)
    print(f"  Task      : {r.task_id}")
    print(f"  Role      : {r.role}")
    print(f"  Agent     : {r.data.get('agent_name', '') or '(none)'} ({r.agent_id or '—'})")
    model = r.data.get("provider_model", "")
    provider_line = r.provider_used or r.data.get("provider_requested", "") or "(none)"
    if model:
        provider_line = f"{provider_line} ({model})"
    print(f"  Provider  : {provider_line}")
    print(f"  Mode      : {r.data.get('mode', mode)}")
    print(f"  Status    : {r.status}")
    verdict = r.data.get("verdict", {})
    if verdict.get("verdict"):
        conf = verdict.get("confidence", "")
        print(f"  Verdict   : {verdict['verdict']}" + (f" ({conf})" if conf else ""))
    gate = r.data.get("gate", {})
    if gate and not gate.get("allowed", True):
        print(f"  Gate      : BLOCKED — {gate.get('reason', '')}")
    approval = r.data.get("approval", {})
    if approval.get("decision"):
        print(f"  Approval  : {approval.get('decision')}")
    _hr()
    if r.message:
        print(f"  {r.message}")
        _hr()
    return 0 if r.success else 1


def _cmd_agent_review(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.agent("review", run_id=args.run_id, approve=args.approve, by=args.by, note=args.note)
    if r.success:
        print(f"{r.run_id}: {r.data.get('approval', {}).get('decision', '')}")
        if r.message:
            print(f"  {r.message}")
        return 0
    print(f"Error: {r.message}", file=sys.stderr)
    return 1


def _cmd_agent_history(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.agent("history", role=getattr(args, "role", None), task_id=getattr(args, "task_id", None), limit=args.limit)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    runs = r.data.get("runs", [])
    if not runs:
        print("No agent runs yet.")
        return 0
    print(f"Agent runs ({r.data.get('count', len(runs))})")
    _hr()
    for run in runs:
        decision = run.get("approval", {}).get("decision", "")
        print(f"  {run['run_id']}  {run.get('role', ''):<14} {run.get('status', ''):<10} {decision:<10} {run.get('task_id', '')}")
    return 0


# ---------------------------------------------------------------------------
# team — the Agent Team Workflow
# ---------------------------------------------------------------------------

def _register_team(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "team",
        help="Run the agent team end-to-end on a task.",
        description=(
            "Run the registered agents as a collaborating team:\n"
            "  CPO → Lead Engineer → QA → Security → Reviewer → human approval\n\n"
            "Each stage receives the prior stages' summaries; QA / Security /\n"
            "Reviewer can stop the pipeline early. Review-required by default —\n"
            "nothing is committed, pushed, or executed live.\n\n"
            "examples:\n"
            "  monday team run TASK-0001\n"
            "  monday team run TASK-0001 --provider fake\n"
            "  monday team run TASK-0001 --mode dry-run\n"
            "  monday team history --task TASK-0001\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    team_sub = p.add_subparsers(title="team actions", metavar="<action>")
    team_sub.required = True

    p_run = team_sub.add_parser("run", help="Run the full team pipeline on a task.")
    p_run.add_argument("task_id", metavar="TASK-ID", help="Task to run the team on.")
    p_run.add_argument("--provider", metavar="NAME", default="", help="Provider override for every stage (e.g. fake).")
    p_run.add_argument("--mode", metavar="MODE", default="review", help="review (default) | dry-run.")
    p_run.add_argument("--json", "-j", action="store_true", help="Output the team run record as JSON.")
    p_run.set_defaults(func=_cmd_team_run)

    p_hist = team_sub.add_parser("history", help="List past team runs.")
    p_hist.add_argument("--task", dest="task_id", metavar="TASK-ID", help="Filter by task.")
    p_hist.add_argument("--limit", type=int, default=20, metavar="N", help="Max runs to show (default: 20).")
    p_hist.set_defaults(func=_cmd_team_history)


def _cmd_team_run(args: argparse.Namespace) -> int:
    import json as _json

    monday = _monday(args)
    r = monday.team("run", task_id=args.task_id, provider=args.provider, mode=args.mode)

    if args.json:
        print(_json.dumps(r.data, indent=2, sort_keys=True))
        return 0 if r.success else 1

    print()
    print("═" * 64)
    print(f"  TEAM RUN — {r.team_run_id}")
    print("═" * 64)
    print(f"  Task    : {r.task_id}")
    print(f"  Mode    : {r.data.get('mode', args.mode)}")
    print(f"  Status  : {r.status}")
    _hr()
    _VERDICT_MARK = {"pass": "✓", "needs_changes": "⚠", "block": "✗"}
    for st in r.stages:
        verdict = st.get("verdict", "")
        mark = _VERDICT_MARK.get(verdict, "✗")
        role = st.get("role", "")
        prov = st.get("provider_used", "") or "—"
        model = st.get("provider_model", "")
        prov_label = f"{prov} ({model})" if model else prov
        conf = (st.get("confidence") or "").strip()
        verdict_label = f"{verdict} ({conf})" if conf else verdict
        print(f"  {mark} {role:<14} [{st.get('status', ''):<9}] {verdict_label:<20} {prov_label}")
        summary = (st.get("summary") or "").strip()
        if summary:
            print(f"      {summary[:100]}")
    if r.stopped_at:
        print("─" * 64)
        print(f"  Stopped at: {r.stopped_at}")
    _hr()
    if r.message:
        print(f"  {r.message}")
        _hr()
    return 0 if r.success else 1


def _cmd_team_history(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.team("history", task_id=getattr(args, "task_id", None), limit=args.limit)
    runs = r.data.get("runs", [])
    if not runs:
        print("No team runs yet.")
        return 0
    print(f"Team runs ({r.data.get('count', len(runs))})")
    _hr()
    for run in runs:
        print(f"  {run['team_run_id']}  {run.get('status', ''):<17} {run.get('task_id', '')}")
    return 0


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

def _register_publish(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "publish",
        help="Publish MondayOS documents to Confluence.",
        description=(
            "Publish a MondayOS document (roadmap, sprint summary, architecture\n"
            "doc, release notes, agent/research report) to Confluence.\n\n"
            "MondayOS stays the system of record — content is read from a local\n"
            "file or knowledge entry and pushed to a Confluence page. The\n"
            "doc-ID → page-ID mapping and history are stored locally.\n\n"
            "Credentials come from the environment only (never source):\n"
            "  CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL,\n"
            "  CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY\n\n"
            "examples:\n"
            "  monday publish confluence RES-0007\n"
            "  monday publish confluence --file docs/ROADMAP.md\n"
            "  monday publish confluence --file docs/ROADMAP.md --space ENG\n"
            "  monday publish confluence --file docs/ROADMAP.md --parent 123456\n"
            "  monday publish confluence --file docs/ROADMAP.md --dry-run\n"
            "  monday publish history\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pub_sub = p.add_subparsers(title="publish actions", metavar="<action>")
    pub_sub.required = True

    c = pub_sub.add_parser("confluence", help="Publish a document to Confluence.")
    c.add_argument("doc_id", nargs="?", default="", metavar="DOC_ID",
                   help="MondayOS document to publish (knowledge entry ID or docs/ name).")
    c.add_argument("--file", metavar="PATH", default="",
                   help="Publish content from an explicit file path.")
    c.add_argument("--title", metavar="TEXT", default="",
                   help="Page title (defaults to the document's first heading).")
    c.add_argument("--space", dest="space_key", metavar="KEY", default="",
                   help="Confluence space key (overrides CONFLUENCE_SPACE_KEY).")
    c.add_argument("--parent", dest="parent_id", metavar="PAGE_ID", default="",
                   help="Parent page ID to nest the new page under.")
    c.add_argument("--update-page", dest="update_page", metavar="PAGE_ID", default="",
                   help="Explicitly update this existing page (overwrite).")
    c.add_argument("--dry-run", action="store_true",
                   help="Preview what would be published without calling Confluence.")
    c.add_argument("--force", action="store_true",
                   help="Publish even if the content is unchanged since last time.")
    c.set_defaults(func=_cmd_publish_confluence)

    h = pub_sub.add_parser("history", help="Show local publish history.")
    h.add_argument("--limit", type=int, default=20, metavar="N",
                   help="Max events to show (default: 20).")
    h.set_defaults(func=_cmd_publish_history)


def _cmd_publish_confluence(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.publish(
        "confluence",
        doc_id=args.doc_id,
        file=args.file,
        title=args.title,
        space_key=args.space_key,
        parent_id=args.parent_id,
        update_page=args.update_page,
        dry_run=args.dry_run,
        force=args.force,
    )

    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1

    if r.dry_run:
        summary = r.data.get("summary", {})
        print("Dry run — no changes made.")
        _hr()
        print(f"  Document : {r.doc_id}")
        print(f"  Would    : {summary.get('would', '')}")
        print(f"  Page     : {summary.get('page_id', '(new)')}")
        print(f"  Space    : {summary.get('space_key', '(default)')}")
        print(f"  Changed  : {summary.get('content_changed', True)}")
        print(f"  Size     : {summary.get('storage_bytes', 0)} bytes (storage format)")
        print(f"  Checksum : {summary.get('new_checksum', '')[:16]}")
        _hr()
        return 0

    print(f"{r.status.title()}: {r.doc_id}")
    _hr()
    print(f"  Page ID  : {r.page_id}")
    if r.url:
        print(f"  URL      : {r.url}")
    print(f"  Status   : {r.status}")
    if r.message:
        print(f"  {r.message}")
    _hr()
    return 0


def _cmd_publish_history(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.publish("history", limit=args.limit)
    events = r.data.get("history", [])
    if not events:
        print("No publish history yet.")
        return 0
    print(f"Publish history ({r.data.get('count', len(events))})")
    _hr()
    for e in events:
        at = (e.get("at", "") or "")[:19]
        page = e.get("page_id", "") or "—"
        print(f"  {at}  {e.get('action', ''):<10} {e.get('doc_id', ''):<20} {page}")
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


# ---------------------------------------------------------------------------
# growth
# ---------------------------------------------------------------------------

def _register_growth(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "growth",
        help="Manage project-isolated marketing workspaces and content approvals.",
        description=(
            "Growth Bot — plan, draft, review, and approve marketing content for a\n"
            "MondayOS project.\n\n"
            "Every workspace is isolated: no asset, credential, draft, audience, or\n"
            "metric crosses a project boundary. Platform credentials are referenced\n"
            "by environment-variable NAME and never stored.\n\n"
            "Nothing here publishes. There is no connector and no lifecycle state\n"
            "beyond 'approved' — see docs/GROWTH_BOT.md for the delivery increments.\n\n"
            "examples:\n"
            "  monday growth workspace-init --project sourcingbot\n"
            "  monday growth bind --project sourcingbot --platform linkedin \\\n"
            "      --account-id 12345 --secret-name LINKEDIN_TOKEN\n"
            "  monday growth content-create --project sourcingbot --platform linkedin \\\n"
            "      --account 12345 --copy \"...\" --cta \"Book a demo\"\n"
            "  monday growth review --project sourcingbot --content CONTENT-0001\n"
            "  monday growth approve --project sourcingbot --content CONTENT-0001 --by jrich\n"
            "  monday growth content-list --project sourcingbot\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(title="growth actions", metavar="<action>")
    sub.required = True

    def _project_arg(parser: Any) -> None:
        parser.add_argument("--project", required=True, help="Registered project name.")

    def _content_arg(parser: Any) -> None:
        parser.add_argument("--content", required=True, help="Content item id (CONTENT-XXXX).")

    init = sub.add_parser("workspace-init", help="Create a growth workspace for a project.")
    _project_arg(init)
    init.set_defaults(func=_cmd_growth_workspace_init)

    get = sub.add_parser("workspace-get", help="Show a project's growth workspace.")
    _project_arg(get)
    get.set_defaults(func=_cmd_growth_workspace_get)

    ls = sub.add_parser("workspace-list", help="List initialized growth workspaces.")
    ls.set_defaults(func=_cmd_growth_workspace_list)

    bind = sub.add_parser("bind", help="Bind a publishing account (by secret name).")
    _project_arg(bind)
    bind.add_argument("--platform", required=True, help="linkedin, x, instagram, ...")
    bind.add_argument("--account-id", required=True, help="Stable platform account id.")
    bind.add_argument("--account-handle", default="", help="Display handle (not approved-on).")
    bind.add_argument(
        "--secret-name",
        default="",
        help="Environment variable holding the credential. Never the credential itself.",
    )
    bind.set_defaults(func=_cmd_growth_bind)

    binds = sub.add_parser("bindings", help="List a workspace's platform bindings.")
    _project_arg(binds)
    binds.set_defaults(func=_cmd_growth_bindings)

    creds = sub.add_parser("credentials", help="Check whether bound credentials are present.")
    _project_arg(creds)
    creds.set_defaults(func=_cmd_growth_credentials)

    create = sub.add_parser("content-create", help="Create a Draft content item.")
    _project_arg(create)
    for flag, helptext in (
        ("--platform", "Target platform."),
        ("--account", "Platform account id this publishes as."),
        ("--copy", "Post copy."),
        ("--cta", "Call to action."),
        ("--destination-url", "Destination URL."),
        ("--campaign", "Campaign name."),
        ("--expected-goal", "What this item is meant to achieve."),
        ("--expected-audience", "Who this item targets."),
        ("--scheduled-at", "Publish time, ISO 8601 UTC."),
    ):
        create.add_argument(flag, default="", help=helptext)
    create.add_argument("--media", nargs="*", default=None, help="Media references, in order.")
    create.set_defaults(func=_cmd_growth_content_create)

    cget = sub.add_parser("content-get", help="Show one content item.")
    _project_arg(cget)
    _content_arg(cget)
    cget.set_defaults(func=_cmd_growth_content_get)

    clist = sub.add_parser("content-list", help="List content items.")
    _project_arg(clist)
    clist.add_argument("--status", default="", help="Filter by lifecycle status.")
    clist.set_defaults(func=_cmd_growth_content_list)

    upd = sub.add_parser("content-update", help="Edit an item (resets a stale approval).")
    _project_arg(upd)
    _content_arg(upd)
    for flag in (
        "--platform", "--account", "--copy", "--cta", "--destination-url",
        "--campaign", "--expected-goal", "--expected-audience", "--scheduled-at", "--notes",
    ):
        upd.add_argument(flag, default=None)
    upd.add_argument("--media", nargs="*", default=None)
    upd.set_defaults(func=_cmd_growth_content_update)

    rev = sub.add_parser("review", help="Submit an item for human review.")
    _project_arg(rev)
    _content_arg(rev)
    rev.set_defaults(func=_cmd_growth_review)

    app = sub.add_parser("approve", help="Record a human approval of an item.")
    _project_arg(app)
    _content_arg(app)
    app.add_argument("--by", default="human:cli", help="Who approved.")
    app.add_argument("--reason", default="", help="Optional note.")
    app.set_defaults(func=_cmd_growth_approve)

    chg = sub.add_parser("request-changes", help="Return an item to the author.")
    _project_arg(chg)
    _content_arg(chg)
    chg.add_argument("--by", default="human:cli")
    chg.add_argument("--reason", default="", help="What needs to change.")
    chg.set_defaults(func=_cmd_growth_request_changes)

    can = sub.add_parser("cancel", help="Terminally cancel an item.")
    _project_arg(can)
    _content_arg(can)
    can.add_argument("--by", default="human:cli")
    can.add_argument("--reason", default="")
    can.set_defaults(func=_cmd_growth_cancel)

    sch = sub.add_parser("schedule", help="Schedule an approved future-dated item.")
    _project_arg(sch)
    _content_arg(sch)
    sch.add_argument("--by", default="human:cli")
    sch.set_defaults(func=_cmd_growth_schedule)

    pub = sub.add_parser("publish", help="Publish an approved item now.")
    _project_arg(pub)
    _content_arg(pub)
    pub.add_argument("--by", default="human:cli")
    pub.add_argument(
        "--force",
        action="store_true",
        help="Publish a scheduled item before its due time. Skips ONLY the due-time "
             "check; approval, isolation, and every pause still apply.",
    )
    pub.set_defaults(func=_cmd_growth_publish)

    rty = sub.add_parser("retry", help="Re-attempt a failed publication.")
    _project_arg(rty)
    _content_arg(rty)
    rty.add_argument("--by", default="human:cli")
    rty.set_defaults(func=_cmd_growth_retry)

    pst = sub.add_parser("publication-status", help="Show publication state and history.")
    _project_arg(pst)
    _content_arg(pst)
    pst.set_defaults(func=_cmd_growth_publication_status)

    pau = sub.add_parser("pause", help="Pause publishing at a scope.")
    _project_arg(pau)
    pau.add_argument(
        "--scope", required=True, choices=["global", "project", "platform", "post"],
        help="global is the portfolio-wide emergency stop and overrides everything.",
    )
    pau.add_argument("--target", default="", help="Platform slug or content id.")
    pau.add_argument("--reason", default="", help="Why publishing is held.")
    pau.set_defaults(func=_cmd_growth_pause)

    res = sub.add_parser("resume", help="Clear a pause at a scope.")
    _project_arg(res)
    res.add_argument(
        "--scope", required=True, choices=["global", "project", "platform", "post"]
    )
    res.add_argument("--target", default="")
    res.set_defaults(func=_cmd_growth_resume)

    pls = sub.add_parser("pauses", help="List active pauses.")
    _project_arg(pls)
    pls.set_defaults(func=_cmd_growth_pauses)

    # ---- campaigns ----
    cc = sub.add_parser("campaign-create", help="Create a Draft campaign.")
    _project_arg(cc)
    cc.add_argument("--name", required=True)
    for flag in ("--description", "--objective", "--target-audience",
                 "--primary-conversion-goal", "--theme", "--cta", "--destination"):
        cc.add_argument(flag, default="")
    cc.add_argument("--channels", nargs="*", default=None)
    cc.add_argument("--kpis", nargs="*", default=None)
    cc.set_defaults(func=_cmd_growth_campaign_create)

    cg = sub.add_parser("campaign-get", help="Show one campaign.")
    _project_arg(cg)
    cg.add_argument("--campaign", required=True)
    cg.set_defaults(func=_cmd_growth_campaign_get)

    cls_ = sub.add_parser("campaign-list", help="List campaigns.")
    _project_arg(cls_)
    cls_.add_argument("--status", default="")
    cls_.set_defaults(func=_cmd_growth_campaign_list)

    cst = sub.add_parser("campaign-status", help="Move a campaign along its lifecycle.")
    _project_arg(cst)
    cst.add_argument("--campaign", required=True)
    cst.add_argument(
        "--status", required=True,
        choices=["draft", "active", "paused", "completed", "cancelled"],
    )
    cst.add_argument("--reason", default="")
    cst.set_defaults(func=_cmd_growth_campaign_status)

    cas = sub.add_parser("campaign-assign", help="Attach content to a campaign.")
    _project_arg(cas)
    _content_arg(cas)
    cas.add_argument("--campaign", default="", help="Empty detaches the item.")
    cas.set_defaults(func=_cmd_growth_campaign_assign)

    # ---- library ----
    lse = sub.add_parser("library-search", help="Search the content library.")
    _project_arg(lse)
    for flag in ("--text", "--theme", "--campaign", "--platform", "--tag",
                 "--content-type", "--status"):
        lse.add_argument(flag, default="")
    lse.add_argument("--reusable-only", action="store_true")
    lse.set_defaults(func=_cmd_growth_library_search)

    lsu = sub.add_parser("library-summary", help="Library counts by type/platform/status.")
    _project_arg(lsu)
    lsu.set_defaults(func=_cmd_growth_library_summary)

    lto = sub.add_parser("library-top", help="Highest-performing content.")
    _project_arg(lto)
    lto.add_argument("--limit", type=int, default=10)
    lto.set_defaults(func=_cmd_growth_library_top)

    lre = sub.add_parser("library-reusable", help="Reusable / not-recently-reused content.")
    _project_arg(lre)
    lre.add_argument("--days", type=int, default=0, help="Only items unused for N days.")
    lre.set_defaults(func=_cmd_growth_library_reusable)

    lva = sub.add_parser("library-variants", help="Per-platform variants of one idea.")
    _project_arg(lva)
    lva.add_argument("--group", required=True)
    lva.set_defaults(func=_cmd_growth_library_variants)

    # ---- onboarding ----
    onb = sub.add_parser("onboard", help="Record growth onboarding (no credentials).")
    _project_arg(onb)
    onb.add_argument("--brand-voice", default=None)
    onb.add_argument("--objectives", nargs="*", default=None)
    onb.add_argument("--audience-personas", nargs="*", default=None)
    onb.add_argument("--audience-icps", nargs="*", default=None)
    onb.add_argument("--brand-assets", nargs="*", default=None)
    onb.add_argument("--prohibited", nargs="*", default=None)
    onb.add_argument("--cadence", type=int, default=None, help="Posts per week.")
    onb.add_argument(
        "--platform", action="append", default=None, metavar="PLATFORM=LABEL",
        help="Desired platform and account LABEL. Never a credential. Repeatable.",
    )
    onb.add_argument("--review-day", default=None)
    onb.add_argument("--review-hour", type=int, default=None)
    onb.set_defaults(func=_cmd_growth_onboard)

    ost = sub.add_parser("onboarding-status", help="Show growth readiness.")
    _project_arg(ost)
    ost.set_defaults(func=_cmd_growth_onboarding_status)

    dem = sub.add_parser("seed-demo", help="Seed clearly-marked synthetic demo data.")
    _project_arg(dem)
    dem.set_defaults(func=_cmd_growth_seed_demo)

    # ---- performance events ----
    ev = sub.add_parser("event-record", help="Record one performance observation.")
    _project_arg(ev)
    ev.add_argument(
        "--type", required=True, dest="event_type",
        choices=["impression", "reach", "engagement", "reaction", "comment", "share",
                 "click", "website_visit", "signup", "purchase", "custom_conversion"],
    )
    ev.add_argument(
        "--source", default="imported", choices=["imported", "synthetic"],
        help="platform is unavailable: no adapter has reported anything.",
    )
    ev.add_argument("--content", default="", dest="content_id")
    ev.add_argument("--campaign", default="")
    ev.add_argument("--platform", default="")
    ev.add_argument("--value", type=float, default=1.0)
    ev.add_argument("--name", default="", help="Qualifier for custom_conversion.")
    ev.add_argument("--at", default="", dest="occurred_at", help="ISO 8601 UTC.")
    ev.set_defaults(func=_cmd_growth_event_record)

    evl = sub.add_parser("event-list", help="List recorded performance events.")
    _project_arg(evl)
    evl.add_argument("--content", default="", dest="content_id")
    evl.add_argument("--campaign", default="")
    evl.add_argument("--platform", default="")
    evl.set_defaults(func=_cmd_growth_event_list)

    # ---- analytics ----
    an = sub.add_parser("analytics", help="Whole-project analytics.")
    _project_arg(an)
    an.set_defaults(func=_cmd_growth_analytics)

    anc = sub.add_parser("analytics-campaign", help="Analytics for one campaign.")
    _project_arg(anc)
    anc.add_argument("--campaign", required=True)
    anc.set_defaults(func=_cmd_growth_analytics_campaign)

    anp = sub.add_parser("analytics-platform", help="Analytics grouped by platform.")
    _project_arg(anp)
    anp.set_defaults(func=_cmd_growth_analytics_platform)

    ans = sub.add_parser("analytics-series", help="One metric bucketed over time.")
    _project_arg(ans)
    ans.add_argument("--metric", default="impressions")
    ans.add_argument("--granularity", default="day", choices=["day", "week"])
    ans.add_argument("--campaign", default="")
    ans.set_defaults(func=_cmd_growth_analytics_series)

    ant = sub.add_parser("analytics-trend", help="Compare this period to the last.")
    _project_arg(ant)
    ant.add_argument("--metric", default="impressions")
    ant.add_argument("--days", type=int, default=7, dest="period_days")
    ant.set_defaults(func=_cmd_growth_analytics_trend)

    anf = sub.add_parser("analytics-funnel", help="The conversion funnel.")
    _project_arg(anf)
    anf.add_argument("--campaign", default="")
    anf.add_argument("--platform", default="")
    anf.set_defaults(func=_cmd_growth_analytics_funnel)

    snt = sub.add_parser("snapshot-take", help="Capture current metrics as a baseline.")
    _project_arg(snt)
    snt.add_argument("--note", default="")
    snt.set_defaults(func=_cmd_growth_snapshot_take)

    snl = sub.add_parser("snapshot-list", help="List metric snapshots.")
    _project_arg(snl)
    snl.set_defaults(func=_cmd_growth_snapshot_list)

    agg = sub.add_parser("aggregate-write", help="Write the portfolio aggregate.")
    _project_arg(agg)
    agg.set_defaults(func=_cmd_growth_aggregate_write)

    # ---- growth brain ----
    ba = sub.add_parser("brain", help="Deterministic analysis over measured outcomes.")
    _project_arg(ba)
    ba.set_defaults(func=_cmd_growth_brain)

    br = sub.add_parser("brain-recommendations", help="Evidence-backed recommendations.")
    _project_arg(br)
    br.set_defaults(func=_cmd_growth_brain_recommendations)

    bh = sub.add_parser("brain-hypotheses", help="Unconfirmed candidate explanations.")
    _project_arg(bh)
    bh.set_defaults(func=_cmd_growth_brain_hypotheses)

    bo = sub.add_parser("brain-opportunities", help="Detected findings.")
    _project_arg(bo)
    bo.set_defaults(func=_cmd_growth_brain_opportunities)

    bs = sub.add_parser("brain-scores", help="Workspace, campaign and channel health.")
    _project_arg(bs)
    bs.set_defaults(func=_cmd_growth_brain_scores)

    bf = sub.add_parser("brain-forecasts", help="Rule-based projections.")
    _project_arg(bf)
    bf.set_defaults(func=_cmd_growth_brain_forecasts)

    be = sub.add_parser("brain-experiments", help="Proposed experiments (approval required).")
    _project_arg(be)
    be.set_defaults(func=_cmd_growth_brain_experiments)

    mr = sub.add_parser("memory-record", help="Remember a tentative claim.")
    _project_arg(mr)
    mr.add_argument("--statement", required=True)
    mr.add_argument("--sample", type=int, required=True, dest="sample_size")
    mr.add_argument(
        "--category", default="recurring-pattern",
        choices=["campaign-outcome", "content-outcome", "platform-outcome",
                 "audience-outcome", "experiment-outcome", "seasonal-observation",
                 "recurring-pattern", "historical-recommendation"],
    )
    mr.add_argument("--metric", default="", dest="metric_affected")
    mr.set_defaults(func=_cmd_growth_memory_record)

    ml = sub.add_parser("memory-list", help="List marketing memory.")
    _project_arg(ml)
    ml.add_argument("--status", default="",
                    choices=["", "tentative", "validated", "invalidated"])
    ml.set_defaults(func=_cmd_growth_memory_list)

    mv = sub.add_parser("memory-validate", help="Promote a claim to validated.")
    _project_arg(mv)
    mv.add_argument("--entry", required=True, dest="entry_id")
    mv.add_argument("--by", default="human:cli")
    mv.add_argument("--reason", default="")
    mv.set_defaults(func=_cmd_growth_memory_validate)

    mi = sub.add_parser("memory-invalidate", help="Mark a claim contradicted.")
    _project_arg(mi)
    mi.add_argument("--entry", required=True, dest="entry_id")
    mi.add_argument("--by", default="human:cli")
    mi.add_argument("--reason", default="")
    mi.set_defaults(func=_cmd_growth_memory_invalidate)


def _growth_call(args: argparse.Namespace, action: str, **kwargs: Any) -> int:
    """Invoke Monday.growth and render the result. All growth commands go through this."""
    monday = _monday(args)
    response = monday.growth(action, **kwargs)
    if not response.success:
        print(f"Error: {response.message}", file=sys.stderr)
        return 1
    print(response.message)
    return 0


def _cmd_growth_workspace_init(args: argparse.Namespace) -> int:
    return _growth_call(args, "workspace-init", project=args.project)


def _cmd_growth_workspace_get(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("workspace-get", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    ws = r.data.get("workspace", {})
    print(f"Growth workspace: {ws.get('slug', '')}")
    _hr()
    print(f"  Project    : {ws.get('registered_name', '')}")
    print(f"  Business   : {ws.get('business', {}).get('name', '') or '—'}")
    print(f"  Voice      : {ws.get('brand', {}).get('voice', '') or '—'}")
    print(f"  Bindings   : {len(ws.get('bindings', []))}")
    print(f"  Updated    : {ws.get('updated', '')}")
    return 0


def _cmd_growth_workspace_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("workspace-list")
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    slugs = r.data.get("workspaces", [])
    if not slugs:
        print("No growth workspaces.")
        return 0
    print(f"Growth workspaces ({len(slugs)})")
    _hr()
    for slug in slugs:
        print(f"  {slug}")
    return 0


def _cmd_growth_bind(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "bind",
        project=args.project, platform=args.platform, account_id=args.account_id,
        account_handle=args.account_handle, secret_name=args.secret_name,
    )


def _cmd_growth_bindings(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("bindings", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("bindings", [])
    if not rows:
        print("No bindings.")
        return 0
    print(f"Bindings ({len(rows)})")
    _hr()
    for b in rows:
        secret = b.get("secret_name") or "—"
        print(f"  {b['platform']:<12} {b['account_id']:<20} secret:{secret}")
    return 0


def _cmd_growth_credentials(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("credentials", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("credentials", [])
    if not rows:
        print("No bindings to check.")
        return 0
    print(f"Credentials ({r.data.get('ready', 0)}/{len(rows)} present)")
    _hr()
    for row in rows:
        mark = "✓" if row["ready"] else "✗"
        print(f"  {mark} {row['platform']:<12} {row['secret_name'] or '—'}")
    return 0


def _cmd_growth_content_create(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "content-create",
        project=args.project, platform=args.platform, account=args.account,
        media=args.media, copy=args.copy, cta=args.cta,
        destination_url=args.destination_url, campaign=args.campaign,
        expected_goal=args.expected_goal, expected_audience=args.expected_audience,
        scheduled_at=args.scheduled_at,
    )


def _cmd_growth_content_get(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("content-get", project=args.project, content_id=args.content)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    item = r.data.get("content", {})
    print(f"{item.get('id', '')}  [{item.get('status', '')}]")
    _hr()
    print(f"  Platform   : {item.get('platform', '') or '—'}")
    print(f"  Account    : {item.get('account', '') or '—'}")
    print(f"  CTA        : {item.get('cta', '') or '—'}")
    print(f"  Scheduled  : {item.get('scheduled_at', '') or '—'}")
    print(f"  Approved   : {item.get('is_approved', False)}")
    if item.get("approval_is_stale"):
        print("  ⚠ Approval is stale — approved fields changed since sign-off.")
    missing = item.get("missing_required_fields", [])
    if missing:
        print(f"  Missing    : {', '.join(missing)}")
    print(f"  Publishable: {item.get('publishable', False)} — {item.get('publishable_reason', '')}")
    return 0


def _cmd_growth_content_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("content-list", project=args.project, status=args.status)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    items = r.data.get("content", [])
    if not items:
        print("No content items.")
        return 0
    print(f"Content ({len(items)})")
    _hr()
    for i in items:
        mark = "✓" if i.get("is_approved") else " "
        print(f"  {mark} {i['id']}  {i.get('status', ''):<18} {i.get('platform', '') or '—'}")
    return 0


def _cmd_growth_content_update(args: argparse.Namespace) -> int:
    supplied = {
        key: value
        for key, value in (
            ("platform", args.platform), ("account", args.account), ("copy", args.copy),
            ("cta", args.cta), ("destination_url", args.destination_url),
            ("campaign", args.campaign), ("expected_goal", args.expected_goal),
            ("expected_audience", args.expected_audience),
            ("scheduled_at", args.scheduled_at), ("notes", args.notes),
            ("media", args.media),
        )
        if value is not None
    }
    return _growth_call(
        args, "content-update", project=args.project, content_id=args.content, **supplied
    )


def _cmd_growth_review(args: argparse.Namespace) -> int:
    return _growth_call(args, "review", project=args.project, content_id=args.content)


def _cmd_growth_approve(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "approve", project=args.project, content_id=args.content,
        approved_by=args.by, reason=args.reason,
    )


def _cmd_growth_request_changes(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "request-changes", project=args.project, content_id=args.content,
        changed_by=args.by, reason=args.reason,
    )


def _cmd_growth_cancel(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "cancel", project=args.project, content_id=args.content,
        changed_by=args.by, reason=args.reason,
    )


def _cmd_growth_schedule(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "schedule", project=args.project, content_id=args.content, actor=args.by
    )


def _cmd_growth_publish(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "publish", project=args.project, content_id=args.content,
        actor=args.by, force=args.force,
    )


def _cmd_growth_retry(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "retry", project=args.project, content_id=args.content, actor=args.by
    )


def _cmd_growth_publication_status(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("publication-status", project=args.project, content_id=args.content)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    st = r.data.get("publication_status", {})
    print(f"{st.get('content_id', '')}  [{st.get('status', '')}]")
    _hr()
    covers = st.get("approval_covers_content", False)
    print(f"  Approval   : {'covers this exact content' if covers else 'DOES NOT COVER — stale'}")
    print(f"  Scheduled  : {st.get('scheduled_at', '') or '—'}"
          + (f"  ({st['scheduled_timezone']})" if st.get("scheduled_timezone") else ""))
    pause = st.get("pause", {})
    print(f"  Pause      : {pause.get('scope') or 'none'}")
    pub = st.get("publication")
    if not pub:
        print("  Publication: never attempted")
    else:
        print(f"  Platform   : {pub.get('platform', '') or '—'}")
        print(f"  Account    : {pub.get('account_ref', '') or '—'}")
        print(f"  External   : {pub.get('external_id', '') or '—'}")
        print(f"  URL        : {pub.get('external_url', '') or '—'}")
        print(f"  Published  : {pub.get('published_at', '') or '—'}")
        print(f"  Attempts   : {pub.get('retry_count', 0)}")
        if pub.get("failure_code"):
            print(f"  Failure    : {pub['failure_code']} — {pub.get('failure_detail', '')}")
        if pub.get("next_attempt_at"):
            print(f"  Retry after: {pub['next_attempt_at']}")
        if pub.get("reconciled"):
            print("  Reconciled : yes (adopted an existing platform post)")
    print(f"  Audit      : {len(st.get('audit', []))} record(s)")
    return 0


def _cmd_growth_pause(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "pause", project=args.project, scope=args.scope,
        target=args.target, reason=args.reason,
    )


def _cmd_growth_resume(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "resume", project=args.project, scope=args.scope, target=args.target
    )


def _cmd_growth_pauses(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("pauses", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("pauses", {})
    if not rows:
        print("No active pauses.")
        return 0
    print(f"Active pauses ({len(rows)})")
    _hr()
    for key, meta in sorted(rows.items()):
        reason = (meta or {}).get("reason") or "—"
        mark = "!!" if key == "global" else "  "
        print(f"  {mark} {key:<28} {reason}")
    return 0


def _cmd_growth_campaign_create(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "campaign-create", project=args.project, name=args.name,
        description=args.description, objective=args.objective,
        target_audience=args.target_audience,
        primary_conversion_goal=args.primary_conversion_goal, theme=args.theme,
        cta=args.cta, destination=args.destination,
        channels=args.channels, kpis=args.kpis,
    )


def _cmd_growth_campaign_get(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("campaign-get", project=args.project, campaign_id=args.campaign)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    c = r.data.get("campaign", {})
    print(f"{c.get('id', '')}  [{c.get('status', '')}]  {c.get('name', '')}")
    _hr()
    print(f"  Objective  : {c.get('objective', '') or '—'}")
    print(f"  Audience   : {c.get('target_audience', '') or '—'}")
    print(f"  Conversion : {c.get('primary_conversion_goal', '') or '—'}")
    print(f"  Theme      : {c.get('theme', '') or '—'}")
    print(f"  Channels   : {', '.join(c.get('channels', [])) or '—'}")
    print(f"  Window     : {c.get('start_date', '') or '—'} → {c.get('end_date', '') or '—'}")
    print(f"  Content    : {c.get('content_count', 0)} "
          f"({c.get('approved_count', 0)} approved, {c.get('published_count', 0)} published)")
    print(f"  Accepting  : {c.get('accepts_content', False)}")
    return 0


def _cmd_growth_campaign_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("campaign-list", project=args.project, status=args.status)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("campaigns", [])
    if not rows:
        print("No campaigns.")
        return 0
    print(f"Campaigns ({len(rows)})")
    _hr()
    for c in rows:
        print(f"  {c['id']}  {c['status']:<11} {c.get('content_count', 0):>3} items  {c['name']}")
    return 0


def _cmd_growth_campaign_status(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "campaign-status", project=args.project, campaign_id=args.campaign,
        status=args.status, reason=args.reason,
    )


def _cmd_growth_campaign_assign(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "campaign-assign", project=args.project, content_id=args.content,
        campaign_id=args.campaign,
    )


def _print_entries(rows: list[dict[str, Any]], header: str) -> int:
    if not rows:
        print("No matching content.")
        return 0
    print(f"{header} ({len(rows)})")
    _hr()
    for e in rows:
        mark = "✓" if e.get("approval_status") == "approved" else " "
        title = e.get("title") or (e.get("body", "")[:40] + "…")
        print(f"  {mark} {e['content_id']}  {e.get('content_type', ''):<20} "
              f"{e.get('platform', '') or '—':<10} {title}")
    return 0


def _cmd_growth_library_search(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth(
        "library-search", project=args.project, text=args.text, theme=args.theme,
        campaign=args.campaign, platform=args.platform, tag=args.tag,
        content_type=args.content_type, status=args.status,
        reusable_only=args.reusable_only,
    )
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return _print_entries(r.data.get("entries", []), "Library")


def _cmd_growth_library_summary(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("library-summary", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    s = r.data.get("summary", {})
    print(f"Content library: {s.get('project', '')}")
    _hr()
    print(f"  Total     : {s.get('total', 0)}")
    print(f"  Reusable  : {s.get('reusable', 0)}")
    for label, key in (("By type", "by_type"), ("By platform", "by_platform"),
                       ("By status", "by_status"), ("Themes", "themes")):
        rows = s.get(key, {})
        pretty = ", ".join(f"{k}={v}" for k, v in rows.items()) or "—"
        print(f"  {label:<10}: {pretty}")
    return 0


def _cmd_growth_library_top(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("library-top", project=args.project, limit=args.limit)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    basis = r.data.get("basis", "")
    if basis == "recency-fallback":
        print("NOTE: no performance data yet — ranked by recency, not by results.")
    return _print_entries(r.data.get("entries", []), f"Top content (by {basis})")


def _cmd_growth_library_reusable(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("library-reusable", project=args.project, days=args.days)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return _print_entries(r.data.get("entries", []), "Reusable content")


def _cmd_growth_library_variants(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("library-variants", project=args.project, variant_group_id=args.group)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return _print_entries(r.data.get("entries", []), f"Variants of {args.group}")


def _cmd_growth_onboard(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"project": args.project}
    if args.platform is not None:
        intents = []
        for raw in args.platform:
            platform, _, label = raw.partition("=")
            intents.append({"platform": platform.strip(), "account_label": label.strip()})
        payload["platforms"] = intents
    for key, value in (
        ("brand_voice", args.brand_voice), ("objectives", args.objectives),
        ("audience_personas", args.audience_personas), ("audience_icps", args.audience_icps),
        ("brand_assets", args.brand_assets), ("prohibited_content", args.prohibited),
        ("cadence_per_week", args.cadence),
        ("weekly_review_day", args.review_day), ("weekly_review_hour_utc", args.review_hour),
    ):
        if value is not None:
            payload[key] = value
    return _growth_call(args, "onboard", **payload)


def _cmd_growth_onboarding_status(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("onboarding-status", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    o = r.data.get("onboarding", {})
    print(f"Growth onboarding: {o.get('project', '')}")
    _hr()
    print(f"  Ready for planning        : {o.get('growth_ready_for_planning', False)}")
    print(f"  Ready for REAL publishing : {o.get('growth_ready_for_real_publishing', False)}")
    print(f"  Completed : {', '.join(o.get('completed_steps', [])) or '—'}")
    print(f"  Missing   : {', '.join(o.get('missing_steps', [])) or '—'}")
    intents = o.get("platform_intents", [])
    print(f"  Platforms : {', '.join(p['platform'] for p in intents) or '—'}  (labels only)")
    print(f"  {o.get('note', '')}")
    return 0


def _cmd_growth_seed_demo(args: argparse.Namespace) -> int:
    return _growth_call(args, "seed-demo", project=args.project)


def _synthetic_banner(synthetic: bool) -> None:
    """Make unverified data impossible to miss on a metrics screen."""
    if synthetic:
        print("  ⚠ SYNTHETIC / IMPORTED DATA — not measured by any platform.")


def _print_metrics(metrics: dict[str, Any]) -> None:
    for key in sorted(metrics):
        m = metrics[key]
        if m.get("value") is None:
            shown = f"—  ({m.get('reason', 'undefined')})"
        elif m.get("unit") == "ratio":
            shown = f"{m['value'] * 100:.2f}%"
        else:
            shown = f"{m['value']:,.0f}"
        flag = " [synthetic]" if m.get("synthetic") else ""
        print(f"    {key:<26} {shown}{flag}")


def _cmd_growth_event_record(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "event-record", project=args.project, event_type=args.event_type,
        source=args.source, content_id=args.content_id, campaign=args.campaign,
        platform=args.platform, value=args.value, name=args.name,
        occurred_at=args.occurred_at,
    )


def _cmd_growth_event_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("event-list", project=args.project, content_id=args.content_id,
                      campaign=args.campaign, platform=args.platform)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("events", [])
    if not rows:
        print("No events.")
        return 0
    print(f"Events ({len(rows)})")
    _hr()
    for e in rows:
        print(f"  {e['occurred_at']}  {e['event_type']:<18} {e['source']:<10} "
              f"{e.get('platform', '') or '—':<10} {e['value']:>8,.0f}  {e.get('content_id', '')}")
    return 0


def _cmd_growth_analytics(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("analytics", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    a = r.data.get("analytics", {})
    print(f"Growth analytics: {a.get('project', '')}")
    _hr()
    _synthetic_banner(bool(a.get("synthetic")))
    print(f"  Events    : {a.get('event_count', 0):,}")
    print(f"  Content   : {a.get('content_total', 0)} total, "
          f"{a.get('content_approved', 0)} approved, {a.get('content_published', 0)} published, "
          f"{a.get('content_failed', 0)} failed")
    print(f"  Campaigns : {a.get('campaigns_total', 0)}")
    print("  Metrics:")
    _print_metrics(a.get("metrics", {}))
    return 0


def _cmd_growth_analytics_campaign(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("analytics-campaign", project=args.project, campaign_id=args.campaign)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    a = r.data.get("analytics", {})
    print(f"{a.get('campaign_id', '')}  [{a.get('status', '')}]  {a.get('campaign_name', '')}")
    _hr()
    _synthetic_banner(bool(a.get("synthetic")))
    print(f"  Objective : {a.get('objective', '') or '—'}")
    print(f"  Content   : {a.get('content_created', 0)} created, "
          f"{a.get('content_approved', 0)} approved, {a.get('content_published', 0)} published")
    print(f"  Top platf.: {a.get('top_platform', '') or '—'}")
    top, worst = a.get("top_content"), a.get("worst_content")
    print(f"  Best      : {(top or {}).get('content_id', '—')} "
          f"({(top or {}).get('conversions', 0):,.0f} conversions)")
    if worst:
        print(f"  Worst     : {worst.get('content_id', '—')} "
              f"({worst.get('conversions', 0):,.0f} conversions)"
              + ("" if worst.get("measured") else "  [never measured]"))
    prog = a.get("objective_progress", {})
    if prog.get("percent") is not None:
        print(f"  Progress  : {prog['achieved']:,.0f} / {prog['target']:,.0f} "
              f"({prog['percent']:.1f}%)")
    else:
        print(f"  Progress  : {prog.get('achieved', 0):,.0f} — {prog.get('reason', '')}")
    print("  Metrics:")
    _print_metrics(a.get("metrics", {}))
    return 0


def _cmd_growth_analytics_platform(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("analytics-platform", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("platforms", [])
    if not rows:
        print("No platform data.")
        return 0
    print(f"Platform performance ({len(rows)})")
    _hr()
    for row in rows:
        m = row.get("metrics", {})
        reach = (m.get("reach") or {}).get("value") or 0
        clicks = (m.get("clicks") or {}).get("value") or 0
        conv = (m.get("conversions") or {}).get("value") or 0
        flag = " [synthetic]" if row.get("synthetic") else ""
        print(f"  {row['platform']:<12} reach={reach:>9,.0f}  clicks={clicks:>7,.0f}  "
              f"conversions={conv:>6,.0f}{flag}")
    return 0


def _cmd_growth_analytics_series(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("analytics-series", project=args.project, metric=args.metric,
                      granularity=args.granularity, campaign=args.campaign)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    s = r.data.get("series", {})
    print(f"{s.get('metric', '')} by {s.get('granularity', '')} — {s.get('project', '')}")
    _hr()
    _synthetic_banner(bool(s.get("synthetic")))
    for point in s.get("points", []):
        value = point.get("value")
        shown = "—" if value is None else f"{value:,.0f}"
        print(f"  {point['bucket']}  {shown:>10}")
    if not s.get("points"):
        print("  No events in any bucket.")
    return 0


def _cmd_growth_analytics_trend(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("analytics-trend", project=args.project, metric=args.metric,
                      period_days=args.period_days)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    t = r.data.get("trend", {})
    arrow = {"up": "▲", "down": "▼", "flat": "=", "unknown": "?"}.get(t.get("direction"), "?")
    print(f"{t.get('metric', '')} over {args.period_days}d — {t.get('direction', '')} {arrow}")
    _hr()
    _synthetic_banner(bool(t.get("synthetic")))
    cur, prev = t.get("current"), t.get("previous")
    print(f"  Current  : {'—' if cur is None else format(cur, ',.0f')}")
    print(f"  Previous : {'—' if prev is None else format(prev, ',.0f')}")
    if t.get("percent_change") is not None:
        print(f"  Change   : {t['percent_change']:+.1f}%")
    elif t.get("reason"):
        print(f"  Change   : — ({t['reason']})")
    return 0


def _cmd_growth_analytics_funnel(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("analytics-funnel", project=args.project, campaign=args.campaign,
                      platform=args.platform)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    f = r.data.get("funnel", {})
    print(f"Conversion funnel — {f.get('project', '')}")
    _hr()
    _synthetic_banner(bool(f.get("synthetic")))
    for stage in f.get("stages", []):
        rate = stage.get("rate_from_previous")
        shown = "—" if rate is None else f"{rate * 100:.1f}%"
        note = f"  ({stage['reason']})" if stage.get("reason") and rate is None else ""
        print(f"  {stage['stage']:<16} {stage['count']:>10,.0f}   from previous: {shown}{note}")
    overall = f.get("overall_rate")
    print(f"  {'overall':<16} {f.get('total_conversions', 0):>10,.0f}   "
          f"rate: {'—' if overall is None else format(overall * 100, '.2f') + '%'}")
    return 0


def _cmd_growth_snapshot_take(args: argparse.Namespace) -> int:
    return _growth_call(args, "snapshot-take", project=args.project, note=args.note)


def _cmd_growth_snapshot_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("snapshot-list", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("snapshots", [])
    if not rows:
        print("No snapshots.")
        return 0
    print(f"Snapshots ({len(rows)})")
    _hr()
    for s in rows:
        flag = " [synthetic]" if s.get("synthetic") else ""
        print(f"  {s['id']}  {s['taken_at']}  {s.get('note', '') or '—'}{flag}")
    return 0


def _cmd_growth_aggregate_write(args: argparse.Namespace) -> int:
    return _growth_call(args, "aggregate-write", project=args.project)


def _cmd_growth_brain(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("brain-analyze", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    a = r.data.get("analysis", {})
    print(f"Growth Brain — {a.get('project', '')}")
    _hr()
    print("  Deterministic rule engine. No model was called.")
    scores = a.get("scores", {}).get("workspace_health", {})
    value = scores.get("value")
    print(f"  Workspace health : {'—' if value is None else f'{value:.0f}'} "
          f"({scores.get('band', 'unknown')})")
    print(f"  Observations     : {len(a.get('observations', []))}")
    print(f"  Opportunities    : {len(a.get('opportunities', []))}")
    print(f"  Recommendations  : {len(a.get('recommendations', []))}")
    print(f"  Hypotheses       : {len(a.get('hypotheses', []))}  (UNCONFIRMED)")
    print(f"  Experiments       : {len(a.get('suggested_experiments', []))} proposed")
    mem = a.get("memory", {})
    print(f"  Memory           : {len(mem.get('validated', []))} validated, "
          f"{len(mem.get('tentative', []))} tentative")
    return 0


def _print_recommendations(rows: list[dict[str, Any]]) -> int:
    if not rows:
        print("No recommendations. Nothing cleared the evidence threshold.")
        return 0
    print(f"Recommendations ({len(rows)})")
    _hr()
    for rec in rows:
        print(f"  [{rec['priority']}] {rec['title']}")
        print(f"        {rec['summary']}")
        print(f"        Action     : {rec['suggested_action']}")
        print(f"        Impact     : {rec['expected_impact']}")
        print(f"        Metric     : {rec['success_metric']}")
        print(f"        Falsifier  : {rec['falsifier']}")
        ev = rec.get("evidence", {})
        flag = " [synthetic]" if ev.get("synthetic") else ""
        print(f"        Evidence   : {len(ev.get('metrics', []))} metric(s), "
              f"n={ev.get('sample_size', 0)}{flag}")
        print()
    return 0


def _cmd_growth_brain_recommendations(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("brain-recommendations", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    return _print_recommendations(r.data.get("recommendations", []))


def _cmd_growth_brain_hypotheses(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("brain-hypotheses", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("hypotheses", [])
    if not rows:
        print("No hypotheses.")
        return 0
    print(f"UNCONFIRMED HYPOTHESES ({len(rows)})")
    _hr()
    print("  These are candidate explanations, NOT findings. Confirm with an")
    print("  experiment before acting on any of them as fact.")
    print()
    for h in rows:
        print(f"  ? {h['statement']}")
        print(f"      {h['rationale']}")
        print(f"      Test with : {h.get('proposed_experiment') or '—'}")
        print()
    return 0


def _cmd_growth_brain_opportunities(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("brain-opportunities", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("opportunities", [])
    if not rows:
        print("No opportunities detected.")
        return 0
    print(f"Opportunities ({len(rows)})")
    _hr()
    for o in rows:
        flag = " [synthetic]" if o.get("synthetic") else ""
        upside = (
            f"{o['expected_upside']:g} {o.get('upside_unit', '')}"
            if o.get("expected_upside") is not None
            else f"not quantified — {o.get('upside_reason', 'no data')}"
        )
        print(f"  [{o['severity']:<8}] {o['title']}{flag}")
        print(f"             {o['detail']}")
        print(f"             Upside: {upside}")
        print(f"             Rule  : {o['rule']}  n={o['sample_size']}")
        print()
    return 0


def _cmd_growth_brain_scores(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("brain-scores", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    s = r.data.get("scores", {})
    def _line(label: str, score: dict[str, Any]) -> None:
        value = score.get("value")
        shown = "—" if value is None else f"{value:6.1f}"
        note = f"  ({score.get('reason')})" if value is None and score.get("reason") else ""
        print(f"  {label:<28} {shown}  {score.get('band', 'unknown')}{note}")
    print(f"Health scores — {s.get('project', '')}")
    _hr()
    _line("workspace", s.get("workspace_health", {}))
    for name, score in (s.get("campaign_health") or {}).items():
        _line(f"campaign {name}", score)
    for name, score in (s.get("channel_health") or {}).items():
        _line(f"channel {name}", score)
    return 0


def _cmd_growth_brain_forecasts(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("brain-forecasts", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    f = r.data.get("forecasts", {})
    print(f"Forecasts — {f.get('project', '')}  (linear run-rate; assumes nothing changes)")
    _hr()
    for cid, row in (f.get("campaigns") or {}).items():
        fc = row.get("forecast", {})
        value = fc.get("value")
        shown = "—" if value is None else f"{value:,.1f}"
        print(f"  {cid}  projected={shown:<12} target={row.get('target') or '—'}  "
              f"verdict={row.get('verdict')}")
        if value is None and fc.get("reason"):
            print(f"      {fc['reason']}")
    for key in ("expected_conversions", "expected_engagement"):
        fc = f.get(key, {})
        value = fc.get("value")
        shown = "—" if value is None else f"{value:,.1f}"
        print(f"  {key:<24} {shown}")
        if value is None and fc.get("reason"):
            print(f"      {fc['reason']}")
    return 0


def _cmd_growth_brain_experiments(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("brain-experiments", project=args.project)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("experiments", [])
    if not rows:
        print("No experiments proposed.")
        return 0
    print(f"Proposed experiments ({len(rows)}) — ALL require human approval to run")
    _hr()
    for e in rows:
        print(f"  {e['id']}  varies {e['variable']}")
        print(f"      Hypothesis : {e['hypothesis']}")
        print(f"      Metric     : {e['metric']}  (min sample {e['minimum_sample']}/arm, "
              f"{e['measurement_days']}d)")
        print(f"      A          : {e['variation_a']['description']}")
        print(f"      B          : {e['variation_b']['description']}")
        print()
    return 0


def _cmd_growth_memory_record(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "memory-record", project=args.project, statement=args.statement,
        sample_size=args.sample_size, category=args.category,
        metric_affected=args.metric_affected,
    )


def _cmd_growth_memory_list(args: argparse.Namespace) -> int:
    monday = _monday(args)
    r = monday.growth("memory-list", project=args.project, status=args.status)
    if not r.success:
        print(f"Error: {r.message}", file=sys.stderr)
        return 1
    rows = r.data.get("memory", [])
    if not rows:
        print("No marketing memory.")
        return 0
    print(f"Marketing memory ({len(rows)})")
    _hr()
    for m in rows:
        print(f"  {m['id']}  {m['rendered']}")
    return 0


def _cmd_growth_memory_validate(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "memory-validate", project=args.project, entry_id=args.entry_id,
        by=args.by, reason=args.reason,
    )


def _cmd_growth_memory_invalidate(args: argparse.Namespace) -> int:
    return _growth_call(
        args, "memory-invalidate", project=args.project, entry_id=args.entry_id,
        by=args.by, reason=args.reason,
    )
