"""
Tests for the MondayOS AI Workspace.

The security tests are the point of this file. Cross-project leakage and secret
exposure are the two failures that cannot be walked back once a prompt has left
the machine, so they are asserted directly rather than assumed from careful code
(ADR-017).
"""

from __future__ import annotations

import json
import random
import subprocess
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from brain.providers.base import AIProvider, ProviderAvailability, ProviderError, ProviderResponse
from reasoning.models import Mode
from workspace.authority import ProjectAuthority, Resolution, TaskRecord
from workspace.context import ContextEngine, ContextSnapshot
from workspace.context import adapters as ctx_adapters
from workspace.context import budget as ctx_budget
from workspace.context.snapshot import ContextSource
from workspace.errors import (
    ConversationArchivedError,
    ConversationNotFoundError,
    InvalidProjectError,
    MessageNotFoundError,
)
from workspace.models import (
    ArtifactKind,
    ArtifactRef,
    ConversationStatus,
    MessageRole,
    derive_title,
    slugify,
)
from workspace.responder import (
    EvidenceHandle,
    EvidenceSet,
    EvidenceVerdict,
    ProviderWorkspaceResponder,
    WorkspaceReply,
    WorkspaceRequest,
    _normalise_id,
    resolve_handles,
    validate_evidence,
)
from workspace.service import WorkspaceService
from workspace.store import ConversationStore

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class FakeProvider(AIProvider):
    """
    An offline provider implementing the real interface.

    Records every prompt and context it was handed, which is what lets the
    isolation tests assert on what would actually have left the machine.
    """

    def __init__(self, reply: str = "A grounded answer.", fail: str = "") -> None:
        self.calls: list[dict[str, Any]] = []
        self._reply = reply
        self._fail = fail

    @property
    def name(self) -> str:
        return "fake"

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(available=True, provider="fake", model="fake-1", reason="ready")

    def ask(
        self, prompt: str, context: str = "", max_tokens: int = 1024, **kwargs: Any
    ) -> ProviderResponse:
        self.calls.append({"prompt": prompt, "context": context, "max_tokens": max_tokens})
        if self._fail:
            raise ProviderError(self._fail)
        return ProviderResponse(
            content=self._reply, model="fake-1", provider="fake", tokens_used=42
        )

    def plan(
        self, objective: str, context: str = "", max_tokens: int = 2048, **kwargs: Any
    ) -> ProviderResponse:
        return self.ask(objective, context, max_tokens)

    def summarize(self, content: str, max_words: int = 150, **kwargs: Any) -> ProviderResponse:
        return self.ask(content)

    def review(self, content: str, criteria: str = "", **kwargs: Any) -> ProviderResponse:
        return self.ask(content)


class UnavailableProvider(FakeProvider):
    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            available=False, provider="fake", reason="no API key", env_var="FAKE_KEY"
        )


class RecordingResponder:
    """A responder that captures requests without touching a provider."""

    def __init__(self, reply: str = "ok") -> None:
        self.requests: list[WorkspaceRequest] = []
        self._reply = reply

    @property
    def name(self) -> str:
        return "recording"

    def respond(self, request: WorkspaceRequest) -> WorkspaceReply:
        self.requests.append(request)
        return WorkspaceReply(content=self._reply, provider="recording", model="rec-1")


def _project_tree(root: Path, name: str, description: str = "") -> Path:
    """A minimal but realistic project on disk."""
    path = root / name
    (path / "docs").mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text(f"# {name}\n\n{description or name} project.\n")
    (path / "docs" / "DECISIONS.md").write_text(
        f"# {name} decisions\n\n## ADR-001: {name} uses widgets\n\n**Status:** Accepted\n"
    )
    (path / "docs" / "ARCHITECTURE.md").write_text(f"# {name} architecture\n")
    return path


def _engine(root: Path, projects: dict[str, Path], **readers: Any) -> ContextEngine:
    def resolve(name: str) -> tuple[str, Path, str]:
        slug = slugify(name)
        if slug not in projects:
            raise InvalidProjectError(name)
        return slug, projects[slug], f"{slug} description"

    return ContextEngine(resolve_project=resolve, **readers)


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #


class TestModels(unittest.TestCase):
    def test_slugify_refuses_path_traversal(self):
        """
        The slug is the isolation primitive, and traversal is now a *rejected
        name* rather than a sanitized one.

        This previously folded "a/b" into "a-b" and accepted it. That silently
        turned a name describing two path segments into one, which is the
        sanitize-instead-of-reject behaviour ADR-011 forbids. Rejecting is the
        stronger guarantee and matches what Growth already did.
        """
        from core.identity import InvalidSlugError

        for hostile in ("../other", "/etc/passwd", "a/../b", "..\\win"):
            with self.subTest(name=hostile), self.assertRaises(InvalidSlugError):
                slugify(hostile)

    def test_slugify_normalises_case_and_spaces(self):
        self.assertEqual(slugify("Cue App"), "cue-app")
        self.assertEqual(slugify("  SourcingBOT "), "sourcingbot")

    def test_derive_title_truncates_on_a_word_boundary(self):
        title = derive_title("word " * 40)
        self.assertLessEqual(len(title), 61)
        self.assertTrue(title.endswith("…"))
        self.assertNotIn("wor…", title)

    def test_derive_title_falls_back_when_empty(self):
        self.assertEqual(derive_title("   "), "New conversation")

    def test_a_message_has_nowhere_to_store_hidden_reasoning(self):
        """ADR-015: provider-private reasoning is not requested and cannot be stored."""
        from workspace.models import Message

        fields = set(Message("m", MessageRole.USER, "hi", T0).to_dict())
        for forbidden in ("reasoning", "thinking", "chain_of_thought", "raw_response"):
            self.assertNotIn(forbidden, fields)

    def test_artifact_ref_round_trips(self):
        ref = ArtifactRef(kind=ArtifactKind.PULL_REQUEST, reference="#37", label="Growth PR")
        self.assertEqual(ArtifactRef.from_dict(ref.to_dict()), ref)


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #


class TestConversationStore(unittest.TestCase):
    def test_create_and_read_round_trip(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            created = store.create("acme", "First", now=T0)
            loaded = store.get("acme", created.id)
            self.assertEqual(loaded.id, created.id)
            self.assertEqual(loaded.title, "First")
            self.assertEqual(loaded.project, "acme")

    def test_conversations_live_in_a_per_project_directory(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            store.create("alpha", "A", now=T0)
            store.create("beta", "B", now=T0)
            base = Path(tmp) / "workspace" / "conversations"
            self.assertTrue((base / "alpha").is_dir())
            self.assertTrue((base / "beta").is_dir())

    def test_each_project_has_its_own_sequence_counter(self):
        """
        A shared counter would let one project infer another's volume from the
        gaps in its own ids.
        """
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            store.create("alpha", "a1", now=T0)
            store.create("alpha", "a2", now=T0)
            first_beta = store.create("beta", "b1", now=T0)
            # The sequence restarts per project; the suffix differs per record.
            self.assertTrue(first_beta.id.startswith("CONV-0001-"), first_beta.id)

    def test_the_same_id_in_two_projects_returns_two_different_conversations(self):
        """
        Project scoping is load-bearing, not decorative.

        Per-project counters mean the *sequence* restarts in every project, so an
        id's numeric part alone does not identify a conversation: a read that
        forgot its project would have nothing to open. Conversations are
        gitignored runtime records, so the full id also carries a random suffix —
        which is why this constructs the shared-id case explicitly rather than
        relying on two projects happening to produce the same string.
        """
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            alpha = store.create("alpha", "alpha topic", now=T0)
            beta = store.create("beta", "beta topic", now=T0)

            # Same sequence number, different projects, different full ids.
            self.assertEqual(alpha.id.split("-")[1], beta.id.split("-")[1])
            self.assertNotEqual(alpha.id, beta.id)

            self.assertEqual(store.get("alpha", alpha.id).title, "alpha topic")
            self.assertEqual(store.get("beta", beta.id).title, "beta topic")

            # And a project cannot open the other's conversation by id.
            with self.assertRaises(ConversationNotFoundError):
                store.get("beta", alpha.id)

    def test_a_project_cannot_read_another_projects_conversation(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            alpha = store.create("alpha", "secret plan", now=T0)
            with self.assertRaises(ConversationNotFoundError):
                store.get("beta", alpha.id)

    def test_listing_is_scoped_to_one_project(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            store.create("alpha", "a1", now=T0)
            store.create("alpha", "a2", now=T0)
            store.create("beta", "b1", now=T0)
            self.assertEqual(len(store.list("alpha")), 2)
            self.assertEqual(len(store.list("beta")), 1)
            self.assertEqual([c.title for c in store.list("beta")], ["b1"])

    def test_a_traversal_slug_cannot_escape_the_conversations_directory(self):
        """
        A traversal name never reaches the filesystem at all now.

        It used to be sanitized to "escape" and stored; it is refused before a
        path is built, so there is no directory to reason about.
        """
        from core.identity import InvalidSlugError

        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            with self.assertRaises(InvalidSlugError):
                store.create("../escape", "x", now=T0)
            with self.assertRaises(InvalidSlugError):
                store.project_dir("../escape")

    def test_an_unusable_project_name_is_refused(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            with self.assertRaises(ValueError):
                store.project_dir("///")

    def test_messages_survive_a_write_read_cycle_with_provenance(self):
        with TemporaryDirectory() as tmp:
            from workspace.models import Message

            store = ConversationStore(Path(tmp))
            conversation = store.create("acme", "c", now=T0)
            conversation.messages.append(
                Message(
                    id="MSG-0001",
                    role=MessageRole.ASSISTANT,
                    content="An answer.",
                    created_at=T0,
                    provider="fake",
                    model="fake-1",
                    snapshot_id="CTX-abc",
                    tokens_used=11,
                )
            )
            store.save(conversation)

            loaded = store.get("acme", conversation.id)
            message = loaded.messages[0]
            self.assertEqual(message.provider, "fake")
            self.assertEqual(message.model, "fake-1")
            self.assertEqual(message.snapshot_id, "CTX-abc")
            self.assertEqual(message.tokens_used, 11)

    def test_a_malformed_file_does_not_break_the_listing(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            store.create("acme", "good", now=T0)
            (store.project_dir("acme") / "CONV-9999.md").write_text("not a conversation")
            self.assertEqual([c.title for c in store.list("acme")], ["good"])

    def test_message_ids_are_unique_within_a_conversation(self):
        with TemporaryDirectory() as tmp:
            from workspace.models import Message

            store = ConversationStore(Path(tmp))
            conversation = store.create("acme", "c", now=T0)
            ids = []
            for _ in range(5):
                new_id = store.next_message_id(conversation)
                ids.append(new_id)
                conversation.messages.append(
                    Message(id=new_id, role=MessageRole.USER, content="x", created_at=T0)
                )
            self.assertEqual(len(set(ids)), 5)

    def test_archived_conversations_are_hidden_unless_requested(self):
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conversation = store.create("acme", "c", now=T0)
            conversation.status = ConversationStatus.ARCHIVED
            store.save(conversation)
            self.assertEqual(store.list("acme"), [])
            self.assertEqual(len(store.list("acme", include_archived=True)), 1)


# --------------------------------------------------------------------------- #
# context engine
# --------------------------------------------------------------------------- #


class TestContextEngine(unittest.TestCase):
    def test_snapshot_carries_attributed_sources(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _project_tree(root, "alpha")
            snapshot = _engine(root, {"alpha": path}).build("alpha")

            names = [s.name for s in snapshot.sources]
            self.assertEqual(
                names, ["identity", "intelligence", "docs", "tasks", "knowledge", "git"]
            )
            for source in snapshot.sources:
                self.assertTrue(source.origin, f"{source.name} has no recorded origin")

    def test_identity_comes_first_in_the_rendered_context(self):
        """Budget priority: a model that knows the commits but not the project is worse."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _project_tree(root, "alpha")
            rendered = _engine(root, {"alpha": path}).build("alpha").render()
            self.assertLess(rendered.index("Project identity"), rendered.index("Documentation"))

    def test_the_snapshot_is_deterministic_for_unchanged_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _project_tree(root, "alpha")
            engine = ContextEngine(
                resolve_project=lambda n: ("alpha", path, "d"),
                now=lambda: T0,
            )
            first, second = engine.build("alpha"), engine.build("alpha")
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.render(), second.render())

    def test_an_unknown_project_is_refused_loudly(self):
        """Building context for a nonexistent project would answer about nothing."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(InvalidProjectError):
                _engine(root, {}).build("ghost")

    def test_an_adapter_failure_fails_closed(self):
        """A broken subsystem makes context thinner, never wider."""

        def exploding(_slug: str) -> list[dict[str, Any]]:
            raise RuntimeError("task store is on fire")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _project_tree(root, "alpha")
            snapshot = _engine(root, {"alpha": path}, read_tasks=exploding).build("alpha")
            tasks = snapshot.source("tasks")
            assert tasks is not None
            self.assertFalse(tasks.ok)
            self.assertIn("task store is on fire", tasks.error)
            self.assertEqual(tasks.items, [])
            # The snapshot still built — one broken source is not a dead conversation.
            self.assertTrue(snapshot.source("identity"))

    def test_a_missing_project_directory_yields_a_thin_snapshot_not_a_crash(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = _engine(root, {"alpha": root / "does-not-exist"}).build("alpha")
            self.assertEqual(snapshot.source("docs").items, [])  # type: ignore[union-attr]

    def test_adr_titles_and_statuses_are_extracted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _project_tree(root, "alpha")
            snapshot = _engine(root, {"alpha": path}).build("alpha")
            docs = "\n".join(snapshot.source("docs").items)  # type: ignore[union-attr]
            self.assertIn("ADR-001", docs)
            self.assertIn("Accepted", docs)

    def test_truncation_is_visible_rather_than_silent(self):
        source = ContextSource(name="tasks", label="Tasks", items=[f"item-{i}" for i in range(60)])
        result = ctx_budget.apply([source], total_cap=50, source_caps={"tasks": 50})
        kept = result.sources[0]
        self.assertTrue(kept.truncated)
        self.assertIn("truncated", kept.render())

    def test_a_source_that_cannot_fit_is_recorded_as_omitted(self):
        big = ContextSource(name="git", label="Git", items=["x" * 500])
        result = ctx_budget.apply([big], total_cap=10)
        self.assertEqual(result.sources, [])
        self.assertEqual(result.omitted, ["git"])

    def test_budget_truncates_on_whole_items(self):
        """Half a commit message reads as a complete one. Never split an item."""
        source = ContextSource(name="git", label="Git", items=["aaaa", "bbbb", "cccc"])
        result = ctx_budget.apply([source], total_cap=100, source_caps={"git": 9})
        self.assertEqual(result.sources[0].items, ["aaaa", "bbbb"])

    def test_priority_order_is_respected_when_the_budget_binds(self):
        sources = [
            ContextSource(name="git", label="Git", items=["g" * 40]),
            ContextSource(name="identity", label="Id", items=["i" * 40]),
        ]
        result = ctx_budget.apply(sources, total_cap=45)
        self.assertEqual([s.name for s in result.sources], ["identity"])
        self.assertEqual(result.omitted, ["git"])

    def test_snapshot_round_trips_through_a_dict(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _project_tree(root, "alpha")
            original = _engine(root, {"alpha": path}).build("alpha")
            restored = ContextSnapshot.from_dict(original.to_dict())
            self.assertEqual(restored.id, original.id)
            self.assertEqual(restored.render(), original.render())


# --------------------------------------------------------------------------- #
# isolation and secrets — the tests that matter most
# --------------------------------------------------------------------------- #


class TestIsolationAndSecrets(unittest.TestCase):
    def test_a_snapshot_contains_only_its_own_project(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            beta = _project_tree(root, "beta")
            (beta / "docs" / "DECISIONS.md").write_text(
                "## ADR-009: BETA_CONFIDENTIAL acquisition plan\n\n**Status:** Accepted\n"
            )
            snapshot = _engine(root, {"alpha": alpha, "beta": beta}).build("alpha")
            rendered = snapshot.render()
            self.assertNotIn("BETA_CONFIDENTIAL", rendered)
            self.assertNotIn("beta", rendered.lower().replace("alphabeta", ""))

    def test_the_provider_never_receives_another_projects_context(self):
        """The end-to-end assertion: what actually leaves the machine."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            beta = _project_tree(root, "beta")
            (beta / "README.md").write_text("# beta\n\nBETA_TRADE_SECRET lives here.\n")

            provider = FakeProvider()
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha, "beta": beta}),
                responder=ProviderWorkspaceResponder(provider),
            )
            conversation = service.create_conversation("alpha", "a")
            service.send_message("alpha", conversation["id"], "what is this project?")

            everything = json.dumps(provider.calls)
            self.assertNotIn("BETA_TRADE_SECRET", everything)

    def test_conversation_history_does_not_cross_projects(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            beta = _project_tree(root, "beta")
            provider = FakeProvider()
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha, "beta": beta}),
                responder=ProviderWorkspaceResponder(provider),
            )
            a = service.create_conversation("alpha", "a")
            service.send_message("alpha", a["id"], "ALPHA_ONLY_PHRASE")

            b = service.create_conversation("beta", "b")
            provider.calls.clear()
            service.send_message("beta", b["id"], "and here?")

            self.assertNotIn("ALPHA_ONLY_PHRASE", json.dumps(provider.calls))

    def test_env_files_are_never_read_into_context(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            (alpha / ".env").write_text("OPENAI_API_KEY=sk-live-must-never-appear\n")
            (alpha / "docs" / ".env.production").write_text("SECRET=nope\n")

            snapshot = _engine(root, {"alpha": alpha}).build("alpha")
            rendered = snapshot.render()
            self.assertNotIn("sk-live-must-never-appear", rendered)
            self.assertNotIn(".env", rendered)

    def test_secret_named_files_are_recognised(self):
        for name in (".env", ".env.local", "id_rsa", "server.pem", "aws.credentials", "my.key"):
            self.assertTrue(ctx_adapters.is_secret_path(Path("/x") / name), name)
        for name in ("README.md", "ARCHITECTURE.md", "main.py"):
            self.assertFalse(ctx_adapters.is_secret_path(Path("/x") / name), name)

    def test_a_token_shaped_value_is_redacted_from_a_source(self):
        """Defence in depth: the adapter should not read it; redaction assumes it did."""

        def leaky(_slug: str) -> list[dict[str, Any]]:
            return [{"id": "K-1", "title": "key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA", "type": "x"}]

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            snapshot = _engine(root, {"alpha": alpha}, read_knowledge=leaky).build("alpha")
            self.assertNotIn("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA", snapshot.render())

    def test_an_adapter_error_message_is_redacted(self):
        def exploding(_slug: str) -> list[dict[str, Any]]:
            raise RuntimeError("failed using sk-ant-api03-BBBBBBBBBBBBBBBBBBBBBB")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            snapshot = _engine(root, {"alpha": alpha}, read_tasks=exploding).build("alpha")
            self.assertNotIn("sk-ant-api03-BBBBBBBBBBBBBBBBBBBBBB", json.dumps(snapshot.to_dict()))

    def test_git_state_is_scoped_to_the_project_not_the_parent_repo(self):
        """Reporting the wrong repository's branch is a confident, plausible, wrong answer."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            source = ctx_adapters.git_source("alpha", alpha)
            # Not a repository and not inside one: an empty source, never the
            # test runner's own repository state.
            self.assertEqual(source.items, [])
            self.assertTrue(source.ok)


# --------------------------------------------------------------------------- #
# responder seam
# --------------------------------------------------------------------------- #


class TestResponderSeam(unittest.TestCase):
    def test_the_request_carries_structure_not_a_rendered_prompt(self):
        """A router handed only a finished string has nothing left to route on."""
        fields = set(WorkspaceRequest(project="a", message="m").__dict__)
        for needed in ("project", "message", "snapshot", "history"):
            self.assertIn(needed, fields)

    def test_provider_responder_reports_provenance(self):
        reply = ProviderWorkspaceResponder(FakeProvider()).respond(
            WorkspaceRequest(project="a", message="hello")
        )
        self.assertTrue(reply.ok)
        self.assertEqual(reply.provider, "fake")
        self.assertEqual(reply.model, "fake-1")
        self.assertEqual(reply.tokens_used, 42)

    def test_an_unavailable_provider_returns_an_error_not_an_exception(self):
        reply = ProviderWorkspaceResponder(UnavailableProvider()).respond(
            WorkspaceRequest(project="a", message="hello")
        )
        self.assertFalse(reply.ok)
        self.assertIn("no API key", reply.error)

    def test_a_provider_error_becomes_a_recorded_failure(self):
        reply = ProviderWorkspaceResponder(FakeProvider(fail="rate limited")).respond(
            WorkspaceRequest(project="a", message="hello")
        )
        self.assertFalse(reply.ok)
        self.assertIn("rate limited", reply.error)

    def test_an_empty_response_is_a_failure_not_an_answer(self):
        """A blank bubble would let the conversation continue as if something was said."""
        reply = ProviderWorkspaceResponder(FakeProvider(reply="   ")).respond(
            WorkspaceRequest(project="a", message="hello")
        )
        self.assertFalse(reply.ok)
        self.assertIn("empty", reply.error.lower())

    def test_the_reply_has_nowhere_to_carry_provider_reasoning(self):
        self.assertNotIn("reasoning", WorkspaceReply(content="x").__dict__)

    def test_history_is_bounded(self):
        from workspace.models import Message

        history = [
            Message(id=f"MSG-{i}", role=MessageRole.USER, content=f"turn-{i}", created_at=T0)
            for i in range(40)
        ]
        rendered = WorkspaceRequest(project="a", message="m", history=history).render_context(
            history_turns=5
        )
        self.assertIn("turn-39", rendered)
        self.assertNotIn("turn-10", rendered)

    def test_event_messages_are_not_replayed_as_dialogue(self):
        from workspace.models import Message

        history = [
            Message(id="MSG-1", role=MessageRole.USER, content="real question", created_at=T0),
            Message(id="MSG-2", role=MessageRole.EVENT, content="EVENT_NOISE", created_at=T0),
        ]
        rendered = WorkspaceRequest(project="a", message="m", history=history).render_context()
        self.assertIn("real question", rendered)
        self.assertNotIn("EVENT_NOISE", rendered)

    def test_a_custom_responder_satisfies_the_protocol_without_a_provider(self):
        """The seam is real: increment 4 adds a class, not a rewrite."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            responder = RecordingResponder("routed answer")
            service = WorkspaceService(
                root=root, engine=_engine(root, {"alpha": alpha}), responder=responder
            )
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "hi")
            self.assertEqual(result["assistant_message"]["content"], "routed answer")
            self.assertEqual(result["assistant_message"]["provider"], "recording")


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #


class TestWorkspaceService(unittest.TestCase):
    def _service(self, tmp: str, provider: AIProvider | None = None, **kw: Any):
        root = Path(tmp)
        alpha = _project_tree(root, "alpha")
        beta = _project_tree(root, "beta")
        responder = ProviderWorkspaceResponder(provider) if provider else None
        service = WorkspaceService(
            root=root,
            engine=_engine(root, {"alpha": alpha, "beta": beta}),
            responder=responder,
            **kw,
        )
        return root, service

    def test_send_message_persists_both_turns(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider("Here is the answer."))
            conversation = service.create_conversation("alpha", "")
            result = service.send_message("alpha", conversation["id"], "What are we building?")

            messages = result["conversation"]["messages"]
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[1]["role"], "assistant")
            self.assertEqual(messages[1]["content"], "Here is the answer.")

    def test_the_title_is_derived_from_the_first_user_message(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            conversation = service.create_conversation("alpha", "")
            result = service.send_message("alpha", conversation["id"], "What are we building?")
            self.assertEqual(result["conversation"]["title"], "What are we building?")

    def test_a_message_records_the_snapshot_it_was_answered_against(self):
        """ADR-016: explain an old answer against the context that produced it."""
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "hi")
            snapshot_id = result["context"]["id"]
            self.assertEqual(result["assistant_message"]["snapshot_id"], snapshot_id)
            self.assertEqual(result["conversation"]["active_snapshot_id"], snapshot_id)

    def test_a_provider_failure_preserves_the_user_message(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider(fail="upstream down"))
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "important question")

            messages = result["conversation"]["messages"]
            self.assertEqual(messages[0]["content"], "important question")
            self.assertTrue(messages[1]["error"])
            self.assertIn("upstream down", messages[1]["error"])

    def test_retry_replaces_the_failed_turn_rather_than_appending(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            provider = FakeProvider(fail="down")
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha}),
                responder=ProviderWorkspaceResponder(provider),
            )
            conversation = service.create_conversation("alpha", "c")
            service.send_message("alpha", conversation["id"], "q")

            provider._fail = ""  # provider recovers
            result = service.retry_message("alpha", conversation["id"])
            messages = result["conversation"]["messages"]
            self.assertEqual(len(messages), 2)
            self.assertFalse(messages[1]["error"])

    def test_no_configured_responder_records_a_clear_failure(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, None)
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "q")
            self.assertIn("No AI provider is configured", result["assistant_message"]["error"])

    def test_an_empty_message_is_refused(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            conversation = service.create_conversation("alpha", "c")
            with self.assertRaises(ValueError):
                service.send_message("alpha", conversation["id"], "   ")

    def test_archiving_then_sending_is_refused(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            conversation = service.create_conversation("alpha", "c")
            service.archive_conversation("alpha", conversation["id"])
            with self.assertRaises(ConversationArchivedError):
                service.send_message("alpha", conversation["id"], "q")

    def test_archive_is_reversible(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            conversation = service.create_conversation("alpha", "c")
            service.archive_conversation("alpha", conversation["id"])
            restored = service.unarchive_conversation("alpha", conversation["id"])
            self.assertEqual(restored["status"], "active")

    def test_rename_requires_a_title(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            conversation = service.create_conversation("alpha", "c")
            with self.assertRaises(ValueError):
                service.rename_conversation("alpha", conversation["id"], "  ")

    def test_conversations_survive_a_restart(self):
        """A new service over the same root sees everything. Nothing lives in memory."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            first = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha}),
                responder=ProviderWorkspaceResponder(FakeProvider("persisted answer")),
            )
            conversation = first.create_conversation("alpha", "c")
            first.send_message("alpha", conversation["id"], "remember this")

            del first
            second = WorkspaceService(root=root, engine=_engine(root, {"alpha": alpha}))
            loaded = second.get_conversation("alpha", conversation["id"])
            self.assertEqual(len(loaded["messages"]), 2)
            self.assertEqual(loaded["messages"][1]["content"], "persisted answer")

    def test_switching_projects_leaves_the_other_conversation_untouched(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            a = service.create_conversation("alpha", "alpha talk")
            service.send_message("alpha", a["id"], "alpha question")
            b = service.create_conversation("beta", "beta talk")
            service.send_message("beta", b["id"], "beta question")

            alpha_after = service.get_conversation("alpha", a["id"])
            self.assertEqual(len(alpha_after["messages"]), 2)
            self.assertEqual(alpha_after["messages"][0]["content"], "alpha question")
            self.assertEqual(len(service.list_conversations("alpha")), 1)
            self.assertEqual(len(service.list_conversations("beta")), 1)

    def test_delete_removes_the_conversation(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, FakeProvider())
            conversation = service.create_conversation("alpha", "c")
            service.delete_conversation("alpha", conversation["id"])
            with self.assertRaises(ConversationNotFoundError):
                service.get_conversation("alpha", conversation["id"])

    def test_continue_receives_the_persisted_context_automatically(self):
        """
        The foundation for typing "Continue." — no heuristics, just enough
        persisted state for a future intent resolver to work from.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            responder = RecordingResponder()
            service = WorkspaceService(
                root=root, engine=_engine(root, {"alpha": alpha}), responder=responder
            )
            conversation = service.create_conversation("alpha", "c")
            service.send_message("alpha", conversation["id"], "first question")
            service.send_message("alpha", conversation["id"], "Continue.")

            last = responder.requests[-1]
            self.assertEqual(last.project, "alpha")
            self.assertIsNotNone(last.snapshot)
            self.assertTrue(any("first question" in m.content for m in last.history))


# --------------------------------------------------------------------------- #
# knowledge capture
# --------------------------------------------------------------------------- #


class TestKnowledgeCapture(unittest.TestCase):
    def _service(self, tmp: str, captured: list[dict[str, Any]]):
        root = Path(tmp)
        alpha = _project_tree(root, "alpha")

        def capture(payload: dict[str, Any]) -> str:
            captured.append(payload)
            return f"RES-{len(captured):03d}"

        return root, WorkspaceService(
            root=root,
            engine=_engine(root, {"alpha": alpha}),
            responder=ProviderWorkspaceResponder(FakeProvider("A durable conclusion.")),
            capture_knowledge=capture,
        )

    def test_saving_a_message_uses_the_existing_knowledge_system(self):
        with TemporaryDirectory() as tmp:
            captured: list[dict[str, Any]] = []
            _, service = self._service(tmp, captured)
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "q")
            message_id = result["assistant_message"]["id"]

            saved = service.save_message_to_knowledge("alpha", conversation["id"], message_id)
            self.assertEqual(saved["knowledge_id"], "RES-001")
            self.assertEqual(captured[0]["project"], "alpha")
            self.assertEqual(captured[0]["conversation_id"], conversation["id"])
            self.assertEqual(captured[0]["message_id"], message_id)
            self.assertEqual(captured[0]["body"], "A durable conclusion.")

    def test_capture_records_an_event_in_the_transcript(self):
        """Knowledge from a conversation should be traceable from both ends."""
        with TemporaryDirectory() as tmp:
            captured: list[dict[str, Any]] = []
            _, service = self._service(tmp, captured)
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "q")
            service.save_message_to_knowledge(
                "alpha", conversation["id"], result["assistant_message"]["id"]
            )
            loaded = service.get_conversation("alpha", conversation["id"])
            events = [m for m in loaded["messages"] if m["role"] == "event"]
            self.assertEqual(len(events), 1)
            self.assertIn("RES-001", events[0]["content"])

    def test_nothing_is_captured_automatically(self):
        with TemporaryDirectory() as tmp:
            captured: list[dict[str, Any]] = []
            _, service = self._service(tmp, captured)
            conversation = service.create_conversation("alpha", "c")
            service.send_message("alpha", conversation["id"], "q")
            service.send_message("alpha", conversation["id"], "another")
            self.assertEqual(captured, [])

    def test_a_user_message_cannot_be_captured(self):
        with TemporaryDirectory() as tmp:
            captured: list[dict[str, Any]] = []
            _, service = self._service(tmp, captured)
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "q")
            with self.assertRaises(ValueError):
                service.save_message_to_knowledge(
                    "alpha", conversation["id"], result["user_message"]["id"]
                )

    def test_a_failed_message_cannot_be_captured(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha}),
                responder=ProviderWorkspaceResponder(FakeProvider(fail="down")),
                capture_knowledge=lambda p: "RES-001",
            )
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "q")
            with self.assertRaises(ValueError):
                service.save_message_to_knowledge(
                    "alpha", conversation["id"], result["assistant_message"]["id"]
                )

    def test_capture_records_the_snapshot_the_answer_was_grounded_in(self):
        """Provenance must reach the context, not just the conversation."""
        with TemporaryDirectory() as tmp:
            captured: list[dict[str, Any]] = []
            _, service = self._service(tmp, captured)
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "q")
            service.save_message_to_knowledge(
                "alpha", conversation["id"], result["assistant_message"]["id"]
            )
            self.assertEqual(captured[0]["snapshot_id"], result["context"]["id"])

    def test_capture_carries_the_provider_and_model_that_wrote_it(self):
        with TemporaryDirectory() as tmp:
            captured: list[dict[str, Any]] = []
            _, service = self._service(tmp, captured)
            conversation = service.create_conversation("alpha", "c")
            result = service.send_message("alpha", conversation["id"], "q")
            service.save_message_to_knowledge(
                "alpha", conversation["id"], result["assistant_message"]["id"]
            )
            self.assertEqual(captured[0]["provider"], "fake")
            self.assertEqual(captured[0]["model"], "fake-1")

    def test_an_unknown_message_is_refused(self):
        with TemporaryDirectory() as tmp:
            captured: list[dict[str, Any]] = []
            _, service = self._service(tmp, captured)
            conversation = service.create_conversation("alpha", "c")
            with self.assertRaises(MessageNotFoundError):
                service.save_message_to_knowledge("alpha", conversation["id"], "MSG-9999")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# increment 2 — streaming
# --------------------------------------------------------------------------- #


class StreamingProvider(FakeProvider):
    """A provider that genuinely streams, one word per chunk."""

    def __init__(self, text: str = "one two three four five", fail_after: int = -1) -> None:
        super().__init__(text)
        self._words = text.split()
        self._fail_after = fail_after

    @property
    def supports_streaming(self) -> bool:
        return True

    def stream(self, prompt, context="", max_tokens=1024, **kwargs):  # type: ignore[no-untyped-def]
        from brain.providers.base import ProviderChunk

        self.calls.append({"prompt": prompt, "context": context})
        for index, word in enumerate(self._words):
            if index == self._fail_after:
                raise ProviderError("stream died mid-answer")
            yield ProviderChunk(text=word + " ")
        yield ProviderChunk(done=True, model="fake-1", provider="fake", tokens_used=7)


class TestStreaming(unittest.TestCase):
    def _service(self, tmp: str, provider: AIProvider):
        root = Path(tmp)
        alpha = _project_tree(root, "alpha")
        return root, WorkspaceService(
            root=root,
            engine=_engine(root, {"alpha": alpha}),
            responder=ProviderWorkspaceResponder(provider),
        )

    def test_a_full_stream_yields_context_deltas_and_a_done_frame(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, StreamingProvider("alpha beta gamma"))
            conversation = service.create_conversation("alpha", "")
            kinds = []
            deltas = []
            done = None
            for event in service.stream_message("alpha", conversation["id"], "hi"):
                kinds.append(event["type"])
                if event["type"] == "delta":
                    deltas.append(event["text"])
                if event["type"] == "done":
                    done = event
            self.assertEqual(kinds[0], "user")
            self.assertEqual(kinds[1], "context")
            self.assertEqual(len(deltas), 3)
            assert done is not None
            self.assertEqual(done["message"]["content"], "alpha beta gamma")
            self.assertFalse(done["message"]["incomplete"])

    def test_stopping_persists_the_partial_marked_incomplete(self):
        """Partial text presented as a finished answer is a correctness failure."""
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, StreamingProvider("one two three four five"))
            conversation = service.create_conversation("alpha", "")
            stream = service.stream_message("alpha", conversation["id"], "hi")
            seen = 0
            for event in stream:
                if event["type"] == "delta":
                    seen += 1
                    if seen == 2:
                        stream.close()
                        break

            loaded = service.get_conversation("alpha", conversation["id"])
            assistant = loaded["messages"][-1]
            self.assertEqual(assistant["role"], "assistant")
            self.assertTrue(assistant["incomplete"])
            self.assertTrue(assistant["content"])
            self.assertNotEqual(assistant["content"], "one two three four five")

    def test_stopping_preserves_the_user_message(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, StreamingProvider())
            conversation = service.create_conversation("alpha", "")
            stream = service.stream_message("alpha", conversation["id"], "do not lose me")
            next(stream)
            stream.close()
            loaded = service.get_conversation("alpha", conversation["id"])
            self.assertEqual(loaded["messages"][0]["content"], "do not lose me")

    def test_a_mid_stream_failure_keeps_what_arrived(self):
        with TemporaryDirectory() as tmp:
            _, service = self._service(tmp, StreamingProvider("one two three", fail_after=2))
            conversation = service.create_conversation("alpha", "")
            done = None
            for event in service.stream_message("alpha", conversation["id"], "hi"):
                if event["type"] == "done":
                    done = event
            assert done is not None
            self.assertIn("stream died", done["message"]["error"])
            self.assertTrue(done["message"]["incomplete"])
            self.assertIn("one", done["message"]["content"])

    def test_a_stream_with_no_provider_records_a_clear_failure(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            service = WorkspaceService(root=root, engine=_engine(root, {"alpha": alpha}))
            conversation = service.create_conversation("alpha", "")
            done = [
                e
                for e in service.stream_message("alpha", conversation["id"], "hi")
                if e["type"] == "done"
            ]
            self.assertIn("No AI provider is configured", done[0]["message"]["error"])

    def test_a_non_streaming_provider_still_works_through_the_seam(self):
        """Every existing provider satisfies the interface without change."""
        with TemporaryDirectory() as tmp:
            provider = FakeProvider("delivered at once")
            _, service = self._service(tmp, provider)
            self.assertFalse(provider.supports_streaming)
            conversation = service.create_conversation("alpha", "")
            done = [
                e
                for e in service.stream_message("alpha", conversation["id"], "hi")
                if e["type"] == "done"
            ]
            self.assertEqual(done[0]["message"]["content"], "delivered at once")

    def test_streaming_does_not_leak_another_project(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            beta = _project_tree(root, "beta")
            (beta / "README.md").write_text("# beta\n\nBETA_STREAM_SECRET.\n")
            provider = StreamingProvider("ok")
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha, "beta": beta}),
                responder=ProviderWorkspaceResponder(provider),
            )
            conversation = service.create_conversation("alpha", "")
            list(service.stream_message("alpha", conversation["id"], "hi"))
            self.assertNotIn("BETA_STREAM_SECRET", json.dumps(provider.calls))


# --------------------------------------------------------------------------- #
# increment 2 — search, relevance, reuse, compaction, briefing
# --------------------------------------------------------------------------- #


class TestConversationSearch(unittest.TestCase):
    def _service(self, tmp: str):
        root = Path(tmp)
        alpha = _project_tree(root, "alpha")
        beta = _project_tree(root, "beta")
        service = WorkspaceService(
            root=root,
            engine=_engine(root, {"alpha": alpha, "beta": beta}),
            responder=ProviderWorkspaceResponder(FakeProvider("noted")),
        )
        a = service.create_conversation("alpha", "Shortlist design")
        service.send_message("alpha", a["id"], "How does the shortlist promotion work?")
        b = service.create_conversation("beta", "Beta planning")
        service.send_message("beta", b["id"], "BETA_ONLY_TERM planning notes")
        return service

    def test_search_is_scoped_to_one_project_by_default(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            result = service.search_conversations("planning", project="alpha")
            self.assertEqual(result["hits"], [])
            self.assertEqual(result["projects_searched"], ["alpha"])

    def test_search_finds_title_and_body_matches(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            result = service.search_conversations("shortlist", project="alpha")
            self.assertEqual(len(result["hits"]), 1)
            self.assertTrue(result["hits"][0]["matched_title"])
            self.assertTrue(result["hits"][0]["snippets"])

    def test_cross_project_search_is_opt_in_and_names_the_project(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            result = service.search_conversations("BETA_ONLY_TERM", scope="all")
            self.assertEqual(len(result["hits"]), 1)
            self.assertEqual(result["hits"][0]["project"], "beta")

    def test_project_scope_without_a_project_is_refused(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            with self.assertRaises(ValueError):
                service.search_conversations("anything")

    def test_an_unknown_scope_is_refused(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            with self.assertRaises(ValueError):
                service.search_conversations("x", project="alpha", scope="everything")

    def test_every_term_must_match(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            self.assertEqual(
                service.search_conversations("shortlist unicorn", project="alpha")["hits"], []
            )


class TestRelevance(unittest.TestCase):
    def test_matching_items_outrank_non_matching_and_record_why(self):
        from workspace.context import relevance

        ranked = relevance.rank(["about widgets", "about sourcing", "about pipes"], "sourcing")
        self.assertEqual(ranked[0].text, "about sourcing")
        self.assertEqual(ranked[0].reason, relevance.REASON_KEYWORD)
        self.assertEqual(ranked[1].reason, relevance.REASON_BASELINE)

    def test_an_empty_query_preserves_arrival_order(self):
        """No query is no evidence; inventing an order would be worse."""
        from workspace.context import relevance

        items = ["c", "a", "b"]
        self.assertEqual([r.text for r in relevance.rank(items, "")], items)

    def test_a_pinned_item_outranks_a_keyword_match(self):
        from workspace.context import relevance

        ranked = relevance.rank(
            ["TASK-1 [in-progress] unrelated", "TASK-2 [backlog] sourcing"],
            "sourcing",
            priority={0: relevance.REASON_ACTIVE_TASK},
        )
        self.assertEqual(ranked[0].reason, relevance.REASON_ACTIVE_TASK)

    def test_ranking_reasons_survive_the_budget(self):
        """The budget must cut reasons in step with items, never drop them."""
        source = ContextSource(
            name="tasks",
            label="Tasks",
            items=["aaaa", "bbbb", "cccc"],
            reasons=["keyword-match", "baseline", "baseline"],
        )
        result = ctx_budget.apply([source], total_cap=100, source_caps={"tasks": 9})
        kept = result.sources[0]
        self.assertEqual(len(kept.items), len(kept.reasons))
        self.assertEqual(kept.reasons[0], "keyword-match")

    def test_the_snapshot_records_what_it_was_ranked_for(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            snapshot = _engine(root, {"alpha": alpha}).build("alpha", query="widgets")
            self.assertEqual(snapshot.query, "widgets")


class TestSnapshotReuse(unittest.TestCase):
    def _engine_for(self, tmp: str, tasks=None):
        root = Path(tmp)
        alpha = _project_tree(root, "alpha")
        return root, alpha, _engine(root, {"alpha": alpha}, read_tasks=tasks or (lambda s: []))

    def test_an_identical_request_reuses_the_snapshot(self):
        with TemporaryDirectory() as tmp:
            _, _, engine = self._engine_for(tmp)
            first, reused_a = engine.build_or_reuse("alpha", "same question")
            second, reused_b = engine.build_or_reuse("alpha", "same question")
            self.assertFalse(reused_a)
            self.assertTrue(reused_b)
            self.assertEqual(first.id, second.id)

    def test_a_different_request_rebuilds(self):
        """A snapshot ranked for another question may have dropped what this needs."""
        with TemporaryDirectory() as tmp:
            _, _, engine = self._engine_for(tmp)
            engine.build_or_reuse("alpha", "first question")
            _, reused = engine.build_or_reuse("alpha", "completely different")
            self.assertFalse(reused)

    def test_a_changed_task_invalidates(self):
        state = {"status": "backlog"}

        def tasks(_slug):
            return [{"id": "TASK-1", "title": "t", "status": state["status"], "priority": "P2"}]

        with TemporaryDirectory() as tmp:
            _, _, engine = self._engine_for(tmp, tasks)
            engine.build_or_reuse("alpha", "q")
            _, reused = engine.build_or_reuse("alpha", "q")
            self.assertTrue(reused)
            state["status"] = "in-progress"
            _, reused_after = engine.build_or_reuse("alpha", "q")
            self.assertFalse(reused_after)

    def test_changed_docs_invalidate(self):
        with TemporaryDirectory() as tmp:
            _, alpha, engine = self._engine_for(tmp)
            engine.build_or_reuse("alpha", "q")
            import os
            import time

            doc = alpha / "docs" / "ARCHITECTURE.md"
            doc.write_text("# changed\n")
            os.utime(doc, (time.time() + 60, time.time() + 60))
            _, reused = engine.build_or_reuse("alpha", "q")
            self.assertFalse(reused)

    def test_explicit_invalidation_forces_a_rebuild(self):
        with TemporaryDirectory() as tmp:
            _, _, engine = self._engine_for(tmp)
            engine.build_or_reuse("alpha", "q")
            engine.invalidate("alpha")
            _, reused = engine.build_or_reuse("alpha", "q")
            self.assertFalse(reused)


class TestCompaction(unittest.TestCase):
    def _messages(self, count: int) -> list:
        from workspace.models import Message

        return [
            Message(
                id=f"MSG-{i:04d}",
                role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                content=f"turn number {i}",
                created_at=T0,
            )
            for i in range(count)
        ]

    def test_a_short_conversation_is_sent_whole(self):
        from workspace import compaction

        plan = compaction.compact(self._messages(6))
        self.assertFalse(plan.was_compacted)
        self.assertEqual(len(plan.verbatim), 6)

    def test_a_long_conversation_keeps_recent_turns_verbatim(self):
        from workspace import compaction

        plan = compaction.compact(self._messages(50), verbatim_turns=10, threshold=20)
        self.assertTrue(plan.was_compacted)
        self.assertEqual(len(plan.verbatim), 10)
        self.assertIn("turn number 49", plan.verbatim[-1].content)
        self.assertIn("turn number 0", plan.digest)

    def test_compaction_never_mutates_the_stored_transcript(self):
        from workspace import compaction

        messages = self._messages(50)
        before = [m.content for m in messages]
        compaction.compact(messages, verbatim_turns=5, threshold=10)
        self.assertEqual([m.content for m in messages], before)

    def test_a_failing_summarizer_degrades_to_the_deterministic_digest(self):
        from workspace import compaction

        class Broken:
            def summarize(self, messages):
                raise RuntimeError("summariser down")

        plan = compaction.compact(
            self._messages(40), verbatim_turns=5, threshold=10, summarizer=Broken()
        )
        self.assertTrue(plan.digest)
        self.assertIn("condensed", plan.digest)


class TestBriefing(unittest.TestCase):
    def test_next_step_prefers_work_already_in_progress(self):
        from workspace.briefing import choose_next_step

        step = choose_next_step(
            [
                {"id": "TASK-2", "title": "b", "status": "backlog", "priority": "P0"},
                {"id": "TASK-1", "title": "a", "status": "in-progress", "priority": "P2"},
            ]
        )
        assert step is not None
        self.assertEqual(step.task_id, "TASK-1")
        self.assertEqual(step.reason, "already in progress")

    def test_next_step_falls_back_to_highest_priority_backlog(self):
        from workspace.briefing import choose_next_step

        step = choose_next_step(
            [
                {"id": "TASK-2", "title": "b", "status": "backlog", "priority": "P2"},
                {"id": "TASK-3", "title": "c", "status": "backlog", "priority": "P0"},
            ]
        )
        assert step is not None
        self.assertEqual(step.task_id, "TASK-3")

    def test_no_tasks_means_no_recommendation(self):
        """An invented priority is worse than none."""
        from workspace.briefing import choose_next_step

        self.assertIsNone(choose_next_step([]))

    def test_a_briefing_with_nothing_recorded_says_so(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = WorkspaceService(root=root)
            result = service.briefing()
            self.assertFalse(result["can_continue"])
            self.assertIn("No conversations recorded yet.", result["notes"])

    def test_a_briefing_points_at_the_most_recent_conversation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha}),
                responder=ProviderWorkspaceResponder(FakeProvider()),
                read_tasks=lambda s: [
                    {"id": "TASK-9", "title": "Do it", "status": "in-progress", "priority": "P1"}
                ],
                read_completed=lambda s: [
                    {"id": "TASK-8", "title": "Done", "status": "completed", "priority": "P1"}
                ],
            )
            conversation = service.create_conversation("alpha", "")
            service.send_message("alpha", conversation["id"], "the last thing I asked")

            result = service.briefing()
            self.assertTrue(result["can_continue"])
            self.assertEqual(result["project"], "alpha")
            self.assertEqual(result["conversation_id"], conversation["id"])
            self.assertEqual(result["active_task"]["id"], "TASK-9")
            self.assertEqual(result["next_step"]["task_id"], "TASK-9")


class TestActivity(unittest.TestCase):
    def test_real_operations_are_recorded(self):
        from workspace.activity import ActivityRecorder

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            recorder = ActivityRecorder()
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha}),
                responder=ProviderWorkspaceResponder(FakeProvider()),
                activity=recorder,
            )
            conversation = service.create_conversation("alpha", "")
            service.send_message("alpha", conversation["id"], "hi")

            kinds = [e["kind"] for e in recorder.to_dicts()]
            self.assertIn("context", kinds)
            self.assertIn("provider", kinds)

    def test_nothing_is_recorded_when_nothing_happens(self):
        from workspace.activity import ActivityRecorder

        self.assertEqual(ActivityRecorder().to_dicts(), [])

    def test_the_feed_is_bounded(self):
        from workspace.activity import ActivityKind, ActivityRecorder

        recorder = ActivityRecorder(limit=5)
        for i in range(20):
            recorder.record(ActivityKind.CONTEXT, f"event {i}")
        self.assertEqual(len(recorder.events()), 5)
        self.assertEqual(recorder.events()[0].message, "event 19")


# --------------------------------------------------------------------------- #
# increment 3 — project intelligence in the workspace
# --------------------------------------------------------------------------- #


def _indexable_project(root: Path, name: str) -> Path:
    """A project with enough real material to index and retrieve from."""
    path = root / name
    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "docs").mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text(f"# {name}\n\nThe {name} project.\n")
    (path / "src" / "engine.py").write_text(
        '"""Assembles context. Implements ADR-017."""\n\n\nclass ContextEngine:\n'
        '    """Builds snapshots."""\n\n    def build(self, project: str) -> str:\n'
        "        return project\n"
    )
    (path / "src" / "responder.py").write_text(
        "class WorkspaceResponder:\n    def respond(self) -> None:\n        return None\n"
    )
    (path / "docs" / "DECISIONS.md").write_text(
        "# Decisions\n\n## ADR-017: The Context Engine Assembles Snapshots\n\n"
        "**Status:** Accepted\n\nBecause it must be explainable.\n"
    )
    return path


class TestIntelligenceIntegration(unittest.TestCase):
    """
    Project intelligence reaching a conversation.

    The property under test is not "retrieval works" — that is covered in
    tests/test_intelligence.py. It is that retrieval arrives through the *same*
    Context Engine as everything else, so budgeting, attribution, redaction and
    project isolation apply to it without being reimplemented.
    """

    def _engine_with_intelligence(self, root: Path, projects: dict[str, Path]):
        from intelligence import QuestionEngine, build_graph, build_index

        engines: dict[str, QuestionEngine] = {}

        def ask(slug: str, question: str, carry: str = ""):
            if slug not in engines:
                index = build_index(slug, projects[slug], use_cache=False)
                engines[slug] = QuestionEngine(index, build_graph(index), [])
            return engines[slug].ask(question, carry=carry)

        def resolve(name: str) -> tuple[str, Path, str]:
            slug = slugify(name)
            if slug not in projects:
                raise InvalidProjectError(name)
            return slug, projects[slug], f"{slug} description"

        return ContextEngine(resolve_project=resolve, ask_intelligence=ask)

    def _service(self, tmp: str, provider: AIProvider | None = None):
        root = Path(tmp)
        projects = {
            "alpha": _indexable_project(root, "alpha"),
            "beta": _indexable_project(root, "beta"),
        }
        responder = ProviderWorkspaceResponder(provider) if provider else None
        return WorkspaceService(
            root=root,
            engine=self._engine_with_intelligence(root, projects),
            responder=responder,
        )

    def _intelligence(self, snapshot: dict) -> list[str]:
        source = next(s for s in snapshot["sources"] if s["name"] == "intelligence")
        return list(source["items"])

    def test_intelligence_arrives_as_a_context_source(self):
        """
        Not a second route into the prompt.

        Everything the Context Engine assembles is budgeted, attributed and
        redacted in one place. A separate path would be a second place those
        rules have to hold, and eventually one of them would be wrong.
        """
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            snapshot = service.build_context("alpha", query="Where is ContextEngine implemented?")
            names = [s["name"] for s in snapshot["sources"]]
            self.assertIn("intelligence", names)
            # Identity still outranks it: an answer about the wrong project is
            # worse than one with thin evidence.
            self.assertLess(names.index("identity"), names.index("intelligence"))

    def test_retrieved_evidence_names_files_and_lines(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            snapshot = service.build_context("alpha", query="Where is ContextEngine implemented?")
            items = self._intelligence(snapshot)
            self.assertTrue(any("ContextEngine" in i for i in items))
            self.assertTrue(any("engine.py:" in i for i in items))

    def test_evidence_reasons_survive_budgeting(self):
        """The budget must cut reasons in step with items, never drop them."""
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            snapshot = service.build_context("alpha", query="Where is ContextEngine implemented?")
            source = next(s for s in snapshot["sources"] if s["name"] == "intelligence")
            self.assertEqual(len(source["items"]), len(source["reasons"]))
            self.assertEqual(set(source["reasons"]), {"retrieved-evidence"})

    def test_no_intelligence_source_without_a_question(self):
        """Retrieval is per-request; a snapshot with no query has nothing to rank."""
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            snapshot = service.build_context("alpha")
            source = next(s for s in snapshot["sources"] if s["name"] == "intelligence")
            self.assertEqual(source["items"], [])

    def test_a_broken_indexer_leaves_the_snapshot_intact(self):
        """Fail closed: thin context, never a dead conversation."""

        def exploding(slug: str, question: str, carry: str = ""):
            raise RuntimeError("index is on fire")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _indexable_project(root, "alpha")
            engine = ContextEngine(
                resolve_project=lambda n: ("alpha", alpha, "d"), ask_intelligence=exploding
            )
            service = WorkspaceService(root=root, engine=engine)
            snapshot = service.build_context("alpha", query="anything")
            source = next(s for s in snapshot["sources"] if s["name"] == "intelligence")
            self.assertEqual(source["items"], [])
            self.assertIn("on fire", source["error"])
            self.assertTrue(
                next(s for s in snapshot["sources"] if s["name"] == "identity")["items"]
            )

    def test_intelligence_never_reaches_across_projects(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = {
                "alpha": _indexable_project(root, "alpha"),
                "beta": _indexable_project(root, "beta"),
            }
            # Not named "secret.py": the scanner skips credential-shaped
            # filenames, which would make this fixture invisible for the right
            # reason and mask the wrong one.
            (projects["beta"] / "src" / "beta_only.py").write_text("BETA_ONLY_SYMBOL = 1\n")

            service = WorkspaceService(
                root=root, engine=self._engine_with_intelligence(root, projects)
            )
            snapshot = service.build_context("alpha", query="Where is BETA_ONLY_SYMBOL defined?")

            # Assert on the retrieved *sources*, not the whole snapshot: the
            # snapshot records the query verbatim, and the query contains the
            # term because the question did. Searching the recorded question for
            # the question's own words proves nothing.
            retrieved = json.dumps([s["items"] for s in snapshot["sources"]])
            self.assertNotIn("BETA_ONLY_SYMBOL", retrieved)
            self.assertNotIn("beta", retrieved)

            # And the same question against beta does find it — so the absence
            # above is isolation, not a broken indexer.
            beta = service.build_context("beta", query="Where is BETA_ONLY_SYMBOL defined?")
            beta_items = json.dumps([s["items"] for s in beta["sources"]])
            self.assertIn("BETA_ONLY_SYMBOL", beta_items)

    def test_the_provider_receives_the_retrieved_evidence(self):
        with TemporaryDirectory() as tmp:
            provider = FakeProvider("noted")
            service = self._service(tmp, provider)
            conversation = service.create_conversation("alpha", "")
            service.send_message("alpha", conversation["id"], "Where is ContextEngine implemented?")

            context = json.dumps(provider.calls)
            self.assertIn("Project intelligence", context)
            self.assertIn("ContextEngine", context)
            self.assertIn("engine.py", context)


class TestSubjectCarryOver(unittest.TestCase):
    """
    A conversation keeps its subject so a follow-up need not restate it.

    Twenty minutes into discussing the ContextEngine, "find every place it is
    used" means the ContextEngine. The subject is persisted on the conversation,
    so it survives a reload like everything else.
    """

    def _service(self, tmp: str):
        return TestIntelligenceIntegration()._service(tmp, FakeProvider("noted"))

    def _subject(self, service, conversation_id: str) -> str:
        return str(service.get_conversation("alpha", conversation_id)["subject"])

    def test_the_first_question_sets_the_subject(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            conversation = service.create_conversation("alpha", "")
            service.send_message("alpha", conversation["id"], "Where is ContextEngine implemented?")
            self.assertIn("contextengine", self._subject(service, conversation["id"]))

    def test_a_follow_up_keeps_the_subject(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            conversation = service.create_conversation("alpha", "")
            service.send_message("alpha", conversation["id"], "Where is ContextEngine implemented?")
            service.send_message("alpha", conversation["id"], "Why did we design it that way?")
            self.assertIn("contextengine", self._subject(service, conversation["id"]))

    def test_a_back_reference_keeps_the_subject(self):
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            conversation = service.create_conversation("alpha", "")
            service.send_message("alpha", conversation["id"], "Where is ContextEngine implemented?")
            service.send_message("alpha", conversation["id"], "Find every place it is used.")
            self.assertIn("contextengine", self._subject(service, conversation["id"]))

    def test_an_explicit_new_subject_replaces_the_old_one(self):
        """Carry-over must never override what was actually asked."""
        with TemporaryDirectory() as tmp:
            service = self._service(tmp)
            conversation = service.create_conversation("alpha", "")
            service.send_message("alpha", conversation["id"], "Where is ContextEngine implemented?")
            service.send_message("alpha", conversation["id"], "Now explain WorkspaceResponder.")
            subject = self._subject(service, conversation["id"])
            self.assertIn("workspaceresponder", subject)
            self.assertNotIn("contextengine", subject)

    def test_the_subject_survives_a_restart(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._service(tmp)
            conversation = first.create_conversation("alpha", "")
            first.send_message("alpha", conversation["id"], "Where is ContextEngine implemented?")

            del first
            second = WorkspaceService(root=root)
            loaded = second.get_conversation("alpha", conversation["id"])
            self.assertIn("contextengine", loaded["subject"])


class TestObservedAssessment(unittest.TestCase):
    """
    The register a turn was routed to, readable after the fact.

    `Message` records provider, model, tokens and `incomplete`; strategic state
    is written only for executive turns. So a grounded turn left no trace of why
    it was grounded, and anything checking that routing behaved had to re-run the
    router and compare its answer to itself -- measuring a reconstruction rather
    than the execution path.

    This is observational metadata. Nothing branches on it, and a caller that
    ignores the key behaves exactly as before.
    """

    def test_a_turn_reports_the_register_it_was_routed_to(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha}),
                responder=ProviderWorkspaceResponder(FakeProvider()),
            )
            conversation = service.create_conversation("alpha", "a")
            result = service.send_message("alpha", conversation["id"], "what is this project?")

            self.assertIn("assessment", result)
            observed = result["assessment"]
            # Without a reasoning layer wired there is no assessment, which is a
            # real outcome rather than a missing field.
            if observed is not None:
                self.assertIn(observed["mode"], ("grounded", "executive"))
                self.assertIsInstance(observed["continuation"], bool)
                self.assertIsInstance(observed["stale"], bool)

    def test_existing_keys_are_untouched(self):
        """Additive means additive: no caller loses anything."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = _project_tree(root, "alpha")
            service = WorkspaceService(
                root=root,
                engine=_engine(root, {"alpha": alpha}),
                responder=ProviderWorkspaceResponder(FakeProvider()),
            )
            conversation = service.create_conversation("alpha", "a")
            result = service.send_message("alpha", conversation["id"], "hello")
            for key in ("conversation", "user_message", "assistant_message", "context"):
                self.assertIn(key, result)

    def test_it_carries_no_prompt_or_hidden_reasoning(self):
        """
        Same rule as StrategicState: only what a reader could have seen.

        A field here that was never on screen would be hidden reasoning wearing a
        different struct, and it would travel to every API client.
        """
        from workspace.service import observed_assessment

        # `.score` is the real attribute. This stub previously said `.value`,
        # which is exactly the mistake the implementation made -- so the test
        # agreed with the bug and could never have caught it.
        class _Score:
            score = 0.5

        class _Rec:
            statement = "Do the thing"
            initiative_slug = "billing"
            alternatives = ()
            evidence_strength = _Score()
            confidence = _Score()
            execution_risk = _Score()

        class _Assessment:
            mode = Mode.EXECUTIVE
            mode_reason = "matched next-work"
            continuation = False
            stale = False
            stale_because = ""
            obsolete = False
            replaced_stale = False
            has_reasoning = True
            recommendations = [_Rec()]
            initiatives: list[Any] = []

        observed = observed_assessment(_Assessment())
        assert observed is not None
        for banned in ("prompt", "context", "snapshot", "instruction", "system", "reasoning_text"):
            self.assertNotIn(banned, observed)
        self.assertEqual(observed["mode"], "executive")
        self.assertEqual(observed["confidence"], 0.5)

    def test_no_assessment_is_a_real_outcome(self):
        from workspace.service import observed_assessment

        self.assertIsNone(observed_assessment(None))

    def test_the_three_scores_are_the_computed_ones(self):
        """
        RC1/ACC-002. The regression this class did not have.

        `observed_assessment` read each score with `getattr(score, "value", 0.0)`.
        `Confidence` and `Risk` both expose `.score`, so every field silently
        took the default and the key reported three zeros on every turn while the
        real numbers sat one attribute away. The original tests asserted the
        fields *existed* and were the right type -- which they were, and 0.0 is a
        float.
        """
        from reasoning.models import Confidence, Recommendation
        from reasoning.options import Risk
        from workspace.service import observed_assessment

        recommendation = Recommendation(
            statement="Harden the scheduler",
            rationale="because",
            confidence=Confidence(score=0.64),
            evidence_strength=Confidence(score=0.91),
            execution_risk=Risk(score=0.30),
        )

        class _Assessment:
            mode = Mode.EXECUTIVE
            mode_reason = "matched next-work"
            continuation = False
            stale = False
            stale_because = ""
            obsolete = False
            replaced_stale = False
            has_reasoning = True
            recommendations = [recommendation]
            initiatives: list[Any] = []

        observed = observed_assessment(_Assessment())
        assert observed is not None
        self.assertEqual(observed["confidence"], 0.64)
        self.assertEqual(observed["evidence_strength"], 0.91)
        self.assertEqual(observed["execution_risk"], 0.30)

    def test_a_missing_score_is_zero_rather_than_a_crash(self):
        """A recommendation without a strength must not take a turn down."""
        from reasoning.models import Confidence, Recommendation
        from workspace.service import observed_assessment

        class _Assessment:
            mode = Mode.EXECUTIVE
            mode_reason = ""
            continuation = False
            stale = False
            stale_because = ""
            obsolete = False
            replaced_stale = False
            has_reasoning = True
            recommendations = [
                Recommendation(statement="s", rationale="r", confidence=Confidence(score=0.5))
            ]
            initiatives: list[Any] = []

        observed = observed_assessment(_Assessment())
        assert observed is not None
        self.assertEqual(observed["confidence"], 0.5)
        self.assertEqual(observed["execution_risk"], 0.0)

    def test_the_reported_key_is_the_key_the_product_persists(self):
        """
        The identifier must identify the record it names.

        `Recommendation` carries `initiative`, not `initiative_slug`. Reading the
        latter returned "" and produced a key derived from a different slug than
        `_capture_strategy` used, so the observational key could disagree with the
        stored one. Both now call `_slug_for`, and this asserts they agree.
        """
        from reasoning.models import Confidence, Recommendation
        from workspace.models import recommendation_key
        from workspace.service import _slug_for, observed_assessment

        class _Initiative:
            name = "Billing"
            slug = "billing"

        recommendation = Recommendation(
            statement="Add tests to Billing",
            rationale="because",
            confidence=Confidence(score=0.7),
            initiative="Billing",
        )

        class _Assessment:
            mode = Mode.EXECUTIVE
            mode_reason = ""
            continuation = False
            stale = False
            stale_because = ""
            obsolete = False
            replaced_stale = False
            has_reasoning = True
            recommendations = [recommendation]
            initiatives = [_Initiative()]

        assessment = _Assessment()
        observed = observed_assessment(assessment)
        assert observed is not None
        expected_slug = _slug_for(assessment, recommendation.initiative)
        self.assertEqual(observed["initiative_slug"], expected_slug)
        self.assertEqual(observed["initiative_slug"], "billing")
        self.assertEqual(
            observed["recommendation_key"],
            recommendation_key(recommendation.statement, expected_slug),
        )


# The real evidence set from the acceptance turn that exposed this, and the six
# identifiers the model invented while six real ones sat unused in its context.
REAL_SHAS = ("5c44663", "4f3bb44", "fe92458", "7845950", "f153af7", "2b00654", "48b1283", "8546e13")
FAKE_SHAS = ("6d23456", "98b4567", "7654321", "a123456", "c987654", "f901234")

FABRICATED_ANSWER = "\n".join(
    [f"{i}. `{sha} Some plausible commit message`" for i, sha in enumerate(REAL_SHAS[:2], 1)]
    + [f"{i}. `{sha} Another plausible message`" for i, sha in enumerate(FAKE_SHAS, 3)]
)
CORRECTED_ANSWER = "\n".join(f"{i}. `{sha} A real commit`" for i, sha in enumerate(REAL_SHAS, 1))


def _evidence(**kw) -> EvidenceSet:
    return EvidenceSet(**{k: frozenset(v) for k, v in kw.items()})


class _StubAuthority:
    """
    A project with a known, fixed set of records.

    Stands in for git and the filesystem so the resolution *policy* can be tested
    without a repository. Anything not listed is `UNKNOWN` -- the records were
    consulted and it is not there -- which is the distinction that matters: an
    authority that answered `UNAVAILABLE` would make every claim unverifiable and
    prove nothing about how a fabrication is classified.
    """

    def __init__(
        self, *, commits=(), decisions=(), tasks=(), prs=(), paths=(), symbols=(), unavailable=()
    ):
        self._known = {
            "commit": {c.lower() for c in commits},
            "decision": set(decisions),
            "task": set(tasks),
            "pull_request": set(prs),
            "path": set(paths),
            "symbol": set(symbols),
        }
        self._unavailable = set(unavailable)

    def _answer(self, kind: str, value: str) -> Resolution:
        if kind in self._unavailable:
            return Resolution.UNAVAILABLE
        known = self._known[kind]
        if kind in ("decision", "task"):
            # `ADR-003` and `ADR-3` are one record, as a real store would read them.
            known = {_normalise_id(k) for k in known}
            value = _normalise_id(value)
        return (
            Resolution.RESOLVED
            if value.lower() in {k.lower() for k in known}
            else Resolution.UNKNOWN
        )

    def commit(self, candidate):
        # Abbreviation resolution, one-directional: a stored reference may be
        # abbreviated by the answer, never extended by it.
        if "commit" in self._unavailable:
            return Resolution.UNAVAILABLE
        matches = {c for c in self._known["commit"] if c.startswith(candidate.lower())}
        if len(matches) == 1:
            return Resolution.RESOLVED
        return Resolution.UNKNOWN

    def decision(self, i):
        return self._answer("decision", i)

    def task(self, i):
        return self._answer("task", i)

    def pull_request(self, i):
        return self._answer("pull_request", i)

    def path(self, i):
        return self._answer("path", i)

    def symbol(self, i):
        return self._answer("symbol", i)

    def available(self):
        return {
            k: k not in self._unavailable
            for k in ("commit", "decision", "task", "pull_request", "path", "symbol")
        }


class TestEvidenceValidation(unittest.TestCase):
    """
    An answer may narrate and infer. It may not invent identifiers that look like
    evidence.

    MondayOS retrieved eight real commits for one acceptance turn. The model
    reported eight, of which six were fabricated — patterned hashes with
    plausible messages — while six real ones sat unused in the context it had
    been given. The evidence was complete; the narration replaced it.
    """

    def _verdicts(self, answer, evidence=None, authority=None):
        report = validate_evidence(
            answer,
            evidence if evidence is not None else _evidence(),
            authority if authority is not None else _StubAuthority(commits=REAL_SHAS),
        )
        return {f.identifier.lower(): f.verdict.value for f in report.findings}

    # ------------------------------------------------------- RT-01: forgery

    def test_a_fabricated_sha_extending_a_real_prefix_is_rejected(self):
        """
        RT-01. The red-team bypass, and the reason prefix matching left this file.

        Evidence held the seven-character `5c44663`, and resolution matched in
        both directions, so *anything beginning with it* verified. A model could
        append thirty-three characters and produce a full-length SHA that does not
        exist and never did, presented as established fact.
        """
        forged = "5c44663deadbeefdeadbeefdeadbeefdeadbeef"
        found = self._verdicts(f"Recent work: `{forged}` overhauled sourcing.")
        self.assertEqual(found.get(forged), "unsupported")

    def test_an_overlong_hex_run_is_still_a_claim(self):
        """
        Caught by an independent verifier, delivered end-to-end before the fix.

        A git SHA is at most 40 characters, and "not a valid SHA" had been
        treated as "not a claim" -- so a 55-character forgery in backticks was
        shown to a reader as a commit without ever being checked.
        """
        forged = "5c44663" + "deadbeef" * 6
        found = self._verdicts(f"Recent work landed in `{forged}`.")
        self.assertEqual(found.get(forged), "unsupported")

    def test_a_malformed_authority_blocks_rather_than_raising(self):
        """
        Every authority failure must have a deterministic outcome.

        The fail-closed matrix found an exception propagating straight out of
        validation, which loses the user's turn instead of refusing it.
        """

        class Exploding:
            def commit(self, c):
                raise RuntimeError("authority exploded")

            decision = task = pull_request = path = symbol = commit

            def available(self):
                raise RuntimeError("availability exploded")

        report = validate_evidence("Work landed in `6d23456`.", _evidence(), Exploding())
        self.assertEqual([f.verdict for f in report.findings], [EvidenceVerdict.UNVERIFIABLE])
        self.assertEqual(len(report.blocking), 1)
        self.assertFalse(report.checked)

    def test_the_project_overrules_retrieval(self):
        """
        Retrieval may not vouch for an identifier the project denies.

        The order used to be the other way round, so anything that reached the
        citation list was verified without the records ever being consulted.
        """
        authority = _StubAuthority(commits=REAL_SHAS)
        found = self._verdicts(
            "Work landed in `deadbee`.",
            evidence=_evidence(commits=("deadbee",)),
            authority=authority,
        )
        self.assertEqual(found.get("deadbee"), "unsupported")

    def test_retrieval_still_settles_what_the_records_cannot_answer(self):
        """With no authority, "we handed the model this" is real evidence."""
        found = self._verdicts(
            "Work landed in `deadbee`.",
            evidence=_evidence(commits=("deadbee",)),
            authority=_StubAuthority(unavailable=("commit",)),
        )
        self.assertEqual(found.get("deadbee"), "verified")

    def test_a_real_abbreviation_still_resolves(self):
        self.assertEqual(
            self._verdicts("Recent work: `5c44663` landed.").get("5c44663"), "verified"
        )

    def test_an_abbreviation_matching_two_commits_is_rejected(self):
        authority = _StubAuthority(commits=("5c44663aaa", "5c44663bbb"))
        self.assertEqual(
            self._verdicts("see `5c44663`", authority=authority).get("5c44663"), "unsupported"
        )

    def test_a_commit_from_another_project_is_rejected(self):
        """Scoping is part of resolution: a parent's commit is not this project's."""
        self.assertEqual(self._verdicts("see `1dcac30`").get("1dcac30"), "unsupported")

    # ------------------------------------- RT-03: independence from retrieval

    def test_commits_are_checked_even_when_retrieval_supplied_none(self):
        """
        RT-03. Validation used to be gated on the evidence set having that class,
        and `git` is last in the context budget's priority -- so a crowded
        snapshot silently disabled commit checking altogether.
        """
        found = self._verdicts(
            "Recent work: `6d23456` rewrote the exporter.",
            evidence=_evidence(paths=("docs/ARCHITECTURE.md",)),
        )
        self.assertEqual(found.get("6d23456"), "unsupported")

    def test_a_claim_with_no_authority_is_unverifiable_not_clean(self):
        """
        RT-04. `UNAVAILABLE` must never read as a pass.

        Returning nothing for an unknowable claim is what let a turn report a
        clean bill of health having examined nothing.
        """
        found = self._verdicts(
            "Recent work: `6d23456` landed.",
            authority=_StubAuthority(unavailable=("commit",)),
        )
        self.assertEqual(found.get("6d23456"), "unverifiable")

    # ------------------------------------------------- RT-02: renderings

    def test_a_fabricated_sha_is_caught_in_every_rendering(self):
        """
        RT-02. Markdown emphasis defeated a rule built on bullet positions:
        `**7654321**` is neither a delimiter the rule knew nor a list position.
        """
        renderings = (
            "**7654321** rewrote scoring",
            "*7654321* rewrote scoring",
            "> **7654321** rewrote scoring",
            "[7654321](http://example.com/x)",
            "| 7654321 | rewrote scoring |",
            "the commit (7654321) shipped",
            "the commit [7654321] shipped",
            "* 7654321 rewrote scoring",
            "-   7654321 rewrote scoring",
            "3. 7654321 rewrote scoring",
            "  - `7654321` a message",
            "7654321 rewrote scoring",
            "- commit: 7654321 — recent commit",
        )
        for text in renderings:
            with self.subTest(rendering=text):
                self.assertEqual(self._verdicts(text).get("7654321"), "unsupported")

    def test_a_real_all_digit_sha_is_verified_in_those_renderings(self):
        """A false accusation is as much a failure as a missed fabrication."""
        for text in (
            "**7845950** was the WIP commit",
            "| 7845950 | WIP |",
            "- commit: 7845950 — recent commit",
            "see `7845950` for the WIP",
        ):
            with self.subTest(rendering=text):
                self.assertEqual(self._verdicts(text).get("7845950"), "verified")

    def test_a_quantity_in_prose_is_never_a_citation(self):
        for text in (
            "We processed 7654321 records last quarter.",
            "The build took 9876543 ms to finish.",
            "It took 1234567 attempts, then 7654321 more.",
            "confidence 0.47 and evidence 94%",
            "roughly 0.8 of the work is done",
        ):
            with self.subTest(text=text):
                self.assertEqual(self._verdicts(text), {})

    def test_validation_reads_what_the_reader_will_see(self):
        """
        Found by an independent verifier that did not share the production parser.

        Three ways the raw text and the rendered text disagreed, each hiding an
        identifier from every pattern at once while a reader saw it plainly:
        a Markdown escape, an HTML entity, and a soft hyphen. The rule is that
        validation and display must agree on what the identifier is.
        """
        cases = {
            "markdown escape": "Work landed in `6d2\\3456`.",
            "html entity": "Work landed in <code>&#54;d23456</code>.",
            "soft hyphen": "Work landed in `6d2\u00ad3456`.",
            "zero width": "Work landed in `6d2\u200b3456`.",
            "word joiner": "Work landed in `6d2\u20603456`.",
            "bidi mark": "Work landed in `\u200f6d23456`.",
        }
        for name, text in cases.items():
            with self.subTest(rendering=name):
                self.assertEqual(self._verdicts(text).get("6d23456"), "unsupported")

    def test_an_entity_encoded_record_id_is_still_read(self):
        found = self._verdicts(
            "This follows ADR&#45;9099.",
            authority=_StubAuthority(decisions=("ADR-3",)),
        )
        self.assertEqual(found.get("adr-9099"), "unsupported")

    def test_a_real_identifier_survives_the_same_normalisation(self):
        """Normalising must not turn a genuine citation into an accusation."""
        for text in ("Work landed in `5c44663`.", "Work landed in <code>5c44663</code>."):
            with self.subTest(text=text):
                self.assertEqual(self._verdicts(text).get("5c44663"), "verified")

    def test_a_lookalike_hidden_behind_unicode_is_still_read(self):
        """Fullwidth digits and a zero-width space both defeated the matchers."""
        self.assertEqual(
            self._verdicts("commit `\uff17\uff16\uff15\uff14\uff13\uff12\uff11` landed").get(
                "7654321"
            ),
            "unsupported",
        )
        self.assertEqual(
            self._verdicts("commit `765\u200b4321` landed").get("7654321"), "unsupported"
        )

    # ------------------------------------------------- the original failure

    def test_the_six_fabricated_shas_are_rejected(self):
        found = self._verdicts(FABRICATED_ANSWER)
        for sha in FAKE_SHAS:
            with self.subTest(sha=sha):
                self.assertEqual(found.get(sha), "unsupported")

    def test_real_abbreviated_shas_from_scoped_history_pass(self):
        found = self._verdicts(CORRECTED_ANSWER)
        for sha in REAL_SHAS:
            with self.subTest(sha=sha):
                self.assertEqual(found.get(sha), "verified")

    # ---------------------------------------------------- other identifiers

    def test_every_identifier_class_is_checked(self):
        authority = _StubAuthority(
            decisions=("ADR-3",),
            tasks=("TASK-59",),
            prs=("28",),
            paths=("workspace/responder.py",),
            symbols=("validate_evidence",),
        )
        cases = (
            ("See ADR-003 for the rationale.", "adr-003", "verified"),
            ("See ADR-017 for the rationale.", "adr-017", "unsupported"),
            ("See ADR-10017 for details.", "adr-10017", "unsupported"),
            ("TASK-0059 is done.", "task-0059", "verified"),
            ("TASK-1234567 is open.", "task-1234567", "unsupported"),
            ("See PR #28 for that.", "28", "verified"),
            ("See PR-999 for that.", "999", "unsupported"),
            ("see github.com/o/r/pull/999", "999", "unsupported"),
            ("see workspace/responder.py", "workspace/responder.py", "verified"),
            ("see workspace/ghost.rs", "workspace/ghost.rs", "unsupported"),
        )
        for text, ident, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self._verdicts(text, authority=authority).get(ident), expected)

    def test_a_web_address_is_not_a_repository_path(self):
        """Citing a website must not fail a turn closed."""
        found = self._verdicts("see https://example.com/docs/guide.html for background")
        self.assertEqual(found.get("example.com/docs/guide.html"), None)

    # ------------------------------------------------- RT-06: per-class truth

    def test_a_class_that_was_never_checked_does_not_report_success(self):
        """
        RT-06. `checked: true` used to mean "an evidence set existed", so a turn
        could report a clean bill of health having validated nothing at all.
        """
        report = validate_evidence(
            "Recent work: `6d23456` landed.",
            _evidence(),
            _StubAuthority(unavailable=("commit",)),
        )
        self.assertFalse(report.classes["commit"].validated)
        self.assertFalse(report.checked)
        self.assertEqual(report.classes["commit"].unverifiable, 1)

    def test_a_verified_class_reports_success(self):
        report = validate_evidence(
            "Recent work: `5c44663` landed.",
            _evidence(),
            _StubAuthority(commits=REAL_SHAS),
        )
        self.assertTrue(report.classes["commit"].validated)
        self.assertTrue(report.checked)

    def test_unverifiable_claims_block_just_like_unsupported_ones(self):
        report = validate_evidence(
            "Recent work: `6d23456` landed.",
            _evidence(),
            _StubAuthority(unavailable=("commit",)),
        )
        self.assertEqual(len(report.blocking), 1)

    # --------------------------------------------- structured citations

    def test_a_symbol_is_validated_when_cited_structurally(self):
        """
        RT-06. Symbols were collected into the evidence set, counted towards
        "is there evidence", and never validated -- so a symbols-only answer
        reported successful validation having checked nothing.

        Free prose is still not scanned for symbols: every backticked word would
        be a candidate and a project index that does not define `git status`
        would report it fabricated. A handle states its kind, so the claim is
        exact rather than guessed.
        """
        authority = _StubAuthority(symbols=("validate_evidence",))
        report = validate_evidence(
            "That work lives in validate_evidence.",
            _evidence(),
            authority,
            extra_claims=[("symbol", "ghost_function")],
        )
        verdicts = {f.identifier: f.verdict.value for f in report.findings}
        self.assertEqual(verdicts.get("ghost_function"), "unsupported")
        self.assertEqual(report.classes["symbol"].claims_found, 1)

    def test_a_symbol_with_no_index_is_unverifiable_not_verified(self):
        report = validate_evidence(
            "x",
            _evidence(),
            _StubAuthority(unavailable=("symbol",)),
            extra_claims=[("symbol", "anything")],
        )
        self.assertEqual(report.classes["symbol"].unverifiable, 1)
        self.assertFalse(report.classes["symbol"].validated)
        self.assertFalse(report.checked)

    def test_free_prose_is_not_scanned_for_symbols(self):
        """The deliberate limitation, asserted so it cannot regress into guessing."""
        report = validate_evidence(
            "Run `git status` and read `README` before calling do_thing().",
            _evidence(),
            _StubAuthority(symbols=("validate_evidence",)),
        )
        self.assertEqual(report.classes["symbol"].claims_found, 0)

    def test_a_cited_handle_resolves_to_the_real_identifier(self):
        handles = {"E1": EvidenceHandle("E1", "commit", "5c44663", "the roadmap")}
        text, cited, unknown = resolve_handles("Recent work landed [E1].", handles)
        self.assertEqual(text, "Recent work landed 5c44663.")
        self.assertEqual([h.label for h in cited], ["E1"])
        self.assertEqual(unknown, [])

    def test_a_handle_that_was_never_supplied_is_caught(self):
        handles = {"E1": EvidenceHandle("E1", "commit", "5c44663")}
        text, cited, unknown = resolve_handles("Work landed [E9].", handles)
        self.assertEqual(unknown, ["E9"])
        self.assertEqual(cited, [])
        self.assertIn("[E9]", text, "an unresolved handle is never rendered as a fact")


def _store_lookup(store: dict):
    """
    A task store that conforms to the lookup contract.

    `None` means the store could not be consulted at all; a missing task is
    `TaskRecord(exists=False)`. `dict.get` conflates the two, and conflating them
    is what turns "we could not check" into "it is not there".
    """
    return lambda task_id: store.get(task_id, TaskRecord(exists=False))


class TestProjectAuthorityAgainstRealGit(unittest.TestCase):
    """
    Commit resolution, against an actual repository.

    RT-01 was fixed by deleting a hand-rolled prefix match and delegating to git,
    so a stub would test the delegation and not the thing delegated to. This
    builds a real repository in a temporary directory: it proves `rev-parse
    --verify` rejects a forged extension and an ambiguous prefix, which is the
    entire basis of the fix.
    """

    def _repo(self, tmp: str) -> tuple[Path, str]:
        root = Path(tmp) / "repo"
        root.mkdir()
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], cwd=root, capture_output=True, text=True, check=True
        )
        run("init", "-q")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "Test")
        (root / "file.txt").write_text("one\n")
        run("add", "-A")
        run("commit", "-qm", "first commit")
        full = run("rev-parse", "HEAD").stdout.strip()
        return root, full

    def test_git_resolves_real_forms_and_rejects_forged_ones(self):
        with TemporaryDirectory() as tmp:
            root, full = self._repo(tmp)
            authority = ProjectAuthority(root)

            self.assertIs(authority.commit(full), Resolution.RESOLVED)
            self.assertIs(authority.commit(full[:7]), Resolution.RESOLVED)
            self.assertIs(authority.commit(full[:12]), Resolution.RESOLVED)

            # The red-team forgery: a real prefix with fabricated characters
            # appended. A prefix match accepted this; git does not.
            forged = (full[:7] + "deadbeef" * 5)[:40]
            self.assertIs(authority.commit(forged), Resolution.UNKNOWN)
            self.assertIs(authority.commit("0" * 7), Resolution.UNKNOWN)

    def test_task_authority_is_ownership_not_existence(self):
        """
        Tasks are managed centrally, so existence is the wrong question.

        One store under the MondayOS root holds every project's tasks and each
        records its owner as a registry slug. `TASK-0059` exists for somebody in
        every case below; the only thing that differs is who. Accepting mere
        existence would let a Cue App task substantiate a claim in sourcingBOT --
        the cross-project leak this subsystem exists to prevent, arriving
        through the front door.
        """
        store = {
            "TASK-0059": TaskRecord(exists=True, project="sourcingbot"),
            "TASK-0100": TaskRecord(exists=True, project="cue-app"),
            "TASK-0200": TaskRecord(exists=True, project=""),
            "TASK-9999": TaskRecord(exists=False),
        }
        authority = ProjectAuthority(Path("."), slug="sourcingbot", task_lookup=store.get)
        cases = {
            "TASK-0059": Resolution.RESOLVED,  # owned by this project
            "TASK-0100": Resolution.UNKNOWN,  # owned by another project
            "TASK-0200": Resolution.UNKNOWN,  # exists, no recorded owner
            "TASK-9999": Resolution.UNKNOWN,  # does not exist
        }
        for task_id, expected in cases.items():
            with self.subTest(task=task_id):
                self.assertIs(authority.task(task_id), expected)

    def test_an_unowned_task_belongs_to_no_project(self):
        """
        `unknown` is not `matches everything`.

        The same rule `TaskManager.list_active(project=...)` already applies, and
        for the same reason: guessing an owner is how a slug-in-title heuristic
        once assigned tasks to projects that never claimed them.
        """
        store = {"TASK-0200": TaskRecord(exists=True, project="")}
        for slug in ("sourcingbot", "cue-app", "mondayos"):
            with self.subTest(project=slug):
                authority = ProjectAuthority(Path("."), slug=slug, task_lookup=_store_lookup(store))
                self.assertIs(authority.task("TASK-0200"), Resolution.UNKNOWN)

    def test_an_unreachable_task_store_is_unavailable_not_absent(self):
        """
        A workspace with no task store has not established that a task is absent.

        Reporting absence would fail a real answer closed for citing real work,
        which is the false-accusation direction.
        """
        authority = ProjectAuthority(Path("."), slug="sourcingbot", task_lookup=lambda _: None)
        self.assertIs(authority.task("TASK-0059"), Resolution.UNAVAILABLE)
        self.assertFalse(authority.available()["task"])

    def test_a_task_claim_for_the_owning_project_verifies_end_to_end(self):
        """The case that was failing closed on legitimate answers."""
        store = {"TASK-0059": TaskRecord(exists=True, project="sourcingbot")}
        authority = ProjectAuthority(
            Path("."), slug="sourcingbot", task_lookup=_store_lookup(store)
        )
        report = validate_evidence("Tracked as TASK-0059.", EvidenceSet(), authority)
        self.assertEqual(
            [(f.identifier, f.verdict) for f in report.findings],
            [("TASK-0059", EvidenceVerdict.VERIFIED)],
        )
        self.assertEqual(report.blocking, [])
        self.assertTrue(report.classes["task"].authority_available)

    def test_a_foreign_task_claim_blocks(self):
        store = {"TASK-0100": TaskRecord(exists=True, project="cue-app")}
        authority = ProjectAuthority(
            Path("."), slug="sourcingbot", task_lookup=_store_lookup(store)
        )
        report = validate_evidence("Tracked as TASK-0100.", EvidenceSet(), authority)
        self.assertEqual(len(report.blocking), 1)

    def test_a_project_without_git_reports_unavailable_not_absent(self):
        """The distinction RT-04 turned on: cannot check is not the same as clean."""
        with TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain"
            plain.mkdir()
            authority = ProjectAuthority(plain)
            self.assertIs(authority.commit("5c44663"), Resolution.UNAVAILABLE)
            self.assertFalse(authority.available()["commit"])

    def test_a_symbol_without_an_index_is_unavailable(self):
        with TemporaryDirectory() as tmp:
            authority = ProjectAuthority(Path(tmp))
            self.assertIs(authority.symbol("anything"), Resolution.UNAVAILABLE)
            self.assertFalse(authority.available()["symbol"])

    def test_a_path_outside_the_project_never_resolves(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            (root / "src").mkdir(parents=True)
            (root / "src" / "main.py").write_text("x = 1\n")
            (Path(tmp) / "secret.txt").write_text("nope\n")
            authority = ProjectAuthority(root)
            self.assertIs(authority.path("src/main.py"), Resolution.RESOLVED)
            self.assertIs(authority.path("../secret.txt"), Resolution.UNKNOWN)
            self.assertIs(authority.path("src/ghost.py"), Resolution.UNKNOWN)


class TestBackstopUnderFuzzing(unittest.TestCase):
    """
    The heuristic backstop, against renderings nobody wrote down.

    Three rounds of live runs each found another Markdown shape the previous rule
    missed -- a bulleted hash, a labelled one, an inline code span -- and each was
    fixed by adding that shape to a list. A list of thirty delimiters proves
    nothing about the thirty-first, so this generates them instead.

    Seeded rather than random: a fuzz test that fails only on some runs is a test
    nobody can act on. The seed is fixed, the corpus is therefore identical on
    every machine, and a failure names the exact rendering that broke.
    """

    WRAPS = (
        "`{0}`",
        "**{0}**",
        "*{0}*",
        "_{0}_",
        "~~{0}~~",
        '"{0}"',
        "'{0}'",
        "({0})",
        "[{0}]",
        "`{0}`",
        "**`{0}`**",
    )
    LEADS = (
        "",
        "- ",
        "* ",
        "+ ",
        "1. ",
        "2) ",
        "  - ",
        "    ",
        "> ",
        "> - ",
        "| ",
        "commit: ",
        "- commit: ",
        "sha: ",
        "rev - ",
    )
    TAILS = (
        "",
        " rewrote the exporter",
        ": rewrote the exporter",
        " |",
        " — recent commit",
        ".",
        ",",
        ")",
    )

    def _corpus(self, token: str, count: int = 600) -> list[str]:
        rng = random.Random(20260911)
        out = []
        for _ in range(count):
            body = rng.choice(self.WRAPS).format(token) if rng.random() < 0.75 else token
            lead = rng.choice(self.LEADS)
            tail = rng.choice(self.TAILS)
            line = f"{lead}{body}{tail}"
            if rng.random() < 0.4:
                line = f"Recent work touched this project:\n{line}"
            out.append(line)
        return out

    def _formatted(self, rendering: str, token: str) -> bool:
        """Whether the token is marked as an identifier rather than left bare in prose."""
        index = rendering.index(token)
        before = rendering[:index].rsplit("\n", 1)[-1]
        after = rendering[index + len(token) :]
        return bool(before.strip() or (before and after[:1] in "`*_~\"')]|"))

    def test_a_fabricated_hex_sha_is_detected_in_every_rendering(self):
        """A hash containing a letter is unambiguous, so this has no exceptions."""
        authority = _StubAuthority(commits=REAL_SHAS)
        for rendering in self._corpus("6d23456"):
            with self.subTest(rendering=rendering):
                found = validate_evidence(rendering, _evidence(), authority)
                self.assertEqual(
                    {f.verdict for f in found.findings if f.identifier == "6d23456"},
                    {EvidenceVerdict.UNSUPPORTED},
                )

    def test_a_fabricated_all_digit_sha_is_detected_wherever_it_is_marked(self):
        """
        The narrower invariant, and the honest one.

        An all-digit token in bare prose is indistinguishable from a quantity --
        `We processed 7654321 records` -- so detection is claimed only where the
        answer marks the token as an identifier. That residual is why the
        structured citation protocol exists rather than a longer delimiter list.
        """
        authority = _StubAuthority(commits=REAL_SHAS)
        for rendering in self._corpus("7654321"):
            if not self._formatted(rendering, "7654321"):
                continue
            with self.subTest(rendering=rendering):
                found = validate_evidence(rendering, _evidence(), authority)
                self.assertEqual(
                    {f.verdict for f in found.findings if f.identifier == "7654321"},
                    {EvidenceVerdict.UNSUPPORTED},
                )

    def test_a_real_sha_is_never_accused_in_any_rendering(self):
        """A false accusation fails a good answer closed, which is its own failure."""
        authority = _StubAuthority(commits=REAL_SHAS)
        for token in ("5c44663", "7845950"):
            for rendering in self._corpus(token, count=300):
                verdicts = {
                    f.verdict
                    for f in validate_evidence(rendering, _evidence(), authority).findings
                    if f.identifier == token
                }
                with self.subTest(rendering=rendering):
                    self.assertNotIn(EvidenceVerdict.UNSUPPORTED, verdicts)
                    self.assertNotIn(EvidenceVerdict.UNVERIFIABLE, verdicts)

    def test_a_quantity_is_never_read_as_a_citation(self):
        authority = _StubAuthority(commits=REAL_SHAS)
        rng = random.Random(9022)
        units = ("records", "ms", "rows", "tokens", "bytes", "users", "attempts")
        for _ in range(300):
            number = rng.randrange(1_000_000, 99_999_999)
            text = f"We processed {number} {rng.choice(units)} last quarter."
            with self.subTest(text=text):
                found = validate_evidence(text, _evidence(), authority)
                self.assertEqual(found.findings, [])


class _ScriptedProvider(AIProvider):
    """A provider that returns prepared answers, so the flow can be tested offline."""

    def __init__(self, *answers: str, fail_on: int = -1) -> None:
        self._answers = list(answers)
        self._fail_on = fail_on
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(available=True, provider="scripted")

    def ask(self, prompt, context="", max_tokens=1024, **kwargs):
        index = len(self.calls)
        self.calls.append(prompt)
        if index == self._fail_on:
            raise ProviderError("provider died during correction")
        answer = self._answers[min(index, len(self._answers) - 1)]
        return ProviderResponse(content=answer, model="m", provider="scripted")

    def plan(self, objective, context="", max_tokens=2048, **kwargs):
        return self.ask(objective)

    def summarize(self, content, max_words=150, **kwargs):
        return self.ask(content)

    def review(self, content, criteria="", **kwargs):
        return self.ask(content)


class TestCitationsSurviveToTheEvidenceSet(unittest.TestCase):
    """
    The allowlist is only worth having if it reaches the validator.

    Validation was written, tested and correct, and inert on the very turn that
    motivated it: the git adapter recorded no citations, and the budget rebuilt
    every source without the ones that did exist. An empty evidence set makes
    every fabricated identifier vacuously acceptable, so these are the two
    joints that have to hold.
    """

    def test_the_git_adapter_cites_the_commits_it_supplied(self):
        from workspace.context.adapters import _cite_commit

        self.assertEqual(
            _cite_commit("  5c44663 sourcingBOT: define shortlist-first sourcing roadmap"),
            {"kind": "commit", "reference": "5c44663"},
        )
        self.assertIsNone(_cite_commit("Working tree: clean"))
        self.assertIsNone(_cite_commit("Current branch: main"))

    def test_a_citation_survives_when_its_reference_is_not_in_the_prose(self):
        """
        RT-05. The budget used to keep a citation only if its reference appeared
        literally in the retained text, so a file citation attached to a prose
        summary was discarded even though nothing had been trimmed. The allowlist
        then shrank below what the model was actually shown -- or emptied, which
        switched validation off entirely.
        """
        from workspace.context.budget import apply

        source = ContextSource(
            name="docs",
            label="Docs",
            items=["Architecture overview: the system has three layers."],
            reasons=["r"],
            citations=[{"kind": "file", "reference": "docs/ARCHITECTURE.md", "item": 0}],
        )
        kept = apply([source], total_cap=10_000).sources[0]
        self.assertEqual([c["reference"] for c in kept.citations], ["docs/ARCHITECTURE.md"])

    def test_the_budget_keeps_citations_for_items_it_kept(self):
        from workspace.context.budget import apply

        source = ContextSource(
            name="git",
            label="Git state",
            items=["  5c44663 kept", "  4f3bb44 dropped"],
            reasons=["recent", "recent"],
            citations=[
                {"kind": "commit", "reference": "5c44663", "item": 0},
                {"kind": "commit", "reference": "4f3bb44", "item": 1},
            ],
        )
        kept = apply([source], total_cap=len("  5c44663 kept")).sources[0]
        self.assertEqual([c["reference"] for c in kept.citations], ["5c44663"])

    def test_a_citation_whose_item_was_dropped_does_not_survive(self):
        """
        Evidence the model was never shown must not become evidence it may cite.

        The budget trims to fit. A citation carried past the item that held it
        would allowlist a commit that never reached the prompt.
        """
        from workspace.context.budget import apply

        source = ContextSource(
            name="git",
            label="Git state",
            items=["  5c44663 kept"],
            reasons=["recent"],
            citations=[{"kind": "commit", "reference": "deadbee", "item": 5}],
        )
        self.assertEqual(apply([source], total_cap=1000).sources[0].citations, [])


class TestValidationPreservesTruncation(unittest.TestCase):
    """
    Checking an answer must not make a cut-off one look finished.

    Validation was written as an early return, which skipped the
    `stop_reason == "max_tokens"` branch below it. Every evidence-bearing turn
    then reported `incomplete=False` no matter where the model stopped -- a
    dishonest answer produced by the machinery built to enforce honesty.
    """

    def _reply(self, stop_reason: str, text: str):
        from brain.providers.base import ProviderChunk

        class Truncating(_ScriptedProvider):
            @property
            def supports_streaming(self) -> bool:
                return True

            def stream(self, prompt, context="", max_tokens=1024, **kwargs):
                yield ProviderChunk(text=text)
                yield ProviderChunk(
                    done=True, model="m", provider="scripted", stop_reason=stop_reason
                )

        snapshot = ContextSnapshot(
            id="snap",
            project="sourcingbot",
            created_at=T0,
            citations=[{"kind": "commit", "reference": sha} for sha in REAL_SHAS],
        )
        request = WorkspaceRequest(
            project="sourcingbot",
            message="What changed recently?",
            snapshot=snapshot,
            authority=_StubAuthority(commits=REAL_SHAS),
        )
        responder = ProviderWorkspaceResponder(Truncating(CORRECTED_ANSWER))
        return list(responder.respond_stream(request))[-1].reply

    def test_a_verified_answer_cut_off_at_max_tokens_is_still_incomplete(self):
        reply = self._reply("max_tokens", f"We landed {REAL_SHAS[0]} and then")
        self.assertTrue(reply.incomplete)
        self.assertEqual(reply.metadata.get("stop_reason"), "max_tokens")
        self.assertTrue(reply.metadata["evidence_validation"]["checked"])

    def test_a_verified_answer_that_finished_is_complete(self):
        reply = self._reply("end_turn", f"We landed {REAL_SHAS[0]}.")
        self.assertFalse(reply.incomplete)
        self.assertEqual(reply.metadata["evidence_validation"]["unsupported_count"], 0)


class TestEvidenceCorrectionFlow(unittest.TestCase):
    """
    Unsupported evidence never survives as a verified factual claim.

    One correction, then fail closed. Never an endless loop, and never the
    original fabrication returned because the retry did not work out.
    """

    def _request(self) -> WorkspaceRequest:
        snapshot = ContextSnapshot(
            id="snap",
            project="sourcingbot",
            created_at=T0,
            citations=[{"kind": "commit", "reference": sha} for sha in REAL_SHAS],
        )
        return WorkspaceRequest(
            project="sourcingbot",
            message="What changed recently?",
            snapshot=snapshot,
            authority=_StubAuthority(commits=REAL_SHAS),
        )

    def test_a_clean_answer_passes_through_untouched(self):
        provider = _ScriptedProvider(CORRECTED_ANSWER)
        reply = ProviderWorkspaceResponder(provider).respond(self._request())
        self.assertEqual(reply.content, CORRECTED_ANSWER)
        self.assertEqual(len(provider.calls), 1)
        validation = reply.metadata["evidence_validation"]
        self.assertEqual(validation["unsupported_count"], 0)
        self.assertFalse(validation["correction_attempted"])

    def test_one_correction_is_attempted_and_can_succeed(self):
        provider = _ScriptedProvider(FABRICATED_ANSWER, CORRECTED_ANSWER)
        reply = ProviderWorkspaceResponder(provider).respond(self._request())
        self.assertEqual(reply.content, CORRECTED_ANSWER)
        self.assertEqual(len(provider.calls), 2, "exactly one correction")
        validation = reply.metadata["evidence_validation"]
        self.assertTrue(validation["correction_attempted"])
        self.assertTrue(validation["correction_succeeded"])

    def test_the_correction_names_the_unsupported_identifiers(self):
        """MondayOS already knows which ones failed; the model is told, not asked."""
        provider = _ScriptedProvider(FABRICATED_ANSWER, CORRECTED_ANSWER)
        ProviderWorkspaceResponder(provider).respond(self._request())
        correction = provider.calls[1]
        for sha in FAKE_SHAS:
            self.assertIn(sha, correction)
        self.assertNotIn("check whether", correction.lower())

    def test_a_second_failure_fails_closed(self):
        provider = _ScriptedProvider(FABRICATED_ANSWER, FABRICATED_ANSWER)
        reply = ProviderWorkspaceResponder(provider).respond(self._request())
        self.assertEqual(len(provider.calls), 2, "never a third attempt")
        self.assertTrue(reply.incomplete)
        self.assertIn("could not verify", reply.content)
        for sha in FAKE_SHAS:
            self.assertNotIn(sha, reply.content, "the fabrication must not be re-shown")
        validation = reply.metadata["evidence_validation"]
        self.assertFalse(validation["correction_succeeded"])
        self.assertEqual(sorted(validation["unsupported"]), sorted(FAKE_SHAS))

    def test_a_provider_failure_during_correction_does_not_rescue_the_original(self):
        provider = _ScriptedProvider(FABRICATED_ANSWER, fail_on=1)
        reply = ProviderWorkspaceResponder(provider).respond(self._request())
        self.assertNotIn("6d23456", reply.content)
        self.assertTrue(reply.incomplete)
        self.assertFalse(reply.metadata["evidence_validation"]["correction_succeeded"])

    def test_the_diagnostic_metadata_is_deterministic(self):
        first = ProviderWorkspaceResponder(
            _ScriptedProvider(FABRICATED_ANSWER, FABRICATED_ANSWER)
        ).respond(self._request())
        second = ProviderWorkspaceResponder(
            _ScriptedProvider(FABRICATED_ANSWER, FABRICATED_ANSWER)
        ).respond(self._request())
        self.assertEqual(
            first.metadata["evidence_validation"], second.metadata["evidence_validation"]
        )

    def test_an_evidence_bearing_stream_emits_nothing_before_validation(self):
        """
        The streaming guarantee. No amount of post-generation checking un-shows a
        citation someone has already read, so an evidence-bearing turn is held
        back until it has been verified.
        """
        provider = _ScriptedProvider(FABRICATED_ANSWER, FABRICATED_ANSWER)
        chunks = list(ProviderWorkspaceResponder(provider).respond_stream(self._request()))
        deltas = [c.text for c in chunks if c.text]
        for sha in FAKE_SHAS:
            for delta in deltas:
                self.assertNotIn(sha, delta, "a fabricated SHA reached the user")

    def test_a_clean_buffered_response_is_emitted_after_validation(self):
        provider = _ScriptedProvider(CORRECTED_ANSWER)
        chunks = list(ProviderWorkspaceResponder(provider).respond_stream(self._request()))
        deltas = "".join(c.text for c in chunks if c.text)
        self.assertIn(REAL_SHAS[0], deltas)
        self.assertTrue(chunks[-1].done)

    def test_a_turn_without_evidence_still_streams_progressively(self):
        """Conversational turns have no identifiers to get wrong; they are untouched."""
        provider = _ScriptedProvider("Hello, nothing to cite here.")
        request = WorkspaceRequest(project="p", message="hi")
        chunks = list(ProviderWorkspaceResponder(provider).respond_stream(request))
        self.assertTrue([c for c in chunks if c.text])

    def test_validation_does_not_touch_scoring_or_state(self):
        """
        Observational only. Nothing downstream may depend on it.

        A validator that changed a score or a register would make evidence
        integrity a reasoning input, which is exactly the coupling the assessment
        key was kept free of.
        """
        provider = _ScriptedProvider(FABRICATED_ANSWER, CORRECTED_ANSWER)
        request = self._request()
        reply = ProviderWorkspaceResponder(provider).respond(request)
        self.assertIn("evidence_validation", reply.metadata)
        self.assertIsNone(request.assessment)
