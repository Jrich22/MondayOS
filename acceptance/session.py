"""
One project's run through the journey.

The pipeline under test is the real one: `Monday.workspace("send-message", ...)`,
the same call the dashboard makes. Nothing here re-implements routing, retrieval
or reasoning -- if it did, the benchmark would be testing a copy of MondayOS
rather than MondayOS.

Isolation is by construction. Each run gets a temporary `project_root` holding a
copy of the project registry, whose `source_path` entries are absolute, so
projects resolve to the real corpora while conversations are written into the
temporary tree. The user's own `workspace/conversations/` is never touched, and
no corpus repository is written to at all.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acceptance.journeys import COLD_TURN, DETERMINISM_TURN, JOURNEY, STALE_TURN, Turn
from acceptance.observe import (
    check_citations,
    cited_commits,
    cited_decisions,
    named_initiatives,
    quoted_scores,
)
from acceptance.pacing import Incident, Outcome, Pacing, classify

# Fields a persisted conversation may legitimately contain. Anything else in a
# strategy record is hidden reasoning wearing a struct, which is the failure
# gate 12 exists to catch.
_ALLOWED_STRATEGY_KEYS = frozenset(
    {
        "question",
        "topic",
        "source_message_id",
        "snapshot_id",
        "fingerprint",
        "project",
        "created_at",
        "recommendation_key",
        "recommendation",
        "rationale",
        "initiative_slug",
        "effort",
        "alternatives",
        "evidence_refs",
        "initiative_slugs",
        "evidence_strength",
        "confidence",
        "execution_risk",
    }
)


@dataclass
class TurnRecord:
    """Everything measured about one turn. All of it mechanical."""

    project: str
    turn_id: str
    question: str
    expect_register: str
    observed_register: str = ""
    mode_reason: str = ""
    outcome: str = Outcome.OK.value
    error: str = ""
    attempts: int = 1
    answer_chars: int = 0
    answer_excerpt: str = ""
    tokens_used: int = 0
    incomplete: bool = False
    stop_reason: str = ""
    latency_ms: int = 0
    recommendation_key: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    # Every score the assessment computed, not only the winning recommendation's.
    # A quote matching any of these is a recitation, not an invention (RC1/H-7).
    computed_values: list[float] = field(default_factory=list)
    quoted_scores: dict[str, float] = field(default_factory=dict)
    citations: dict[str, Any] = field(default_factory=dict)
    initiatives: dict[str, list[str]] = field(default_factory=dict)
    cited_decisions: list[str] = field(default_factory=list)
    invented_decisions: list[str] = field(default_factory=list)
    # Commit references the answer made, and the subset that do not belong to
    # this project. Populated from the project's own git log -- this field was
    # declared and read by gate 9 while no code ever wrote to it, so the gate
    # could not fail (RC1/H-1).
    cited_commits: list[str] = field(default_factory=list)
    foreign_history: list[str] = field(default_factory=list)
    # True when the turn explicitly asked for a fresh assessment, so a changed
    # recommendation is expected rather than a violation.
    reassessed: bool = False
    skipped: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn_id,
            "question": self.question,
            "register": {
                "expected": self.expect_register,
                "observed": self.observed_register,
                "reason": self.mode_reason,
            },
            "reply": {
                "chars": self.answer_chars,
                "tokens_used": self.tokens_used,
                "incomplete": self.incomplete,
                "stop_reason": self.stop_reason,
                "error": self.error[:300],
                "outcome": self.outcome,
                "attempts": self.attempts,
            },
            "recommendation_key": self.recommendation_key,
            "scores": self.scores,
            "computed_values": self.computed_values,
            "quoted_scores": self.quoted_scores,
            "citations": self.citations,
            "initiatives": self.initiatives,
            "cited_decisions": self.cited_decisions,
            "invented_decisions": self.invented_decisions,
            "cited_commits": self.cited_commits,
            "foreign_history": self.foreign_history,
            "reassessed": self.reassessed,
            "latency_ms": self.latency_ms,
            "skipped": self.skipped,
            "excerpt": self.answer_excerpt,
        }


def isolated_root(registry: Path, into: Path) -> Path:
    """
    A MondayOS root that resolves the real projects but stores nothing in them.

    The registry's `source_path` entries are absolute, so copying it is enough:
    projects point at the real corpora while every conversation this run creates
    is written under `into`.
    """
    into.mkdir(parents=True, exist_ok=True)
    (into / "config").mkdir(exist_ok=True)
    shutil.copy2(registry, into / "config" / "projects.json")
    return into


class ProjectSession:
    """Runs the journey for one project and records what came back."""

    def __init__(
        self,
        monday: Any,
        project: str,
        corpus_root: Path,
        monday_root: Path,
        boundaries: frozenset[str],
        discovered: list[str],
        own_decisions: list[str],
        own_commits: frozenset[str],
        pacing: Pacing,
        clock: Any = time.perf_counter,
    ) -> None:
        self._monday = monday
        self.project = project
        self._root = corpus_root
        # Where this run's conversations are written -- a temporary tree, passed
        # in rather than read off the Monday instance so the session never
        # reaches into another object's internals to find out where it is.
        self._monday_root = monday_root
        self._boundaries = boundaries
        self._discovered = discovered
        self._own_decisions = {d.upper() for d in own_decisions}
        # Abbreviated SHAs from this project's own history, scoped by RepoScope.
        # A cited commit outside this set is another project's work.
        self._own_commits = own_commits
        self._pacing = pacing
        self._clock = clock
        self.turns: list[TurnRecord] = []
        self.incidents: list[Incident] = []
        self.strategy_keys_seen: set[str] = set()
        # Whether a completed turn ever caused MondayOS to persist a decision.
        # Gate 8 and gate 12 are meaningless without one, and saying so is the
        # difference between "we looked and found nothing" and "we never looked".
        self.strategy_persisted = False

    # ------------------------------------------------------------------ send

    def _send(self, conversation_id: str, turn: Turn, question: str) -> TurnRecord:
        """One turn, retried only for provider trouble."""
        record = TurnRecord(
            project=self.project,
            turn_id=turn.id,
            question=question,
            expect_register=turn.expect_register,
        )
        started = self._clock()
        payload: dict[str, Any] | None = None

        for attempt in range(self._pacing.attempts):
            record.attempts = attempt + 1
            try:
                response = self._monday.workspace(
                    "send-message",
                    project=self.project,
                    conversation_id=conversation_id,
                    content=question,
                )
            except Exception as exc:  # noqa: BLE001 — a product crash must be recorded, not raised
                record.outcome = Outcome.PRODUCT.value
                record.error = f"{type(exc).__name__}: {exc}"
                break

            payload = getattr(response, "data", None) or {}
            message = payload.get("assistant_message") or {}
            error = str(message.get("error", "") or "")
            outcome = classify(error)
            record.outcome = outcome.value
            record.error = error
            if outcome is not Outcome.PROVIDER_TRANSIENT:
                break
            if attempt + 1 < self._pacing.attempts:
                self._pacing.wait_before_retry(attempt)

        record.latency_ms = int((self._clock() - started) * 1000)
        if payload:
            self._measure(record, payload)
        if record.outcome in (Outcome.PROVIDER_TRANSIENT.value, Outcome.PROVIDER_FATAL.value):
            self.incidents.append(
                Incident(self.project, turn.id, record.outcome, record.error, record.attempts)
            )
        return record

    def _measure(self, record: TurnRecord, payload: dict[str, Any]) -> None:
        message = payload.get("assistant_message") or {}
        answer = str(message.get("content", "") or "")
        record.answer_chars = len(answer)
        record.answer_excerpt = _excerpt(answer)
        record.tokens_used = int(message.get("tokens_used", 0) or 0)
        record.incomplete = bool(message.get("incomplete", False))

        observed = payload.get("assessment") or {}
        record.stop_reason = str((observed.get("metadata") or {}).get("stop_reason", "") or "")
        # The register as the product decided it, not as this harness would.
        mode = str(observed.get("mode", "") or "")
        record.observed_register = "continuation" if observed.get("continuation") else mode
        record.mode_reason = str(observed.get("mode_reason", "") or "")
        record.recommendation_key = str(observed.get("recommendation_key", "") or "")
        record.scores = {
            name: float(observed.get(name, 0.0) or 0.0)
            for name in ("evidence_strength", "confidence", "execution_risk")
            if observed.get(name) is not None
        }

        record.computed_values = [float(v) for v in observed.get("computed_values") or []]
        record.citations = check_citations(answer, self._root, self._boundaries).to_dict()
        record.initiatives = named_initiatives(answer, self._discovered)
        record.quoted_scores = quoted_scores(answer)
        record.cited_decisions = cited_decisions(answer)
        record.invented_decisions = [
            adr for adr in record.cited_decisions if adr.upper() not in self._own_decisions
        ]
        # History the answer named, checked against this project's own log. A
        # candidate that matches no commit anywhere is prose that happens to look
        # like a SHA, not evidence of a leak; only a commit belonging to a
        # *different* project counts.
        record.cited_commits = [
            sha for sha in cited_commits(answer) if _looks_like_history(answer, sha)
        ]
        record.foreign_history = [
            sha for sha in record.cited_commits if not self._is_own_commit(sha)
        ]

        # RC1/H-6. Strategic state is read from the conversation *record*, not
        # from the API response. `Conversation.to_dict()` emits no `strategy`
        # key, so the previous read returned {} on every turn no matter what
        # MondayOS did -- and four gates went unexercised because of it while the
        # product was persisting correctly all along (`workspace/store.py`).
        conversation = payload.get("conversation") or {}
        strategy = self._persisted_strategy(str(conversation.get("id", "")))
        if strategy.get("recommendation_key"):
            # MondayOS persists a decision only when the turn completed, so this
            # is the signal that a user-visible recommendation actually exists.
            self.strategy_persisted = True
            self.strategy_keys_seen |= set(strategy)

    def _persisted_strategy(self, conversation_id: str) -> dict[str, Any]:
        """
        The strategic state MondayOS actually wrote, read through its own store.

        Two wrong assumptions preceded this. The first read
        `payload["conversation"]["strategy"]`, and the API response carries no
        such key. The second read the record directly but globbed for `*.json`,
        and conversations are Markdown with YAML frontmatter (ADR-003) -- so it
        found nothing either, and the fix looked like it had worked while
        changing nothing.

        Using `ConversationStore` removes the guesswork: the product's own reader
        knows where records live and how they are shaped, and a future change to
        either cannot silently break this again.
        """
        if not conversation_id:
            return {}
        try:
            conversation = self._store().get(self.project, conversation_id)
        except Exception:  # noqa: BLE001 — a missing record is an answer, not a crash
            return {}
        strategy = getattr(conversation, "strategy", None)
        if strategy is None:
            return {}
        return dict(strategy.to_dict())

    def _store(self) -> Any:
        from workspace.store import ConversationStore

        return ConversationStore(self._monday_root)

    def _is_own_commit(self, sha: str) -> bool:
        """Whether an abbreviated SHA belongs to this project's own history."""
        return any(own.startswith(sha) or sha.startswith(own) for own in self._own_commits)

    # --------------------------------------------------------------- journey

    def run(self, nouns: dict[str, str]) -> dict[str, Any]:
        """The full journey, in order, plus the cold, stale and repeat cases."""
        main = self._monday.workspace(
            "create-conversation", project=self.project, title="acceptance"
        )
        conversation_id = (getattr(main, "data", {}) or {}).get("id", "")

        anchor = ""
        for turn in JOURNEY:
            question = turn.question.format(**nouns) if "{" in turn.question else turn.question
            if turn.requires_prior_strategy and not anchor:
                self.turns.append(
                    TurnRecord(
                        project=self.project,
                        turn_id=turn.id,
                        question=question,
                        expect_register=turn.expect_register,
                        skipped="no recommendation was produced by the strategic turn",
                    )
                )
                continue
            record = self._send(conversation_id, turn, question)
            self.turns.append(record)
            # RC1/ACC-004. The anchor comes from a turn the *user saw*, never
            # from a recommendation computed before generation. MondayOS persists
            # strategic state only for a completed turn, so anchoring on an
            # unfinished one made the harness expect a continuation the product
            # had correctly refused to offer -- and reported that refusal as a
            # product failure.
            if turn.id == "B.next" and self.strategy_persisted and record.outcome == "ok":
                anchor = record.recommendation_key
            self._pacing.pause(record.observed_register in ("executive", "continuation"))

        cold = self._cold_conversation()
        stale = self._stale_scenario(nouns, anchor)
        repeat = self._determinism(anchor)

        hidden = sorted(self.strategy_keys_seen - _ALLOWED_STRATEGY_KEYS)
        completed = all(
            t.outcome == Outcome.OK.value or t.skipped or t.outcome.startswith("provider")
            for t in self.turns
        )
        return {
            "available": True,
            "completed": completed,
            "recommendation_key": anchor,
            "strategy_persisted": self.strategy_persisted,
            "hidden_reasoning_keys": hidden,
            "determinism": repeat,
            "stale_scenario": stale,
            "cold_elaboration": cold,
        }

    def _cold_conversation(self) -> dict[str, Any]:
        """ "Say more" with nothing before it, in a conversation of its own."""
        created = self._monday.workspace(
            "create-conversation", project=self.project, title="acceptance-cold"
        )
        conversation_id = (getattr(created, "data", {}) or {}).get("id", "")
        record = self._send(conversation_id, COLD_TURN, COLD_TURN.question)
        self.turns.append(record)
        self._pacing.pause(False)
        return {"register": record.observed_register, "outcome": record.outcome}

    def _stale_scenario(self, nouns: dict[str, str], anchor: str) -> dict[str, Any]:
        """
        Ask again after the stored decision stops describing the world.

        Nothing on disk is touched. The conversation's own fingerprint is
        rewritten, which is precisely what staleness is -- a decision computed
        against a world that has since moved -- and it is reversible, local to a
        temporary tree, and does not require editing anybody's repository.
        """
        if not anchor:
            return {"exercised": False, "reason": "no recommendation to make stale"}
        created = self._monday.workspace(
            "create-conversation", project=self.project, title="acceptance-stale"
        )
        conversation_id = (getattr(created, "data", {}) or {}).get("id", "")
        seed = self._send(conversation_id, JOURNEY[1], JOURNEY[1].question)
        self.turns.append(seed)
        if not seed.recommendation_key:
            return {"exercised": False, "reason": "the strategic turn produced no decision"}

        moved = self._move_fingerprint(conversation_id)
        self._pacing.pause(True)
        record = self._send(conversation_id, STALE_TURN, STALE_TURN.question)
        self.turns.append(record)
        return {
            "exercised": moved,
            "register": record.observed_register,
            "reason": record.mode_reason,
            "outcome": record.outcome,
        }

    def _move_fingerprint(self, conversation_id: str) -> bool:
        """
        Rewrite the stored fingerprint so the recorded decision reads as stale.

        Nothing on disk in the *project* changes. The conversation's own
        world-state digest is edited, which is exactly what staleness is -- a
        decision computed against a world that has since moved -- and it is
        local to a temporary tree.

        Goes through the store for the same reason the read does: the record is
        Markdown with frontmatter, and a harness that assumed otherwise silently
        never exercised this scenario at all.
        """
        store = self._store()
        try:
            conversation = store.get(self.project, conversation_id)
        except Exception:  # noqa: BLE001
            return False
        strategy = getattr(conversation, "strategy", None)
        if strategy is None or not getattr(strategy, "fingerprint", ""):
            return False
        strategy.fingerprint = "acceptance-moved-world"
        store.save(conversation)
        return True

    def _determinism(self, anchor: str) -> dict[str, tuple[Any, Any]]:
        """
        The same strategic question again, in a fresh conversation.

        Wording may differ -- that is the model's business. Identity may not: an
        unchanged project that recommends something different the second time
        cannot be relied on for anything.
        """
        if not anchor:
            return {}
        created = self._monday.workspace(
            "create-conversation", project=self.project, title="acceptance-repeat"
        )
        conversation_id = (getattr(created, "data", {}) or {}).get("id", "")
        record = self._send(conversation_id, DETERMINISM_TURN, DETERMINISM_TURN.question)
        self.turns.append(record)
        if record.outcome != Outcome.OK.value:
            return {}
        first = next((t for t in self.turns if t.turn_id == "B.next"), None)
        if first is None:
            return {}
        return {
            "recommendation_key": (first.recommendation_key, record.recommendation_key),
            "mode": (first.observed_register, record.observed_register),
            "evidence_strength": (
                first.scores.get("evidence_strength"),
                record.scores.get("evidence_strength"),
            ),
            "confidence": (first.scores.get("confidence"), record.scores.get("confidence")),
            "execution_risk": (
                first.scores.get("execution_risk"),
                record.scores.get("execution_risk"),
            ),
        }


def _looks_like_history(answer: str, sha: str) -> bool:
    """
    Whether a hex token is being used as a commit reference.

    A commit line reads `5c44663 sourcingBOT: define the roadmap` -- the SHA is
    followed by a message. A bare hex string in prose, or one inside a longer
    identifier, is not history. Requiring the shape keeps the gate honest in the
    other direction too: it must be able to fail, but not on a coincidence.
    """
    for match in re.finditer(re.escape(sha), answer or "", re.I):
        after = (answer[match.end() : match.end() + 2] or "").strip()
        before = answer[max(0, match.start() - 1) : match.start()]
        if before and (before.isalnum() or before in "/._-"):
            continue
        if after:
            return True
    return False


def _excerpt(answer: str, limit: int = 700) -> str:
    """Enough of an answer for a human to judge it, without pasting an essay."""
    text = " ".join((answer or "").split())
    return text if len(text) <= limit else f"{text[:limit]}…"
