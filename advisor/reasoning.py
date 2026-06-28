"""
Pure synthesis functions for the AdvisorEngine.

All functions take structured data and return insights.
No I/O. No external calls. Fully testable in isolation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from advisor.advisory import Action, Advisory, Risk

if TYPE_CHECKING:
    from doctor.result import DoctorReport

# Severity ordering for sorting
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Doctor finding categories → high-impact flag
_HIGH_IMPACT_CATEGORIES = {"tests", "git", "config"}

# Known entry types that should exist in a healthy knowledge base
_EXPECTED_KB_TYPES = [
    "sprint",
    "decision",
    "pattern",
    "lesson",
    "bug",
    "runbook",
]

# Threshold for "many" TODO markers
_MANY_TODOS = 20


# ---------------------------------------------------------------------------
# Risk synthesis
# ---------------------------------------------------------------------------

def synthesize_risks(
    doctor_report: "DoctorReport",
    knowledge_entries: list[Any],
    active_tasks: list[Any],
) -> list[Risk]:
    """
    Produce a ranked list of engineering risks from all available data sources.

    Risks are deduplicated (same title + category) and sorted:
    critical → high → medium → low.
    """
    from doctor.finding import Severity

    risks: list[Risk] = []
    seen: set[str] = set()  # deduplication key: (category, title[:40])

    def _add(risk: Risk) -> None:
        key = f"{risk.category}:{risk.title[:40]}"
        if key not in seen:
            seen.add(key)
            risks.append(risk)

    # ── From DoctorReport findings ──────────────────────────────────────────
    for finding in doctor_report.all_findings:
        if finding.severity == Severity.CRITICAL:
            _add(Risk(
                title=finding.title,
                severity="critical",
                category=finding.category,
                impact=_critical_impact(finding),
                recommendation=finding.recommendation or "Address this issue immediately.",
                source="doctor",
            ))
        elif finding.severity == Severity.WARNING:
            sev = "high" if finding.category in _HIGH_IMPACT_CATEGORIES else "medium"
            _add(Risk(
                title=finding.title,
                severity=sev,
                category=finding.category,
                impact=_warning_impact(finding),
                recommendation=finding.recommendation or "Review and address this warning.",
                source="doctor",
            ))

    # ── From knowledge store ─────────────────────────────────────────────────
    if not knowledge_entries:
        _add(Risk(
            title="Knowledge base is empty",
            severity="high",
            category="knowledge",
            impact=(
                "No institutional memory. Every bug, decision, and pattern must be "
                "re-discovered rather than retrieved. `monday ask` returns no results."
            ),
            recommendation="Run `monday migrate` to import existing project documentation.",
            source="knowledge",
        ))
    else:
        present_types = {e.entry_type.value for e in knowledge_entries}
        for ktype in _EXPECTED_KB_TYPES:
            if ktype not in present_types:
                _add(Risk(
                    title=f"No {ktype} entries in knowledge base",
                    severity="low",
                    category="knowledge",
                    impact=f"Knowledge queries for {ktype} will return no results.",
                    recommendation=f"Add {ktype} entries via `monday learn` or `monday migrate`.",
                    source="knowledge",
                ))

    # ── From task system ─────────────────────────────────────────────────────
    for task in active_tasks:
        if task.status.value == "blocked":
            sev = "high" if task.priority.value in ("P0", "P1") else "medium"
            _add(Risk(
                title=f"Task blocked: {task.title[:60]}",
                severity=sev,
                category="tasks",
                impact=f"{task.id} cannot progress until the blocker is resolved.",
                recommendation=f"Identify and resolve blocker for {task.id}.",
                source="tasks",
            ))

    urgent_backlog = [
        t for t in active_tasks
        if t.status.value == "backlog" and t.priority.value in ("P0", "P1")
    ]
    if urgent_backlog:
        titles = ", ".join(t.id for t in urgent_backlog[:3])
        _add(Risk(
            title=f"{len(urgent_backlog)} high-priority task(s) unstarted in backlog",
            severity="high",
            category="tasks",
            impact="High-priority work is not progressing.",
            recommendation=f"Start high-priority tasks: {titles}.",
            source="tasks",
        ))

    risks.sort(key=lambda r: _SEV_ORDER.get(r.severity, 9))
    return risks


# ---------------------------------------------------------------------------
# Action synthesis
# ---------------------------------------------------------------------------

def synthesize_next_actions(
    risks: list[Risk],
    active_tasks: list[Any],
    knowledge_entries: list[Any],
    doctor_report: "DoctorReport",
    workflow_runs: list[dict[str, Any]],
) -> list[Action]:
    """
    Produce a ranked list of highest-value next actions.

    Ranking principle: fix-critical → unlock-blocked → compound-value → polish.
    Actions are deduplicated and capped at 10.
    """
    from doctor.finding import Severity

    actions: list[Action] = []
    seen_titles: set[str] = set()

    def _add(action: Action) -> None:
        if action.title not in seen_titles:
            seen_titles.add(action.title)
            actions.append(action)

    # ── Priority 1: Fix critical issues ─────────────────────────────────────
    critical_risks = [r for r in risks if r.severity == "critical"]
    for i, risk in enumerate(critical_risks[:3]):
        _add(Action(
            title=f"Fix: {risk.title}",
            priority=1,
            category=risk.category,
            rationale=risk.impact,
            effort="hours",
            command=_command_for_risk(risk),
        ))

    # ── Priority 2: Unblock blocked tasks ───────────────────────────────────
    blocked = [t for t in active_tasks if t.status.value == "blocked"]
    for task in blocked[:2]:
        _add(Action(
            title=f"Resolve blocker: {task.title[:50]}",
            priority=2,
            category="tasks",
            rationale=f"{task.id} is blocked and cannot progress.",
            effort="hours",
            command=f"monday task get {task.id}",
        ))

    # ── Priority 3: Highest compound-value actions ──────────────────────────
    # Empty knowledge base + importable docs = highest unlock value
    if not knowledge_entries:
        _add(Action(
            title="Import knowledge base",
            priority=3,
            category="knowledge",
            rationale=(
                "The knowledge base is empty. Importing documentation unlocks "
                "`monday ask`, pattern matching, and advisory confidence."
            ),
            effort="minutes",
            command="monday migrate --dry-run",
        ))

    # Start high-priority backlog items
    urgent = [t for t in active_tasks if t.status.value == "backlog"
              and t.priority.value in ("P0", "P1")]
    for task in urgent[:2]:
        _add(Action(
            title=f"Start: {task.title[:50]}",
            priority=3,
            category="tasks",
            rationale=f"{task.id} is [{task.priority.value}] and unstarted.",
            effort="hours",
            command=f"monday task start {task.id}",
        ))

    # ── Priority 4: Address high-severity warnings ──────────────────────────
    high_risks = [r for r in risks if r.severity == "high"]
    for risk in high_risks[:3]:
        cmd = _command_for_risk(risk)
        if cmd:
            _add(Action(
                title=risk.recommendation.split(".")[0] if risk.recommendation else risk.title,
                priority=4,
                category=risk.category,
                rationale=risk.impact,
                effort=_effort_for_risk(risk),
                command=cmd,
            ))

    # ── Priority 5: Documentation and configuration ─────────────────────────
    from doctor.finding import Severity as Sev
    for finding in doctor_report.all_findings:
        if finding.severity == Sev.WARNING and finding.category == "documentation":
            if finding.recommendation:
                _add(Action(
                    title=finding.recommendation.split(".")[0],
                    priority=5,
                    category="documentation",
                    rationale=finding.title,
                    effort="minutes",
                    command="",
                ))

    # ── Priority 6: Run doctor if stale ─────────────────────────────────────
    # (always useful, low cost)
    _add(Action(
        title="Review full health report",
        priority=6,
        category="quality",
        rationale="Regular health checks catch regressions early.",
        effort="minutes",
        command="monday doctor --verbose",
    ))

    # Renumber by order, cap at 10
    actions = actions[:10]
    for i, a in enumerate(actions, 1):
        a.priority = i
    return actions


# ---------------------------------------------------------------------------
# Sprint goal
# ---------------------------------------------------------------------------

def synthesize_sprint_goal(
    risks: list[Risk],
    active_tasks: list[Any],
    knowledge_entries: list[Any],
) -> tuple[str, str]:
    """
    Recommend a sprint goal and rationale.

    Returns (goal_title, rationale_paragraph).
    Decision tree: criticals → P0/P1 backlog → empty KB → in-progress → default.
    """
    critical_risks = [r for r in risks if r.severity == "critical"]
    high_risks = [r for r in risks if r.severity == "high"]
    in_progress = [t for t in active_tasks if t.status.value == "in-progress"]
    urgent_backlog = [
        t for t in active_tasks
        if t.status.value == "backlog" and t.priority.value in ("P0", "P1")
    ]
    blocked = [t for t in active_tasks if t.status.value == "blocked"]

    # 1. CRITICAL issues must be resolved first
    if critical_risks:
        titles = "; ".join(r.title for r in critical_risks[:2])
        return (
            "Restore engineering health",
            (
                f"{len(critical_risks)} critical issue(s) must be resolved before "
                f"new work can proceed safely: {titles}. "
                "A sprint focused on health restoration will prevent compounding "
                "technical debt and restore deployment confidence."
            ),
        )

    # 2. High-priority unstarted backlog
    if urgent_backlog:
        titles = ", ".join(t.id for t in urgent_backlog[:3])
        return (
            f"Start high-priority work ({', '.join(t.id for t in urgent_backlog[:2])})",
            (
                f"{len(urgent_backlog)} high-priority task(s) are unstarted: {titles}. "
                "These items have the greatest impact on delivery and should be "
                "pulled into active development this sprint."
            ),
        )

    # 3. Empty knowledge base with no other pressures
    if not knowledge_entries:
        return (
            "Establish knowledge foundation",
            (
                "The knowledge base is empty, limiting `monday ask` effectiveness "
                "and advisory confidence. Running `monday migrate` will import existing "
                "documentation and sprint history, giving the platform access to "
                "institutional memory accumulated across prior development cycles."
            ),
        )

    # 4. Blocked tasks need clearing
    if blocked:
        blocker_list = ", ".join(t.id for t in blocked[:3])
        return (
            "Clear task blockers",
            (
                f"{len(blocked)} task(s) are blocked ({blocker_list}). "
                "Resolving these blockers will restore momentum and reduce "
                "coordination overhead."
            ),
        )

    # 5. Continue in-progress work
    if in_progress:
        titles = "; ".join(t.title[:40] for t in in_progress[:2])
        return (
            "Complete in-progress work",
            (
                f"{len(in_progress)} task(s) are currently in progress: {titles}. "
                "Finishing in-flight work before starting new tasks reduces "
                "context switching and delivers completed increments."
            ),
        )

    # 6. Default: high-severity warnings
    if high_risks:
        return (
            "Address high-priority warnings",
            (
                f"{len(high_risks)} high-severity issue(s) are present. "
                "Addressing these will improve platform reliability and reduce "
                "future engineering overhead."
            ),
        )

    # 7. Healthy state — forward momentum
    return (
        "Continue forward momentum",
        (
            "No critical issues are present and all critical systems are healthy. "
            "This sprint can focus on planned feature work and quality improvements."
        ),
    )


# ---------------------------------------------------------------------------
# Technical debt
# ---------------------------------------------------------------------------

def synthesize_debt(
    doctor_report: "DoctorReport",
    knowledge_entries: list[Any],
) -> tuple[str, list[str]]:
    """
    Summarise technical debt from doctor findings and knowledge entries.

    Returns (summary_sentence, list_of_debt_items).
    """
    from doctor.finding import Severity

    items: list[str] = []

    # TODO/FIXME count from doctor
    for finding in doctor_report.all_findings:
        if "TODO" in finding.title or "FIXME" in finding.title:
            data = finding.data or {}
            count = data.get("marker_count", 0)
            if count:
                items.append(f"{count} TODO/FIXME markers in source code")

    # Bug entries in knowledge base
    bug_entries = [e for e in knowledge_entries if e.entry_type.value == "bug"]
    if bug_entries:
        titles = "; ".join(e.title[:50] for e in bug_entries[:3])
        items.append(f"{len(bug_entries)} known bug(s) in knowledge base: {titles}")
    else:
        items.append("No bug entries in knowledge base (may indicate untracked debt)")

    # Deprecated entries
    deprecated = [
        e for e in knowledge_entries
        if hasattr(e, "status") and e.status.value == "deprecated"
    ]
    if deprecated:
        items.append(f"{len(deprecated)} deprecated knowledge entry/entries should be reviewed")

    # Failing tests as debt
    for finding in doctor_report.all_findings:
        if "failed" in finding.title.lower() and finding.severity == Severity.CRITICAL:
            items.append(finding.title)

    if not items:
        summary = "No significant technical debt detected."
    elif len(items) == 1:
        summary = items[0] + "."
    else:
        summary = f"{len(items)} debt sources identified."

    return summary, items


# ---------------------------------------------------------------------------
# Knowledge gaps
# ---------------------------------------------------------------------------

def synthesize_knowledge_gaps(
    knowledge_entries: list[Any],
    active_tasks: list[Any],
) -> list[str]:
    """
    Identify topics that lack knowledge coverage.

    Gaps are inferred from: missing expected entry types, task topics
    with no matching knowledge entries, and knowledge base emptiness.
    """
    gaps: list[str] = []

    if not knowledge_entries:
        gaps.append("No knowledge entries of any type")
        return gaps

    present_types = {e.entry_type.value for e in knowledge_entries}
    for ktype in _EXPECTED_KB_TYPES:
        if ktype not in present_types:
            gaps.append(f"No {ktype} entries captured")

    # Tasks with topics not covered in KB
    if active_tasks and knowledge_entries:
        kb_text = " ".join(
            f"{e.title} {' '.join(e.tags)}" for e in knowledge_entries
        ).lower()
        for task in active_tasks[:10]:
            keywords = task.title.lower().split()
            meaningful = [w for w in keywords if len(w) > 4]
            if meaningful and not any(w in kb_text for w in meaningful):
                gaps.append(f"No knowledge covering task topic: '{task.title[:50]}'")

    return gaps[:8]  # cap to avoid noise


# ---------------------------------------------------------------------------
# Documentation gaps
# ---------------------------------------------------------------------------

def synthesize_documentation_gaps(doctor_report: "DoctorReport") -> list[str]:
    """Extract documentation gaps from the doctor's documentation analyzer."""
    from doctor.finding import Severity

    gaps: list[str] = []
    for finding in doctor_report.all_findings:
        if finding.category == "documentation" and finding.severity in (
            Severity.WARNING, Severity.CRITICAL
        ):
            gaps.append(finding.title)
    return gaps


# ---------------------------------------------------------------------------
# Repository summary
# ---------------------------------------------------------------------------

def summarize_repository(
    project_root_name: str,
    doctor_report: "DoctorReport",
    knowledge_entries: list[Any],
    active_tasks: list[Any],
    workflow_runs: list[dict[str, Any]],
) -> str:
    """
    Generate a natural-language summary of the repository state.

    Template-based — no external model. Reads structured data points
    from all available sources and composes a readable paragraph.
    """
    from doctor.finding import Severity

    lines: list[str] = []

    # Health
    score = doctor_report.health_score
    grade = doctor_report.grade
    n_crit = len([f for f in doctor_report.all_findings if f.severity == Severity.CRITICAL])
    n_warn = len([f for f in doctor_report.all_findings if f.severity == Severity.WARNING])

    health_desc = (
        f"Repository health is {grade} ({score}/100)"
        + (f" with {n_crit} critical issue(s)" if n_crit else "")
        + (f" and {n_warn} warning(s)" if n_warn else "")
        + "."
    )
    lines.append(health_desc)

    # Git
    git_findings = [f for f in doctor_report.all_findings if f.category == "git"]
    branch_finding = next((f for f in git_findings if "branch" in f.title.lower()), None)
    commit_finding = next((f for f in git_findings if "commit" in f.title.lower()), None)
    dirty_finding = next((f for f in git_findings if "dirty" in f.title.lower()), None)

    git_parts = []
    if branch_finding and branch_finding.data.get("branch"):
        git_parts.append(f"active branch: {branch_finding.data['branch']}")
    if commit_finding and commit_finding.data.get("recent_commits"):
        n = len(commit_finding.data["recent_commits"])
        git_parts.append(f"{n} recent commit(s)")
    if dirty_finding and dirty_finding.data.get("changed_files"):
        git_parts.append(f"{dirty_finding.data['changed_files']} uncommitted change(s)")
    if git_parts:
        lines.append("Git: " + "; ".join(git_parts) + ".")

    # Tests
    test_findings = [f for f in doctor_report.all_findings if f.category == "tests"]
    for f in test_findings:
        if f.data.get("total_tests"):
            lines.append(f"{f.data['total_tests']} test(s) in last run.")
            break
        if f.data.get("test_files"):
            lines.append(f"{f.data['test_files']} test file(s) found.")
            break

    # Knowledge
    if knowledge_entries:
        from collections import Counter
        type_counts = Counter(e.entry_type.value for e in knowledge_entries)
        top = sorted(type_counts.items(), key=lambda t: t[1], reverse=True)[:3]
        kb_desc = f"Knowledge base: {len(knowledge_entries)} active entries"
        if top:
            kb_desc += " (" + ", ".join(f"{v} {k}" for k, v in top) + ")"
        lines.append(kb_desc + ".")
    else:
        lines.append("Knowledge base: empty (0 entries).")

    # Tasks
    if active_tasks:
        in_prog = sum(1 for t in active_tasks if t.status.value == "in-progress")
        blocked = sum(1 for t in active_tasks if t.status.value == "blocked")
        task_desc = f"{len(active_tasks)} active task(s)"
        if in_prog:
            task_desc += f", {in_prog} in progress"
        if blocked:
            task_desc += f", {blocked} blocked"
        lines.append("Tasks: " + task_desc + ".")
    else:
        lines.append("Tasks: none active.")

    # Workflows
    if workflow_runs:
        lines.append(f"Workflows: {len(workflow_runs)} execution(s) logged.")

    return " ".join(lines)


# ---------------------------------------------------------------------------
# Confidence score
# ---------------------------------------------------------------------------

def compute_confidence(
    knowledge_entries: list[Any],
    active_tasks: list[Any],
    workflow_runs: list[dict[str, Any]],
    doctor_ran_cleanly: bool,
) -> float:
    """
    Score how much confidence the advisor has in its analysis.

    Confidence scales with data richness. The maximum is 0.85 —
    full confidence requires an LLM to validate assumptions.
    """
    score = 0.25  # base: something is always uncertain

    if knowledge_entries:
        # Scale by knowledge richness (0.05–0.20)
        kb_score = min(len(knowledge_entries) / 50, 1.0) * 0.20
        score += kb_score

    if active_tasks:
        score += 0.10

    if doctor_ran_cleanly:
        score += 0.15

    if workflow_runs:
        score += 0.08

    # Bonus for diverse knowledge types
    if knowledge_entries:
        from collections import Counter
        type_count = len(Counter(e.entry_type.value for e in knowledge_entries))
        if type_count >= 3:
            score += 0.07

    return round(min(score, 0.85), 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _critical_impact(finding: Any) -> str:
    cat = finding.category
    if cat == "tests":
        return "Failing tests indicate broken functionality. Deployment is risky."
    if cat == "git":
        return "Version control integrity is compromised."
    if cat == "config":
        return "Invalid configuration may cause runtime failures."
    return "This critical issue blocks safe operation or deployment."


def _warning_impact(finding: Any) -> str:
    cat = finding.category
    if cat == "tests":
        return "Test health is degraded. Regressions may go undetected."
    if cat == "documentation":
        return "Documentation gaps slow onboarding and increase bus-factor risk."
    if cat == "knowledge":
        return "Knowledge gaps mean work may be duplicated or patterns re-discovered."
    if cat == "tasks":
        return "Task health issues impair delivery predictability."
    if cat == "code-quality":
        return "Code quality signals accumulate into larger maintainability problems."
    return "This warning, if unaddressed, will compound into a larger issue."


def _command_for_risk(risk: Risk) -> str:
    cat = risk.category
    if cat == "tests":
        return "pytest --tb=short -q"
    if cat == "knowledge":
        return "monday migrate --dry-run"
    if cat == "documentation" and "README" in risk.title:
        return ""
    if cat == "config":
        return "monday doctor --only config"
    if cat == "tasks":
        return "monday task list"
    if cat == "git":
        return "git status"
    return ""


def _effort_for_risk(risk: Risk) -> str:
    cat = risk.category
    if cat in ("knowledge", "documentation"):
        return "minutes"
    if cat == "tasks":
        return "hours"
    return "hours"
