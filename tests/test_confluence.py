"""
Tests for the Confluence publishing integration.

Covers the Markdown→storage converter, credential handling, the fake client,
the local mapping/history store, the publisher (create / update / dry-run /
unchanged / explicit page), and the Monday.publish API + `monday publish` CLI.

No test makes a live API call or requires a real Confluence account: every
publish routes through FakeConfluenceClient or the MONDAYOS_CONFLUENCE_FAKE
env seam.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from integrations.confluence import (
    ConfluenceConfig,
    ConfluencePublisher,
    FakeConfluenceClient,
    PublishDoc,
    PublishEvent,
    PublishRecord,
    PublishStore,
    markdown_to_storage,
)
from monday import Monday, MondayConfig
from monday.cli import main

_CONFLUENCE_ENV = [
    "CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_SPACE_KEY", "MONDAYOS_CONFLUENCE_FAKE",
]


def _patched_env(overrides: dict[str, str] | None = None):
    """Patch os.environ with confluence vars cleared, then apply overrides.

    Keeps unrelated environment variables (PATH, etc.) intact so Monday can
    still construct, while making the four Confluence credentials deterministic.
    """
    base = {k: v for k, v in os.environ.items() if k not in _CONFLUENCE_ENV}
    base.update(overrides or {})
    return mock.patch.dict(os.environ, base, clear=True)


# ---------------------------------------------------------------------------
# Markdown → storage converter
# ---------------------------------------------------------------------------

class TestConverter(unittest.TestCase):
    def test_headings(self):
        self.assertEqual(markdown_to_storage("# H1"), "<h1>H1</h1>")
        self.assertEqual(markdown_to_storage("### Sub"), "<h3>Sub</h3>")

    def test_paragraph_and_inline(self):
        out = markdown_to_storage("See [x](https://y.io), **bold**, `code`.")
        self.assertIn('<a href="https://y.io">x</a>', out)
        self.assertIn("<strong>bold</strong>", out)
        self.assertIn("<code>code</code>", out)
        self.assertTrue(out.startswith("<p>"))

    def test_unordered_list(self):
        self.assertEqual(markdown_to_storage("- a\n- b"), "<ul><li>a</li><li>b</li></ul>")

    def test_ordered_list(self):
        self.assertEqual(markdown_to_storage("1. a\n2. b"), "<ol><li>a</li><li>b</li></ol>")

    def test_code_block(self):
        out = markdown_to_storage("```python\nprint(1)\n```")
        self.assertIn('ac:name="code"', out)
        self.assertIn('ac:name="language">python', out)
        self.assertIn("<![CDATA[print(1)]]>", out)

    def test_table(self):
        out = markdown_to_storage("| A | B |\n|---|---|\n| 1 | 2 |")
        self.assertIn("<th>A</th>", out)
        self.assertIn("<td>2</td>", out)

    def test_html_is_escaped(self):
        out = markdown_to_storage("a < b & c > d")
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)
        self.assertIn("&gt;", out)


# ---------------------------------------------------------------------------
# Config + credential checks
# ---------------------------------------------------------------------------

class TestConfig(unittest.TestCase):
    def test_missing_all_credentials(self):
        chk = ConfluenceConfig.from_env({}).credential_check()
        self.assertFalse(chk.ok)
        self.assertIn("CONFLUENCE_BASE_URL", chk.missing)
        self.assertIn("never in source", chk.instructions())

    def test_all_present(self):
        cfg = ConfluenceConfig.from_env({
            "CONFLUENCE_BASE_URL": "https://x.atlassian.net",
            "CONFLUENCE_EMAIL": "a@b.c",
            "CONFLUENCE_API_TOKEN": "tok",
            "CONFLUENCE_SPACE_KEY": "ENG",
        })
        self.assertTrue(cfg.credential_check().ok)

    def test_api_token_never_exposed(self):
        cfg = ConfluenceConfig.from_env({"CONFLUENCE_API_TOKEN": "supersecret"})
        self.assertNotIn("supersecret", json.dumps(cfg.redacted()))
        self.assertNotIn("supersecret", cfg.credential_check().instructions())

    def test_fake_requires_no_credentials(self):
        cfg = ConfluenceConfig.from_env({"MONDAYOS_CONFLUENCE_FAKE": "1"})
        self.assertTrue(cfg.use_fake)
        self.assertTrue(cfg.credential_check().ok)

    def test_space_only_required_for_create(self):
        cfg = ConfluenceConfig.from_env({
            "CONFLUENCE_BASE_URL": "https://x", "CONFLUENCE_EMAIL": "a",
            "CONFLUENCE_API_TOKEN": "t",
        })
        self.assertFalse(cfg.credential_check(require_space=True).ok)
        self.assertTrue(cfg.credential_check(require_space=False).ok)


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

class TestFakeClient(unittest.TestCase):
    def test_create_then_get(self):
        c = FakeConfluenceClient()
        r = c.create_page("ENG", "T", "<p>x</p>")
        self.assertTrue(r.id)
        self.assertEqual(r.version, 1)
        self.assertEqual(c.get_page(r.id).title, "T")

    def test_update_increments_version(self):
        c = FakeConfluenceClient()
        r = c.create_page("ENG", "T", "<p>x</p>")
        r2 = c.update_page(r.id, "T2", "<p>y</p>")
        self.assertEqual(r2.id, r.id)
        self.assertEqual(r2.version, 2)

    def test_get_missing_returns_none(self):
        self.assertIsNone(FakeConfluenceClient().get_page("PAGE-999"))

    def test_persistence_across_instances(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "fake.json"
            rid = FakeConfluenceClient(store_path=path).create_page("ENG", "T", "<p>x</p>").id
            self.assertIsNotNone(FakeConfluenceClient(store_path=path).get_page(rid))


# ---------------------------------------------------------------------------
# Mapping / history store
# ---------------------------------------------------------------------------

class TestMappingStore(unittest.TestCase):
    def test_persist_and_reload(self):
        with TemporaryDirectory() as d:
            store = PublishStore(Path(d))
            store.record(
                PublishRecord(doc_id="D", page_id="P1", checksum="abc", status="created"),
                PublishEvent(doc_id="D", action="created", page_id="P1"),
            )
            reloaded = PublishStore(Path(d))
            self.assertEqual(reloaded.get("D").page_id, "P1")
            self.assertEqual(len(reloaded.history()), 1)

    def test_history_most_recent_first(self):
        with TemporaryDirectory() as d:
            store = PublishStore(Path(d))
            store.log_event(PublishEvent(doc_id="D", action="dry-run", at="2026-01-01T00:00:00Z"))
            store.log_event(PublishEvent(doc_id="D", action="created", at="2026-01-02T00:00:00Z"))
            hist = store.history()
            self.assertEqual(hist[0].action, "created")


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------

class _RecordingClient(FakeConfluenceClient):
    """Fake client that records which mutating calls were made."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def create_page(self, *a, **k):
        self.calls.append("create")
        return super().create_page(*a, **k)

    def update_page(self, *a, **k):
        self.calls.append("update")
        return super().update_page(*a, **k)


class TestPublisher(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.client = FakeConfluenceClient()
        self.pub = ConfluencePublisher(
            self.root, ConfluenceConfig(space_key="ENG"), client=self.client
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _doc(self, body="# Roadmap\n\n- a\n", doc_id="docs/ROADMAP.md"):
        return PublishDoc(doc_id=doc_id, title="Roadmap", markdown=body, source=doc_id)

    def test_create_new_page(self):
        r = self.pub.publish(self._doc())
        self.assertTrue(r.success)
        self.assertEqual(r.action, "created")
        self.assertTrue(r.page_id)
        self.assertTrue(r.url)

    def test_update_uses_stored_mapping(self):
        r1 = self.pub.publish(self._doc())
        r2 = self.pub.publish(self._doc(body="# Roadmap\n\n- a\n- b\n"))
        self.assertEqual(r2.action, "updated")
        self.assertEqual(r2.page_id, r1.page_id)  # same page, not a new one

    def test_unchanged_content_is_skipped(self):
        self.pub.publish(self._doc())
        r = self.pub.publish(self._doc())
        self.assertEqual(r.action, "up-to-date")

    def test_force_republishes_unchanged(self):
        self.pub.publish(self._doc())
        r = self.pub.publish(self._doc(), force=True)
        self.assertEqual(r.action, "updated")

    def test_dry_run_makes_no_client_calls(self):
        client = _RecordingClient()
        pub = ConfluencePublisher(self.root, ConfluenceConfig(space_key="ENG"), client=client)
        r = pub.publish(self._doc(), dry_run=True)
        self.assertTrue(r.dry_run)
        self.assertEqual(r.action, "dry-run")
        self.assertEqual(client.calls, [])

    def test_explicit_update_page_overwrites(self):
        pre = self.client.create_page("ENG", "Existing", "<p>old</p>")
        r = self.pub.publish(self._doc(doc_id="new-doc"), update_page=pre.id)
        self.assertEqual(r.action, "updated")
        self.assertEqual(r.page_id, pre.id)

    def test_create_requires_space_key(self):
        pub = ConfluencePublisher(self.root, ConfluenceConfig(), client=self.client)
        r = pub.publish(self._doc())
        self.assertFalse(r.success)
        self.assertIn("space", r.message.lower())

    def test_metadata_is_recorded(self):
        r = self.pub.publish(self._doc())
        rec = PublishStore(self.root).get("docs/ROADMAP.md")
        self.assertEqual(rec.page_id, r.page_id)
        self.assertEqual(rec.status, "created")
        self.assertTrue(rec.checksum)
        self.assertTrue(rec.last_published)
        self.assertTrue(rec.url)
        self.assertEqual(rec.source, "docs/ROADMAP.md")


# ---------------------------------------------------------------------------
# Monday.publish API
# ---------------------------------------------------------------------------

class TestMondayPublish(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "ROADMAP.md").write_text("# Roadmap\n\n- ship it\n")
        self.monday = Monday(MondayConfig(project_root=self.root))

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_run_needs_no_credentials(self):
        with _patched_env():
            r = self.monday.publish("confluence", file="docs/ROADMAP.md", dry_run=True)
        self.assertTrue(r.success)
        self.assertTrue(r.dry_run)
        self.assertEqual(r.status, "dry-run")

    def test_missing_credentials_fail_clearly(self):
        with _patched_env():
            r = self.monday.publish("confluence", file="docs/ROADMAP.md")
        self.assertFalse(r.success)
        self.assertIn("Missing Confluence credentials", r.message)

    def test_publish_with_injected_client(self):
        r = self.monday.publish(
            "confluence", file="docs/ROADMAP.md", space_key="ENG",
            client=FakeConfluenceClient(),
        )
        self.assertTrue(r.success)
        self.assertEqual(r.status, "created")
        self.assertTrue(r.page_id)

    def test_update_previously_published_page(self):
        client = FakeConfluenceClient()
        first = self.monday.publish(
            "confluence", file="docs/ROADMAP.md", space_key="ENG", client=client
        )
        (self.root / "docs" / "ROADMAP.md").write_text("# Roadmap\n\n- ship it\n- and more\n")
        second = self.monday.publish("confluence", file="docs/ROADMAP.md", client=client)
        self.assertEqual(second.status, "updated")
        self.assertEqual(second.page_id, first.page_id)

    def test_unresolvable_document(self):
        r = self.monday.publish("confluence", doc_id="NOPE-9999")
        self.assertFalse(r.success)
        self.assertIn("resolve", r.message.lower())

    def test_history_action(self):
        self.monday.publish(
            "confluence", file="docs/ROADMAP.md", space_key="ENG",
            client=FakeConfluenceClient(),
        )
        r = self.monday.publish("history")
        self.assertTrue(r.success)
        self.assertGreaterEqual(r.data["count"], 1)

    def test_unknown_action(self):
        r = self.monday.publish("frobnicate")
        self.assertFalse(r.success)
        self.assertIn("Unknown action", r.message)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestPublishCLI(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = str(Path(self._tmp.name))
        (Path(self.root) / "docs").mkdir()
        (Path(self.root) / "docs" / "ROADMAP.md").write_text("# Roadmap\n\n- ship it\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _cli(self, *argv, env=None) -> tuple[int, str]:
        buf = io.StringIO()
        with _patched_env(env), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(["--project-root", self.root, *argv])
        return code, buf.getvalue()

    def test_dry_run(self):
        code, out = self._cli("publish", "confluence", "--file", "docs/ROADMAP.md", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Dry run", out)

    def test_missing_credentials(self):
        code, out = self._cli("publish", "confluence", "--file", "docs/ROADMAP.md")
        self.assertEqual(code, 1)
        self.assertIn("Missing Confluence credentials", out)

    def test_history_empty(self):
        code, out = self._cli("publish", "history")
        self.assertEqual(code, 0)
        self.assertIn("No publish history", out)

    def test_fake_create_then_update_via_cli(self):
        env = {"MONDAYOS_CONFLUENCE_FAKE": "1", "CONFLUENCE_SPACE_KEY": "ENG"}
        code, out = self._cli("publish", "confluence", "--file", "docs/ROADMAP.md", env=env)
        self.assertEqual(code, 0)
        self.assertIn("Created", out)
        self.assertIn("PAGE-", out)

        # change content, publish again → update of the same page
        (Path(self.root) / "docs" / "ROADMAP.md").write_text("# Roadmap\n\n- ship it\n- more\n")
        code2, out2 = self._cli("publish", "confluence", "--file", "docs/ROADMAP.md", env=env)
        self.assertEqual(code2, 0)
        self.assertIn("Updated", out2)

        code3, out3 = self._cli("publish", "history", env=env)
        self.assertEqual(code3, 0)
        self.assertIn("created", out3)
        self.assertIn("updated", out3)


if __name__ == "__main__":
    unittest.main()
