"""
The question engine — turning a question into retrieved, cited evidence.

This is retrieval, not generation. It classifies what is being asked, gathers the
sources that bear on it from the index and the graph, and hands back an
``Answer`` carrying those sources plus a deterministic finding. A model may then
write prose over it — but the sources are fixed before any model is involved, so
what the model can say is bounded by what the project actually contains.

**Intent classification is pattern-based and explicit.** Eight intents, each with
its own retrieval strategy, because "why did we decide X" and "where is X
implemented" want completely different sources: the first wants decision records,
the second wants symbol definitions. A single similarity search would return the
same thing for both and be wrong for at least one.

**Subject carry-over.** A question with no subject of its own inherits the last
one. Twenty minutes into a conversation about ContextEngine, "where is that
implemented?" means ContextEngine — and requiring the operator to restate it is
the difference between a tool and a search box.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from intelligence.evidence import Citation, CitationKind, Evidence
from intelligence.graph import RelationshipGraph
from intelligence.index import STOPWORDS, ProjectIndex
from intelligence.models import FileKind, Node, NodeKind, Symbol


class Intent(Enum):
    """What kind of answer the question wants."""

    CURRENT_WORK = "current-work"
    WHY_DECISION = "why-decision"
    WHERE_IMPLEMENTED = "where-implemented"
    EVERYTHING_ABOUT = "everything-about"
    WHY_BLOCKED = "why-blocked"
    WHAT_CHANGED = "what-changed"
    WHERE_DOCUMENTED = "where-documented"
    GENERAL = "general"


# Ordered: the first match wins, so more specific patterns come first. "why is
# TASK-0074 blocked" must not be caught by the generic "why" rule.
_PATTERNS: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    (Intent.WHY_BLOCKED, re.compile(r"\bwhy\b.*\b(blocked|stuck|failing|held up)\b", re.I)),
    (
        Intent.WHAT_CHANGED,
        re.compile(r"\bwhat\b.*\bchanged\b|\bchanges?\s+since\b|\brecent(ly)?\b", re.I),
    ),
    (
        Intent.WHY_DECISION,
        # "decis" as well as "decid": "why did we make this decision" is the most
        # natural phrasing and the stem-only pattern missed it entirely.
        re.compile(
            r"\bwhy\b.*\b(decis|decid|choos|chose|pick|rational|reason|approach|design)", re.I
        ),
    ),
    (Intent.WHERE_DOCUMENTED, re.compile(r"\bwhere\b.*\bdocument", re.I)),
    (
        Intent.WHERE_IMPLEMENTED,
        re.compile(r"\bwhere\b.*\b(implement|defined|live|code)|find (every|all)\b", re.I),
    ),
    (
        Intent.EVERYTHING_ABOUT,
        re.compile(
            r"\b(everything|all)\b.*\b(related|about)\b|\bshow me\b.*\b(about|related)\b", re.I
        ),
    ),
    (
        Intent.CURRENT_WORK,
        re.compile(r"\bwhat\b.*\b(currently|now)?\s*(are we|we're|working on|building)\b", re.I),
    ),
)

_TASK_REF = re.compile(r"\b(TASK-\d{3,})\b", re.I)
_ADR_REF = re.compile(r"\b(ADR-\d{3,})\b", re.I)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Words that describe the *question*, not its subject. Removing them stops
# "where is streaming implemented" from searching for "implemented".
_QUESTION_WORDS = frozenset(
    """
    what where why how when who which show me find every all everything about related
    is are was were do does did the a an our my your we us it that this these those
    implemented implement implements documented document documents defined define
    currently now recently since yesterday today building build built working work
    place places code decision decide decided blocked stuck happening
    make made making take takes use used uses using get gets happen happens
    design designed designing way ways thing things explain explains
    """.split()
)

# Words that point back at something already discussed rather than naming
# something new. Their presence is the signal that a question is a follow-up.
_BACK_REFERENCE = frozenset({"it", "its", "that", "this", "them", "those", "these", "there"})


@dataclass
class Answer:
    """
    What the engine found, and what it was found from.

    ``finding`` is assembled deterministically from the retrieved sources — no
    model. It is a factual summary a caller can show as-is, or hand to a
    responder as grounded material.
    """

    question: str
    intent: Intent
    subject: str
    finding: str
    evidence: Evidence = field(default_factory=Evidence)
    # Terms actually used for retrieval, so a poor answer is diagnosable.
    terms: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return not self.evidence.empty

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent.value,
            "subject": self.subject,
            "finding": self.finding,
            "terms": list(self.terms),
            "grounded": self.grounded,
            "evidence": self.evidence.to_dict(),
        }

    def render(self) -> str:
        """The answer as text, with its Based-on line."""
        return f"{self.finding}\n\n{self.evidence.summary()}"


def classify(question: str) -> Intent:
    """Which retrieval strategy this question wants."""
    for intent, pattern in _PATTERNS:
        if pattern.search(question):
            return intent
    return Intent.GENERAL


_ARTEFACT = re.compile(r"\b((?:TASK|ADR|DEC|DOC|RES)-\d{3,})\b", re.I)


def subject_terms(question: str) -> list[str]:
    """
    The nouns a question is actually about.

    Question words are stripped, so "where is streaming implemented" searches for
    `streaming` rather than for `where`, `implemented` and `streaming` equally.
    Order is preserved: the first substantive word is usually the subject.

    Artefact ids are kept whole. Splitting `TASK-0074` on the hyphen would yield
    `task` — a word so common in this project that it retrieves everything, which
    is worse than retrieving nothing.
    """
    out: list[str] = []
    consumed: set[str] = set()
    for match in _ARTEFACT.finditer(question):
        ref = match.group(1).upper()
        if ref not in out:
            out.append(ref)
        consumed.add(ref.split("-")[0].lower())

    for word in _WORD.findall(question):
        if word.lower() in consumed:
            continue
        lowered = word.lower()
        if lowered in _QUESTION_WORDS or lowered in STOPWORDS:
            continue
        if lowered not in out:
            out.append(lowered)
    return out


class QuestionEngine:
    """
    Answers questions about one project from its index and graph.

    Scoped to a single project by construction — the index and graph it holds
    were built for one, and there is no argument that reaches another.
    """

    def __init__(
        self,
        index: ProjectIndex,
        graph: RelationshipGraph,
        tasks: list[dict[str, Any]] | None = None,
    ) -> None:
        self._index = index
        self._graph = graph
        self._tasks = tasks or []

    # ------------------------------------------------------------------ ask

    def ask(self, question: str, carry: str = "") -> Answer:
        """
        Answer one question.

        ``carry`` is the subject of the conversation so far. It is used only when
        the question supplies no subject of its own — an explicit subject always
        wins, so carry-over can never override what was actually asked.
        """
        intent = classify(question)
        terms = subject_terms(question)
        # Carry-over. Two cases, and the second is the one that matters.
        #
        # A question with no subject at all inherits the last one outright.
        #
        # A question that *points back* — "find every place **it** is used" —
        # keeps the carried subject and treats its own words as refinement.
        # Relying only on a verb stoplist made this fragile: a single missing
        # word ("used") turned a follow-up into a search for that word. The
        # pronoun is the reliable signal, because it is what makes the sentence
        # a follow-up in the first place.
        carried = subject_terms(carry) if carry else []
        if not terms and carried:
            terms = carried
        elif carried and _points_back(question):
            terms = carried + [t for t in terms if t not in carried]
        subject = " ".join(terms[:3])

        handler = {
            Intent.CURRENT_WORK: self._current_work,
            Intent.WHY_DECISION: self._why_decision,
            Intent.WHERE_IMPLEMENTED: self._where_implemented,
            Intent.EVERYTHING_ABOUT: self._everything_about,
            Intent.WHY_BLOCKED: self._why_blocked,
            Intent.WHAT_CHANGED: self._what_changed,
            Intent.WHERE_DOCUMENTED: self._where_documented,
            Intent.GENERAL: self._general,
        }[intent]

        answer = handler(question, terms)
        answer.intent = intent
        answer.subject = subject
        answer.terms = terms
        return answer

    # ------------------------------------------------------------ strategies

    def _current_work(self, question: str, terms: list[str]) -> Answer:
        in_progress = [t for t in self._tasks if str(t.get("status")) == "in-progress"]
        recent = self._commits(limit=5)
        evidence = Evidence()

        for task in in_progress:
            evidence.add(
                Citation(
                    kind=CitationKind.TASK,
                    reference=str(task.get("id", "")),
                    label=str(task.get("title", "")),
                    because="in progress",
                )
            )
        for sha, subject in recent:
            evidence.add(
                Citation(
                    kind=CitationKind.COMMIT,
                    reference=sha,
                    label=subject,
                    because="recent commit",
                )
            )

        if in_progress:
            lines = [f"{t['id']} · {t['title']}" for t in in_progress]
            finding = "In progress:\n" + "\n".join(f"  {line}" for line in lines)
        else:
            finding = "No task is marked in progress."
        if recent:
            finding += "\n\nMost recent commits:\n" + "\n".join(
                f"  {sha} {subject}" for sha, subject in recent
            )
        return Answer(question, Intent.CURRENT_WORK, "", finding, evidence)

    def _why_decision(self, question: str, terms: list[str]) -> Answer:
        evidence = Evidence()
        found = self._decisions_matching(question, terms)

        for node, reason in found:
            evidence.add(
                Citation(
                    kind=CitationKind.DECISION,
                    reference=node.label.split(":")[0].strip(),
                    label=node.label,
                    path=node.path,
                    line=node.line,
                    because=reason,
                )
            )
            # What implements the decision is part of why it still holds.
            for other, edge in self._graph.related(node.id, depth=1, limit=6):
                if other.kind in (NodeKind.FILE, NodeKind.TASK):
                    evidence.add(self._cite_node(other, edge.because))

        if not found:
            # No ADR covers this — say so, and still show what the subject *is*.
            #
            # Returning nothing here was wrong twice over: it dropped the answer
            # to a reasonable question, and because an ungrounded answer produces
            # no context source at all, it also silently dropped the carried
            # subject from the conversation's view. "The reasoning was never
            # written down, but here is the implementation and its docs" is both
            # true and useful; bare silence is neither.
            for path, reason in self._files_matching(terms, kinds={FileKind.DOCUMENTATION})[:4]:
                evidence.add(self._cite_file(path, reason))
            for symbol in [s for t in terms[:2] for s in self._index.search_symbols(t, limit=3)][
                :4
            ]:
                evidence.add(
                    Citation(
                        kind=CitationKind.SYMBOL,
                        reference=symbol.qualified,
                        label=f"{symbol.kind.value} {symbol.qualified}",
                        path=symbol.path,
                        line=symbol.line,
                        end_line=symbol.end_line,
                        because=f"defines {symbol.name}",
                    )
                )

            note = (
                "No decision record covers this — the reasoning may never have been "
                "written down as an ADR."
            )
            if evidence.empty:
                return Answer(question, Intent.WHY_DECISION, "", note, evidence)
            return Answer(
                question,
                Intent.WHY_DECISION,
                "",
                note
                + "\n\nWhat exists instead:\n"
                + "\n".join(f"  {c.display()}" for c in evidence.citations[:8]),
                evidence,
            )

        finding = "Decisions on record:\n" + "\n".join(
            f"  {n.label}  ({n.path}:{n.line})" for n, _ in found
        )
        return Answer(question, Intent.WHY_DECISION, "", finding, evidence)

    def _where_implemented(self, question: str, terms: list[str]) -> Answer:
        evidence = Evidence()
        definitions: list[Symbol] = []
        for term in terms[:3]:
            definitions.extend(self._index.search_symbols(term, limit=8))

        seen: set[tuple[str, int]] = set()
        unique: list[Symbol] = []
        for symbol in definitions:
            key = (symbol.path, symbol.line)
            if key not in seen:
                seen.add(key)
                unique.append(symbol)

        for symbol in unique[:12]:
            evidence.add(
                Citation(
                    kind=CitationKind.SYMBOL,
                    reference=symbol.qualified,
                    label=f"{symbol.kind.value} {symbol.qualified}",
                    path=symbol.path,
                    line=symbol.line,
                    end_line=symbol.end_line,
                    because=f"defines {symbol.name}",
                )
            )

        # Files that mention it but do not define it — the usage sites.
        mentions = self._files_matching(terms, exclude={s.path for s in unique})
        for path, reason in mentions[:8]:
            evidence.add(self._cite_file(path, reason))

        if not unique and not mentions:
            return Answer(
                question,
                Intent.WHERE_IMPLEMENTED,
                "",
                f"Nothing in the index defines or mentions {' '.join(terms) or 'that'}.",
                evidence,
            )

        parts: list[str] = []
        if unique:
            parts.append(
                "Defined in:\n"
                + "\n".join(
                    f"  {s.kind.value} {s.qualified} — {s.path}:{s.line}" for s in unique[:12]
                )
            )
        if mentions:
            parts.append("Also referenced in:\n" + "\n".join(f"  {p}" for p, _ in mentions[:8]))
        return Answer(question, Intent.WHERE_IMPLEMENTED, "", "\n\n".join(parts), evidence)

    def _everything_about(self, question: str, terms: list[str]) -> Answer:
        evidence = Evidence()
        buckets: dict[str, list[str]] = {}

        for symbol in [s for t in terms[:2] for s in self._index.search_symbols(t, limit=6)][:10]:
            evidence.add(
                Citation(
                    kind=CitationKind.SYMBOL,
                    reference=symbol.qualified,
                    label=f"{symbol.kind.value} {symbol.qualified}",
                    path=symbol.path,
                    line=symbol.line,
                    end_line=symbol.end_line,
                    because=f"defines {symbol.name}",
                )
            )
            buckets.setdefault("Code", []).append(
                f"{symbol.qualified} — {symbol.path}:{symbol.line}"
            )

        for node, reason in self._decisions_matching(question, terms)[:5]:
            evidence.add(
                Citation(
                    kind=CitationKind.DECISION,
                    reference=node.label.split(":")[0].strip(),
                    label=node.label,
                    path=node.path,
                    line=node.line,
                    because=reason,
                )
            )
            buckets.setdefault("Decisions", []).append(node.label)

        for path, reason in self._files_matching(terms, kinds={FileKind.TEST})[:6]:
            evidence.add(self._cite_file(path, reason, kind=CitationKind.TEST))
            buckets.setdefault("Tests", []).append(path)

        for path, reason in self._files_matching(terms, kinds={FileKind.DOCUMENTATION})[:6]:
            evidence.add(self._cite_file(path, reason))
            buckets.setdefault("Documentation", []).append(path)

        for task in self._tasks_matching(terms)[:5]:
            evidence.add(
                Citation(
                    kind=CitationKind.TASK,
                    reference=str(task["id"]),
                    label=str(task.get("title", "")),
                    because="task text matches",
                )
            )
            buckets.setdefault("Tasks", []).append(f"{task['id']} · {task.get('title', '')}")

        if not buckets:
            return Answer(
                question, Intent.EVERYTHING_ABOUT, "", "Nothing in the project matches.", evidence
            )

        finding = "\n\n".join(
            f"{name}:\n" + "\n".join(f"  {line}" for line in lines[:8])
            for name, lines in buckets.items()
        )
        return Answer(question, Intent.EVERYTHING_ABOUT, "", finding, evidence)

    def _why_blocked(self, question: str, terms: list[str]) -> Answer:
        evidence = Evidence()
        refs = [m.group(1).upper() for m in _TASK_REF.finditer(question)]
        candidates = [t for t in self._tasks if str(t.get("id")) in refs] or [
            t for t in self._tasks if str(t.get("status")) == "blocked"
        ]

        if not candidates:
            return Answer(
                question, Intent.WHY_BLOCKED, "", "No task matches, and none is blocked.", evidence
            )

        lines: list[str] = []
        for task in candidates[:5]:
            task_id = str(task["id"])
            status = str(task.get("status", ""))
            reason = str(task.get("blocked_reason") or "").strip()
            evidence.add(
                Citation(
                    kind=CitationKind.TASK,
                    reference=task_id,
                    label=str(task.get("title", "")),
                    because=f"status is {status}",
                )
            )
            if status != "blocked":
                # Answer the question that was asked, not the one that fits.
                lines.append(f"{task_id} is not blocked — it is {status}.")
            else:
                lines.append(f"{task_id} is blocked: {reason or 'no reason recorded'}")

            for node, edge in self._graph.related(f"task:{task_id}", depth=1, limit=6):
                evidence.add(self._cite_node(node, edge.because))

        return Answer(question, Intent.WHY_BLOCKED, "", "\n".join(lines), evidence)

    def _what_changed(self, question: str, terms: list[str]) -> Answer:
        evidence = Evidence()
        window = _window_of(question)
        commits = self._commits(limit=25, since=window)

        for sha, subject in commits:
            evidence.add(
                Citation(
                    kind=CitationKind.COMMIT,
                    reference=sha,
                    label=subject,
                    because=f"commit since {window}" if window else "recent commit",
                )
            )

        if not commits:
            return Answer(
                question,
                Intent.WHAT_CHANGED,
                "",
                f"No commits{f' since {window}' if window else ''}.",
                evidence,
            )
        finding = f"{len(commits)} commit(s){f' since {window}' if window else ''}:\n" + "\n".join(
            f"  {sha} {subject}" for sha, subject in commits
        )
        return Answer(question, Intent.WHAT_CHANGED, "", finding, evidence)

    def _where_documented(self, question: str, terms: list[str]) -> Answer:
        evidence = Evidence()
        docs = self._files_matching(terms, kinds={FileKind.DOCUMENTATION, FileKind.DECISION})
        for path, reason in docs[:10]:
            evidence.add(self._cite_file(path, reason))

        if not docs:
            return Answer(
                question,
                Intent.WHERE_DOCUMENTED,
                "",
                f"No documentation mentions {' '.join(terms) or 'that'}.",
                evidence,
            )
        finding = "Documented in:\n" + "\n".join(f"  {p}" for p, _ in docs[:10])
        return Answer(question, Intent.WHERE_DOCUMENTED, "", finding, evidence)

    def _general(self, question: str, terms: list[str]) -> Answer:
        """
        The fallback: everything that mentions the terms, ranked.

        Deliberately not an error. A question that does not match a pattern still
        deserves the project's own material, and the evidence trail makes clear
        that this was a broad match rather than a targeted one.
        """
        evidence = Evidence()
        for symbol in [s for t in terms[:2] for s in self._index.search_symbols(t, limit=4)][:6]:
            evidence.add(
                Citation(
                    kind=CitationKind.SYMBOL,
                    reference=symbol.qualified,
                    label=f"{symbol.kind.value} {symbol.qualified}",
                    path=symbol.path,
                    line=symbol.line,
                    end_line=symbol.end_line,
                    because=f"defines {symbol.name}",
                )
            )
        for path, reason in self._files_matching(terms)[:8]:
            evidence.add(self._cite_file(path, reason))

        if evidence.empty:
            return Answer(
                question, Intent.GENERAL, "", "Nothing in the project index matches.", evidence
            )
        finding = "Matches:\n" + "\n".join(f"  {c.display()}" for c in evidence.citations[:10])
        return Answer(question, Intent.GENERAL, "", finding, evidence)

    # --------------------------------------------------------------- helpers

    def _files_matching(
        self,
        terms: list[str],
        kinds: set[FileKind] | None = None,
        exclude: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """
        Files containing the terms, ranked by how many they contain.

        Ranking is a count, not a score: "matches 3 of 3 terms" is a fact a
        reader can check against the file, which a cosine distance is not.
        """
        if not terms:
            return []
        excluded = exclude or set()
        hits: dict[str, int] = {}
        for term in terms[:5]:
            for path in self._index.files_with(term):
                if path in excluded:
                    continue
                entry = self._index.files.get(path)
                if kinds and (entry is None or entry.kind not in kinds):
                    continue
                hits[path] = hits.get(path, 0) + 1

        ordered = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
        used = min(len(terms), 5)
        return [(path, f"matches {count}/{used} term(s)") for path, count in ordered]

    def _decisions_matching(self, question: str, terms: list[str]) -> list[tuple[Node, str]]:
        """ADR nodes matching an explicit id, else matching the subject terms."""
        nodes = self._graph.of_kind(NodeKind.DECISION)
        explicit = {m.group(1).upper() for m in _ADR_REF.finditer(question)}
        if explicit:
            return [(n, "named in the question") for n in nodes if _adr_id(n.label) in explicit]

        found = []
        for node in nodes:
            label = node.label.lower()
            matched = [t for t in terms if _mentions(label, t)]
            if matched:
                found.append((node, f"decision title mentions {', '.join(matched)}"))
        return found[:6]

    def _tasks_matching(self, terms: list[str]) -> list[dict[str, Any]]:
        if not terms:
            return []
        out = []
        for task in self._tasks:
            blob = f"{task.get('title', '')} {task.get('objective', '')}".lower()
            if any(t in blob for t in terms[:3]):
                out.append(task)
        return out

    def _commits(self, limit: int, since: str = "") -> list[tuple[str, str]]:
        args = ["log", f"-{limit}", "--format=%h%x1f%s"]
        if since:
            args.insert(1, f"--since={since}")
        out = _git(self._index.root, *args)
        commits: list[tuple[str, str]] = []
        for line in out.splitlines():
            sha, _, subject = line.partition("\x1f")
            if sha:
                commits.append((sha.strip(), subject.strip()))
        return commits

    def _cite_file(
        self, path: str, because: str, kind: CitationKind = CitationKind.FILE
    ) -> Citation:
        entry = self._index.files.get(path)
        actual = kind
        if entry and entry.kind is FileKind.TEST:
            actual = CitationKind.TEST
        return Citation(kind=actual, reference=path, label=path, path=path, because=because)

    def _cite_node(self, node: Node, because: str) -> Citation:
        mapping = {
            NodeKind.TASK: CitationKind.TASK,
            NodeKind.DECISION: CitationKind.DECISION,
            NodeKind.FILE: CitationKind.FILE,
            NodeKind.TEST: CitationKind.TEST,
            NodeKind.COMMIT: CitationKind.COMMIT,
            NodeKind.PULL_REQUEST: CitationKind.PULL_REQUEST,
            NodeKind.KNOWLEDGE: CitationKind.KNOWLEDGE,
            NodeKind.SYMBOL: CitationKind.SYMBOL,
        }
        return Citation(
            kind=mapping.get(node.kind, CitationKind.FILE),
            reference=node.id.split(":", 1)[-1],
            label=node.label,
            path=node.path,
            line=node.line,
            because=because,
        )


def _points_back(question: str) -> bool:
    """Whether a question refers to something already under discussion."""
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", question)}
    return bool(words & _BACK_REFERENCE)


def _mentions(haystack: str, term: str) -> bool:
    """
    Whether a term appears, tolerating a trailing plural.

    "providers" should reach a decision titled "Multi-Provider Model
    Abstraction". Deliberately only the trailing `s`: a real stemmer would start
    conflating words this codebase distinguishes (`state`/`stats`,
    `test`/`tests` are fine, but `bus`/`bu` is not).
    """
    if term in haystack:
        return True
    return len(term) > 3 and term.endswith("s") and term[:-1] in haystack


def _adr_id(label: str) -> str:
    match = re.match(r"(ADR-\d{3,})", label)
    return match.group(1).upper() if match else ""


def _window_of(question: str) -> str:
    """A git --since window named in the question, if any."""
    lowered = question.lower()
    for phrase, window in (
        ("yesterday", "yesterday"),
        ("today", "midnight"),
        ("this week", "1 week ago"),
        ("last week", "2 weeks ago"),
        ("this month", "1 month ago"),
    ):
        if phrase in lowered:
            return window
    return ""


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""
