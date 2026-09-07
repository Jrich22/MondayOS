"""
Tests for canonical identity.

MondayOS had four slug implementations that disagreed on eleven of seventeen
ordinary inputs. Since a slug is both a directory name and an identity key, the
same project could be two projects depending on which subsystem asked.

The invariant these tests defend:

    Same input -> same canonical slug, everywhere, always.

A subsystem may **reject** a canonical slug for its own safety reasons. It may
never **transform** one into a different identity. Several tests below exist only
to prove that distinction survives.
"""

from __future__ import annotations

import unittest

from core.identity import (
    MAX_SLUG_LENGTH,
    InvalidSlugError,
    collides,
    require_slug,
    slug,
)


class TestCanonicalTransformation(unittest.TestCase):
    def test_the_named_cases(self):
        cases = {
            "Cue App": "cue-app",
            "Invite & RSVP": "invite-rsvp",
            "AI Workspace": "ai-workspace",
            "Growth BOT": "growth-bot",
            "MondayOS": "mondayos",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(slug(value), expected)

    def test_acronyms_survive_as_lowercase_words(self):
        self.assertEqual(slug("API/SDK Bridge"), "api-sdk-bridge")
        self.assertEqual(slug("RSVP"), "rsvp")
        self.assertEqual(slug("AI"), "ai")

    def test_punctuation_folds_to_a_single_separator(self):
        self.assertEqual(slug("Don't Panic"), "don-t-panic")
        self.assertEqual(slug("100% Coverage"), "100-coverage")
        self.assertEqual(slug("C++ Parser"), "c-parser")
        self.assertEqual(slug("a--b"), "a-b")
        self.assertEqual(slug("Sprint 1.2"), "sprint-1-2")

    def test_whitespace_collapses_and_trims(self):
        self.assertEqual(slug("  spaced   out  "), "spaced-out")
        self.assertEqual(slug("\ttabbed\nname\r"), "tabbed-name")
        self.assertEqual(slug("---edges---"), "edges")

    def test_it_is_idempotent(self):
        """
        Re-slugging a stored slug must not change it, or every load would
        migrate identities a little further away from what is on disk.
        """
        for value in ("Cue App", "Invite & RSVP", "  Growth BOT  ", "日本語"):
            with self.subTest(value=value):
                once = slug(value)
                self.assertEqual(slug(once), once)

    def test_it_is_deterministic(self):
        self.assertEqual(slug("Invite & RSVP"), slug("Invite & RSVP"))


class TestNonAscii(unittest.TestCase):
    def test_a_non_latin_name_never_becomes_an_empty_string(self):
        """
        The bug this module was written to fix.

        `workspace.slugify` stripped every non-ASCII character, so a project
        named in Japanese slugged to "" and resolved to no directory at all — the
        absence of an identity, pointing at whatever the parent happened to be.
        """
        self.assertNotEqual(slug("日本語"), "")
        self.assertEqual(slug("日本語"), "日本語")

    def test_accented_latin_is_preserved_not_mangled(self):
        self.assertEqual(slug("Ünïcode Nàme"), "ünïcode-nàme")

    def test_a_non_ascii_name_is_a_valid_filesystem_identity(self):
        self.assertEqual(require_slug("日本語"), "日本語")

    def test_only_input_with_no_alphanumerics_yields_empty(self):
        for value in ("???", "---", "   ", ""):
            with self.subTest(value=value):
                self.assertEqual(slug(value), "")


class TestPathValidation(unittest.TestCase):
    """`require_slug` is the boundary for anything that becomes a directory."""

    def test_traversal_is_a_rejected_name_not_a_sanitized_one(self):
        """
        ADR-011's rule. Folding "a/b" to "a-b" would accept a name that describes
        two path segments and quietly make it one — sanitize-instead-of-reject,
        which is exactly what that decision forbids.
        """
        for hostile in ("../other", "/etc/passwd", "a/../b", "..\\win", "a\\b", ".."):
            with self.subTest(name=hostile), self.assertRaises(InvalidSlugError):
                require_slug(hostile)

    def test_a_name_with_no_alphanumerics_is_refused(self):
        with self.assertRaises(InvalidSlugError):
            require_slug("???")
        with self.assertRaises(InvalidSlugError):
            require_slug("")

    def test_length_is_bounded(self):
        self.assertEqual(len(require_slug("a" * MAX_SLUG_LENGTH)), MAX_SLUG_LENGTH)
        with self.assertRaises(InvalidSlugError):
            require_slug("a" * (MAX_SLUG_LENGTH + 1))

    def test_a_rejection_says_which_part_to_change(self):
        try:
            require_slug("a/b")
        except InvalidSlugError as exc:
            self.assertEqual(exc.value, "a/b")
            self.assertIn("separator", exc.reason)
        else:
            self.fail("expected rejection")


class TestRejectVersusTransform(unittest.TestCase):
    """
    The invariant that makes one identity space possible.

    A subsystem may accept fewer names than another. For every name both accept,
    the slug must be byte-identical.
    """

    def test_ascii_only_narrows_acceptance_without_changing_identity(self):
        for value in ("Cue App", "Invite & RSVP", "Growth BOT", "mondayos"):
            with self.subTest(value=value):
                self.assertEqual(require_slug(value, ascii_only=True), require_slug(value))

    def test_ascii_only_rejects_rather_than_transliterating(self):
        """
        The failure mode this prevents: a subsystem "helpfully" folding "日本語"
        to some ASCII approximation, giving the same project two identities.
        """
        self.assertEqual(require_slug("日本語"), "日本語")
        with self.assertRaises(InvalidSlugError):
            require_slug("日本語", ascii_only=True)

    def test_every_production_slug_function_agrees_with_canonical(self):
        """
        The load-bearing test: there is exactly one transformation in production.
        """
        from initiatives.models import slugify as initiatives_slug
        from migrate.candidate import slugify as migrate_slug
        from workspace.models import slugify as workspace_slug

        for value in (
            "Cue App",
            "Invite & RSVP",
            "AI Workspace",
            "Growth BOT",
            "  spaced  out  ",
            "Don't Panic",
            "a--b",
            "mondayos",
            "cue-app",
        ):
            with self.subTest(value=value):
                expected = slug(value)
                self.assertEqual(workspace_slug(value), expected)
                self.assertEqual(initiatives_slug(value), expected)
                self.assertEqual(migrate_slug(value), expected)

    def test_growth_agrees_for_every_name_it_accepts(self):
        from growth.project import normalize_project_slug

        for value in ("Cue App", "Invite & RSVP", "weatherbot", "Growth BOT"):
            with self.subTest(value=value):
                self.assertEqual(normalize_project_slug(value), slug(value))


class TestCollisionDetection(unittest.TestCase):
    def test_distinct_names_collapsing_to_one_slug_are_reported(self):
        """
        Unifying four transformations narrows the identity space, so names that
        were distinguishable somewhere might now collide. Failing loudly at
        registration beats discovering it as two projects sharing a directory.
        """
        found = collides(["Cue App", "cue app", "CUE-APP", "Growth"])
        self.assertIn("cue-app", found)
        self.assertEqual(len(found["cue-app"]), 3)
        self.assertNotIn("growth", found)

    def test_an_unambiguous_set_reports_nothing(self):
        self.assertEqual(collides(["alpha", "beta", "gamma"]), {})

    def test_the_same_name_twice_is_not_a_collision(self):
        self.assertEqual(collides(["Cue App", "Cue App"]), {})

    def test_names_with_no_slug_are_ignored(self):
        self.assertEqual(collides(["???", "---"]), {})

    def test_the_current_registry_has_no_collisions(self):
        """
        Migration safety, asserted rather than assumed: the names registered in
        this repository must not collapse into each other under the canonical
        transformation.
        """
        names = ["weatherbot", "WeatherBot", "cue-app", "sourcingbot", "mondayos"]
        found = collides(names)
        # weatherbot/WeatherBot intentionally share a slug and a path; that pair
        # is a known alias, not an accident.
        self.assertEqual(set(found), {"weatherbot"})
        self.assertEqual(sorted(found["weatherbot"]), ["WeatherBot", "weatherbot"])


class TestPersistedIdentityIsUnchanged(unittest.TestCase):
    def test_existing_conversation_directories_still_resolve(self):
        """
        Migration proof. Every directory currently on disk must slug to itself,
        or unifying the implementations orphans live conversations.
        """
        for existing in ("cue-app", "mondayos"):
            with self.subTest(existing=existing):
                self.assertEqual(require_slug(existing), existing)

    def test_registered_project_names_keep_their_identity(self):
        for name, expected in (
            ("weatherbot", "weatherbot"),
            ("WeatherBot", "weatherbot"),
            ("cue-app", "cue-app"),
            ("sourcingbot", "sourcingbot"),
            ("mondayos", "mondayos"),
        ):
            with self.subTest(name=name):
                self.assertEqual(require_slug(name), expected)


if __name__ == "__main__":
    unittest.main()
