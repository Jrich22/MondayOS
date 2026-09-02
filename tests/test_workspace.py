"""
Tests for the MondayOS AI Workspace.

The security tests are the point of this file. Cross-project leakage and secret
exposure are the two failures that cannot be walked back once a prompt has left
the machine, so they are asserted directly rather than assumed from careful code
(ADR-017).
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from brain.providers.base import AIProvider, ProviderAvailability, ProviderError, ProviderResponse
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
    ProviderWorkspaceResponder,
    WorkspaceReply,
    WorkspaceRequest,
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
        """The slug is the isolation primitive: no separators survive it."""
        for hostile in ("../other", "/etc/passwd", "a/../b", "..\\win"):
            self.assertNotIn("/", slugify(hostile))
            self.assertNotIn("\\", slugify(hostile))
            self.assertNotIn("..", slugify(hostile))

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
            self.assertEqual(first_beta.id, "CONV-0001")

    def test_the_same_id_in_two_projects_returns_two_different_conversations(self):
        """
        Per-project counters mean CONV-0001 exists in every project.

        That is deliberate (it stops one project inferring another's volume), and
        it makes project scoping load-bearing rather than decorative: an id alone
        does not identify a conversation, so a read that forgot its project could
        not silently return the wrong one — it would have nothing to open.
        """
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            alpha = store.create("alpha", "alpha topic", now=T0)
            beta = store.create("beta", "beta topic", now=T0)
            self.assertEqual(alpha.id, beta.id)
            self.assertEqual(store.get("alpha", "CONV-0001").title, "alpha topic")
            self.assertEqual(store.get("beta", "CONV-0001").title, "beta topic")

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
        with TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            created = store.create("../escape", "x", now=T0)
            base = (Path(tmp) / "workspace" / "conversations").resolve()
            path = store.project_dir("../escape").resolve()
            self.assertTrue(str(path).startswith(str(base)))
            self.assertEqual(created.project, "escape")

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
            self.assertEqual(names, ["identity", "docs", "tasks", "knowledge", "git"])
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
