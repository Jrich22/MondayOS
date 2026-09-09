"""
The three artefacts a run produces.

**The JSON report** is every measured field for every turn, so a disagreement
about what happened is settled by reading it rather than by re-running.

**The human review package** is for the judgement no program should make. It
shows the question, an excerpt of the answer, what was cited, and why each turn
passed or failed mechanically -- and then stops, because whether a
recommendation was *useful* is a person's call. There is deliberately no
"reasoning quality" number anywhere: it would be the least trustworthy figure in
the report and the one people would quote.

**`release_readiness.md`** is the checklist. It says READY, READY WITH KNOWN
LIMITATIONS, or NOT READY, and shows the evidence for whichever it says.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from acceptance.gates import GateResult, Verdict, overall


@dataclass
class Defect:
    """
    Something the product did that it should not have.

    Written down in the shape a person needs to act on it, because the point of
    finding a defect during acceptance is the punch list, not the count.
    """

    severity: str  # blocker | major | minor
    title: str
    projects: list[str] = field(default_factory=list)
    reproducibility: str = ""
    root_cause: str = ""
    proposed_fix: str = ""
    benchmark_impact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "title": self.title,
            "projects": self.projects,
            "reproducibility": self.reproducibility,
            "root_cause": self.root_cause,
            "proposed_fix": self.proposed_fix,
            "benchmark_impact": self.benchmark_impact,
        }


@dataclass
class AcceptanceReport:
    """One run, whole."""

    provider: dict[str, Any]
    started_at: str
    projects: dict[str, Any] = field(default_factory=dict)
    turns: list[Any] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)
    incidents: list[Any] = field(default_factory=list)
    defects: list[Defect] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return overall(self.gates)

    def to_dict(self) -> dict[str, Any]:
        by_project: dict[str, Any] = {}
        for slug, record in self.projects.items():
            by_project[slug] = dict(record)
            by_project[slug]["turns"] = [
                t.to_dict() for t in self.turns if getattr(t, "project", "") == slug
            ]
        return {
            "schema": 1,
            "kind": "acceptance",
            "provider": self.provider,
            "started_at": self.started_at,
            "verdict": self.verdict,
            "projects": by_project,
            "gates": [g.to_dict() for g in self.gates],
            "provider_incidents": [i.to_dict() for i in self.incidents],
            "defects": [d.to_dict() for d in self.defects],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)

    # ------------------------------------------------------------- human copy

    def review_markdown(self) -> str:
        """The package a person reads to judge answer quality."""
        out: list[str] = [
            "# Live acceptance — human review",
            "",
            f"Provider **{self.provider.get('name', '?')} · {self.provider.get('model', '?')}**"
            f" · started {self.started_at}",
            "",
            "Mechanical results are in the JSON report. What follows is for the judgement",
            "no program should make: whether these answers are actually useful. Nothing here",
            "is scored.",
            "",
        ]
        for slug in sorted(self.projects):
            record = self.projects[slug]
            if not record.get("available"):
                out += [f"## {slug}", "", f"Skipped — {record.get('reason', 'unavailable')}.", ""]
                continue
            out += [f"## {slug}", ""]
            for turn in [t for t in self.turns if getattr(t, "project", "") == slug]:
                out += self._turn_markdown(turn)
            out.append("")
        return "\n".join(out)

    def _turn_markdown(self, turn: Any) -> list[str]:
        if turn.skipped:
            return [f"### {turn.turn_id}", "", f"**Skipped** — {turn.skipped}.", ""]

        register = turn.observed_register or "(none)"
        agreed = "as expected" if register == turn.expect_register else "**≠ expected**"
        lines = [
            f"### {turn.turn_id} — {turn.question}",
            "",
            f"- **Register:** {register} ({agreed}: {turn.expect_register})"
            + (f" — {turn.mode_reason}" if turn.mode_reason else ""),
            f"- **Reply:** {turn.answer_chars} chars"
            + (f", {turn.tokens_used} tokens" if turn.tokens_used else "")
            + (", **incomplete**" if turn.incomplete else "")
            + f", {turn.latency_ms} ms",
        ]
        if turn.outcome != "ok":
            lines.append(f"- **Outcome:** {turn.outcome} — {turn.error[:200]}")
        citations = turn.citations or {}
        if citations.get("total"):
            lines.append(
                f"- **Citations:** {citations.get('resolved', 0)}/{citations.get('total', 0)}"
                f" resolved, {citations.get('with_line', 0)} with a line"
                + (
                    f", unresolved: {citations['unresolved']}"
                    if citations.get("unresolved")
                    else ""
                )
                + (
                    f", **outside boundary: {citations['outside_boundary']}**"
                    if citations.get("outside_boundary")
                    else ""
                )
            )
        if turn.recommendation_key:
            scores = ", ".join(f"{k}={v}" for k, v in sorted(turn.scores.items()))
            lines.append(f"- **Recommendation:** `{turn.recommendation_key}` ({scores})")
        if turn.quoted_scores:
            lines.append(f"- **Quoted in the answer:** {turn.quoted_scores}")
        if turn.initiatives.get("invented"):
            lines.append(f"- **⚠ Invented initiatives:** {turn.initiatives['invented']}")
        if turn.invented_decisions:
            lines.append(f"- **⚠ Cited decisions that do not exist:** {turn.invented_decisions}")
        lines += ["", "> " + (turn.answer_excerpt or "(no answer)"), ""]
        return lines

    # --------------------------------------------------------- readiness copy

    def readiness_markdown(self) -> str:
        """The release checklist."""
        exercised = sorted(s for s, r in self.projects.items() if r.get("available"))
        skipped = sorted(s for s, r in self.projects.items() if not r.get("available"))
        failed = [g for g in self.gates if g.verdict is Verdict.FAILED]
        unverifiable = [g for g in self.gates if g.verdict is Verdict.UNVERIFIABLE]
        not_run = [g for g in self.gates if g.verdict is Verdict.NOT_EXERCISED]

        out = [
            "# MondayOS release readiness",
            "",
            f"## {self.verdict}",
            "",
            f"- **Provider:** {self.provider.get('name', '?')} · {self.provider.get('model', '?')}"
            f" · reports stop reason: {self.provider.get('reports_stop_reason', False)}",
            f"- **Projects exercised:** {', '.join(exercised) or 'none'}",
            f"- **Projects skipped:** {', '.join(skipped) or 'none'}",
            f"- **Turns recorded:** {len(self.turns)}",
            f"- **Provider incidents:** {len(self.incidents)}"
            " (reported, never counted as product failures)",
            "",
            "## Gates",
            "",
            "| # | Gate | Verdict | Notes |",
            "|---|---|---|---|",
        ]
        for gate in self.gates:
            note = gate.reason or ("—" if gate.verdict is Verdict.PASSED else "")
            if gate.violations:
                note = f"{note} ({len(gate.violations)} violation(s))"
            out.append(f"| {gate.number} | {gate.name} | **{gate.verdict.value}** | {note} |")

        out += ["", "## Known limitations", ""]
        if unverifiable:
            for gate in unverifiable:
                out.append(f"- Gate {gate.number} ({gate.name}) is **unverifiable**: {gate.reason}")
        if not_run:
            for gate in not_run:
                out.append(f"- Gate {gate.number} ({gate.name}) was **not exercised** by this run.")
        if not unverifiable and not not_run:
            out.append("- None. Every gate was exercised and answerable.")

        out += ["", "## Unresolved defects", ""]
        if self.defects:
            for defect in self.defects:
                out += [
                    f"### [{defect.severity}] {defect.title}",
                    "",
                    f"- **Projects:** {', '.join(defect.projects) or '—'}",
                    f"- **Reproducibility:** {defect.reproducibility or '—'}",
                    f"- **Root cause:** {defect.root_cause or 'not yet established'}",
                    f"- **Proposed fix:** {defect.proposed_fix or 'not yet proposed'}",
                    f"- **Benchmark impact:** {defect.benchmark_impact or '—'}",
                    "",
                ]
        else:
            out.append("- None found during this run.")

        out += ["", "## Recommendation", ""]
        if failed:
            out.append(
                f"**NOT READY.** {len(failed)} gate(s) failed: "
                + ", ".join(f"{g.number} ({g.name})" for g in failed)
                + ". Each failure is a product defect and is listed above."
            )
        elif unverifiable or not_run:
            out.append(
                "**READY WITH KNOWN LIMITATIONS.** No gate failed. Some could not be "
                "answered by this run — see Known limitations — so the guarantees they "
                "cover are untested rather than proven."
            )
        else:
            out.append("**READY.** Every gate was exercised and passed.")
        out.append("")
        return "\n".join(out)


def stamp() -> str:
    """An ISO timestamp for the report header."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
