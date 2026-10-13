"""Tests for Project Registry, Monday.project(), Monday.onboard(), and CLI (Initiative 010)."""
from __future__ import annotations

import io
import json
import shutil
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

import core.project as project_module
from core.project import (
    ProjectAlreadyExistsError,
    ProjectEntry,
    ProjectNotFoundError,
    ProjectRegistry,
    ProjectRegistryError,
)
from monday.types import OnboardResponse, ProjectResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_monday(tmp_path: Path):
    from monday import Monday, MondayConfig
    return Monday(MondayConfig(project_root=tmp_path))


def _make_registry(tmp_path: Path) -> ProjectRegistry:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return ProjectRegistry(config_dir)


def _fake_project(tmp_path: Path, name: str = "myproject") -> Path:
    """Create a minimal fake project directory."""
    proj = tmp_path / name
    proj.mkdir(parents=True, exist_ok=True)
    return proj


# ===========================================================================
# TestProjectEntry
# ===========================================================================

class TestProjectEntry:
    def test_to_dict_round_trips(self):
        entry = ProjectEntry(
            name="weatherbot",
            source_path="/path/to/wb",
            description="Weather CLI",
            registered_at="2026-06-28T00:00:00+00:00",
        )
        d = entry.to_dict()
        assert d["name"] == "weatherbot"
        assert d["source_path"] == "/path/to/wb"
        assert d["description"] == "Weather CLI"

    def test_from_dict(self):
        d = {
            "name": "weatherbot",
            "source_path": "/path/to/wb",
            "description": "Weather CLI",
            "registered_at": "2026-06-28T00:00:00+00:00",
        }
        entry = ProjectEntry.from_dict(d)
        assert entry.name == "weatherbot"
        assert entry.source_path == "/path/to/wb"

    def test_path_property(self):
        entry = ProjectEntry(name="x", source_path="/tmp/x", description="", registered_at="")
        assert entry.path == Path("/tmp/x")

    def test_missing_description_defaults_empty(self):
        entry = ProjectEntry.from_dict({"name": "x", "source_path": "/p", "registered_at": ""})
        assert entry.description == ""

    def test_legacy_entry_defaults_delivery_binding_and_snapshot(self):
        entry = ProjectEntry.from_dict(
            {"name": "x", "source_path": "/p", "registered_at": ""}
        )
        assert entry.delivery_repository == ""
        assert entry.source_device is None
        assert entry.source_inode is None


# ===========================================================================
# TestProjectRegistry
# ===========================================================================

class TestProjectRegistry:
    def test_register_and_get(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        entry = registry.register("weatherbot", proj)
        assert entry.name == "weatherbot"
        assert Path(entry.source_path) == proj.resolve()

    def test_register_stores_to_file(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj)
        data = json.loads((tmp_path / "config" / "projects.json").read_text())
        assert "weatherbot" in data

    def test_register_pins_delivery_repository_and_filesystem_identity(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        entry = registry.register(
            "weatherbot", proj, delivery_repository="Acme/WeatherBot"
        )

        path_stat = proj.stat()
        assert entry.delivery_repository == "acme/weatherbot"
        assert entry.source_device == path_stat.st_dev
        assert entry.source_inode == path_stat.st_ino
        assert registry.resolve("WEATHERBOT") == entry

    def test_register_stores_canonical_source_path(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        noncanonical = proj / ".." / "wb"
        registry = _make_registry(tmp_path)

        entry = registry.register("weatherbot", noncanonical)

        assert entry.source_path == str(proj.resolve())

    def test_register_duplicate_raises(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj)
        with pytest.raises(ProjectAlreadyExistsError):
            registry.register("weatherbot", proj)

    def test_register_overwrite_replaces(self, tmp_path):
        proj1 = _fake_project(tmp_path, "wb1")
        proj2 = _fake_project(tmp_path, "wb2")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj1)
        entry = registry.register("weatherbot", proj2, overwrite=True)
        assert Path(entry.source_path) == proj2.resolve()

    def test_delivery_enabled_project_cannot_be_retargeted_by_overwrite(self, tmp_path):
        original = _fake_project(tmp_path, "original")
        replacement = _fake_project(tmp_path, "replacement")
        registry = _make_registry(tmp_path)
        registry.register(
            "weatherbot", original, delivery_repository="acme/weatherbot"
        )

        with pytest.raises(ProjectAlreadyExistsError, match="immutable target"):
            registry.register(
                "weatherbot",
                replacement,
                delivery_repository="acme/replacement",
                overwrite=True,
            )

    def test_delivery_enabled_project_allows_identical_overwrite(self, tmp_path):
        project = _fake_project(tmp_path, "weatherbot")
        registry = _make_registry(tmp_path)
        original = registry.register(
            "weatherbot", project, delivery_repository="acme/weatherbot"
        )

        updated = registry.register(
            "weatherbot",
            project,
            description="Updated description",
            delivery_repository="ACME/WeatherBot",
            overwrite=True,
        )

        assert updated.source_path == original.source_path
        assert updated.delivery_repository == original.delivery_repository
        assert updated.source_device == original.source_device
        assert updated.source_inode == original.source_inode
        assert updated.description == "Updated description"

    def test_register_missing_path_raises(self, tmp_path):
        registry = _make_registry(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            registry.register("x", tmp_path / "nonexistent")

    def test_register_file_path_raises(self, tmp_path):
        source_file = tmp_path / "not-a-directory"
        source_file.write_text("data", encoding="utf-8")
        registry = _make_registry(tmp_path)

        with pytest.raises(ValueError, match="not a directory"):
            registry.register("x", source_file)

    def test_register_symlink_path_raises(self, tmp_path):
        proj = _fake_project(tmp_path, "real")
        linked = tmp_path / "linked"
        linked.symlink_to(proj, target_is_directory=True)
        registry = _make_registry(tmp_path)

        with pytest.raises(ValueError, match="must not be a symlink"):
            registry.register("x", linked)

    @pytest.mark.parametrize(
        "repository",
        [
            "https://github.com/acme/weather",
            "acme/weather.git",
            "acme/weather/extra",
            " acme/weather",
            "-acme/weather",
            "ac--me/weather",
            "acme/.weather",
            "acme/",
        ],
    )
    def test_register_rejects_noncanonical_delivery_repository(self, tmp_path, repository):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)

        with pytest.raises(ValueError, match="delivery_repository"):
            registry.register("weatherbot", proj, delivery_repository=repository)

    def test_delivery_projects_cannot_share_source_path(self, tmp_path):
        proj = _fake_project(tmp_path, "shared")
        registry = _make_registry(tmp_path)
        registry.register("alpha", proj, delivery_repository="acme/alpha")

        with pytest.raises(ProjectAlreadyExistsError, match="source path"):
            registry.register("beta", proj, delivery_repository="acme/beta")

    def test_delivery_projects_cannot_share_repository(self, tmp_path):
        alpha = _fake_project(tmp_path, "alpha")
        beta = _fake_project(tmp_path, "beta")
        registry = _make_registry(tmp_path)
        registry.register("alpha", alpha, delivery_repository="Acme/Product")

        with pytest.raises(ProjectAlreadyExistsError, match="already pinned"):
            registry.register("beta", beta, delivery_repository="acme/product")

    def test_non_delivery_projects_may_retain_same_path_aliases(self, tmp_path):
        proj = _fake_project(tmp_path, "shared")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj)
        registry.register("WeatherBot", proj)

        assert len(registry.list()) == 2

    def test_delivery_enrollment_rejects_existing_canonical_alias(self, tmp_path):
        proj = _fake_project(tmp_path, "shared")
        registry = _make_registry(tmp_path)
        registry.register("Weather Bot", proj)

        with pytest.raises(ProjectAlreadyExistsError, match="exactly one name"):
            registry.register(
                "weather-bot",
                proj,
                delivery_repository="acme/weather-bot",
            )

    def test_analysis_alias_cannot_make_delivery_identity_ambiguous(self, tmp_path):
        proj = _fake_project(tmp_path, "shared")
        registry = _make_registry(tmp_path)
        registry.register(
            "Weather Bot",
            proj,
            delivery_repository="acme/weather-bot",
        )

        with pytest.raises(ProjectAlreadyExistsError, match="exactly one name"):
            registry.register("weather-bot", proj)

    def test_delivery_project_identity_must_be_ascii(self, tmp_path):
        proj = _fake_project(tmp_path, "shared")
        registry = _make_registry(tmp_path)

        with pytest.raises(ValueError, match="must use ASCII"):
            registry.register(
                "天気",
                proj,
                delivery_repository="acme/weather-bot",
            )

    @pytest.mark.parametrize("name", ["mondayos", "MondayOS", "MONDAYOS"])
    def test_controller_identity_cannot_be_registered_as_an_external_project(
        self, tmp_path, name
    ):
        proj = _fake_project(tmp_path, "shared")
        registry = _make_registry(tmp_path)

        with pytest.raises(ValueError, match="reserved"):
            registry.register(name, proj)

    def test_get_not_found_raises(self, tmp_path):
        registry = _make_registry(tmp_path)
        with pytest.raises(ProjectNotFoundError):
            registry.get("nonexistent")

    def test_get_and_remove_accept_the_canonical_project_identity(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("Weather Portal", proj)

        assert registry.get("weather-portal").name == "Weather Portal"
        registry.remove("WEATHER_PORTAL")
        assert registry.list() == []

    def test_list_empty(self, tmp_path):
        registry = _make_registry(tmp_path)
        assert registry.list() == []

    def test_list_multiple(self, tmp_path):
        registry = _make_registry(tmp_path)
        for name in ["alpha", "beta", "gamma"]:
            proj = _fake_project(tmp_path, name)
            registry.register(name, proj)
        entries = registry.list()
        assert len(entries) == 3
        assert {e.name for e in entries} == {"alpha", "beta", "gamma"}

    def test_remove_existing(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj)
        registry.remove("weatherbot")
        assert not registry.exists("weatherbot")

    def test_remove_not_found_raises(self, tmp_path):
        registry = _make_registry(tmp_path)
        with pytest.raises(ProjectNotFoundError):
            registry.remove("nonexistent")

    def test_exists_true(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj)
        assert registry.exists("weatherbot") is True

    def test_exists_false(self, tmp_path):
        registry = _make_registry(tmp_path)
        assert registry.exists("missing") is False

    def test_description_stored(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj, description="A weather tool")
        entry = registry.get("weatherbot")
        assert entry.description == "A weather tool"

    def test_registry_persists_across_instances(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        reg1 = ProjectRegistry(config_dir)
        reg1.register("weatherbot", proj)
        reg2 = ProjectRegistry(config_dir)
        assert reg2.exists("weatherbot")

    def test_resolve_uses_canonical_name(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("Weather Bot", proj, delivery_repository="acme/weather")

        assert registry.resolve("weather_bot").name == "Weather Bot"

    def test_resolve_rejects_ambiguous_alias_even_on_same_path(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj)
        registry.register("WeatherBot", proj)

        with pytest.raises(ProjectRegistryError, match="ambiguous"):
            registry.resolve("weatherbot")

    def test_resolve_rejects_unsafe_hand_edited_alias(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        _make_registry(tmp_path)
        registry_path = tmp_path / "config" / "projects.json"
        registry_path.write_text(
            json.dumps(
                {
                    "weather/bot": {
                        "name": "weather/bot",
                        "source_path": str(proj.resolve()),
                    }
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ProjectRegistryError, match="unsafe canonical alias"):
            _make_registry(tmp_path).resolve("weather-bot")

    def test_resolve_rejects_replaced_directory_identity(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj, delivery_repository="acme/weather")
        proj.rmdir()
        proj.mkdir()

        with pytest.raises(ProjectRegistryError, match="identity changed"):
            registry.resolve("weatherbot")

    def test_resolve_rejects_source_path_replaced_by_symlink(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        replacement = _fake_project(tmp_path, "other")
        registry = _make_registry(tmp_path)
        registry.register("weatherbot", proj, delivery_repository="acme/weather")
        proj.rmdir()
        proj.symlink_to(replacement, target_is_directory=True)

        with pytest.raises(ProjectRegistryError, match="became a symlink"):
            registry.resolve("weatherbot")

    def test_resolve_supports_legacy_entry_without_snapshot(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        _make_registry(tmp_path)
        registry_path = tmp_path / "config" / "projects.json"
        registry_path.write_text(
            json.dumps(
                {
                    "weatherbot": {
                        "name": "weatherbot",
                        "source_path": str(proj.resolve()),
                    }
                }
            ),
            encoding="utf-8",
        )

        entry = _make_registry(tmp_path).resolve("WeatherBot")

        assert entry.source_device is None
        assert entry.source_inode is None

    @pytest.mark.parametrize(
        "contents",
        [
            "{not-json",
            "[]",
            '{"weatherbot": []}',
            '{"weatherbot": {}}',
        ],
    )
    def test_registry_corruption_fails_closed(self, tmp_path, contents):
        _make_registry(tmp_path)
        registry_path = tmp_path / "config" / "projects.json"
        registry_path.write_text(contents, encoding="utf-8")

        with pytest.raises(ProjectRegistryError):
            _make_registry(tmp_path).list()

    def test_registry_symlink_fails_closed(self, tmp_path):
        real_registry = tmp_path / "real-projects.json"
        real_registry.write_text("{}", encoding="utf-8")
        _make_registry(tmp_path)
        registry_path = tmp_path / "config" / "projects.json"
        registry_path.symlink_to(real_registry)

        with pytest.raises(ProjectRegistryError, match="must not be a symlink"):
            _make_registry(tmp_path).list()

    def test_registry_mutation_lock_symlink_fails_closed(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        unrelated = tmp_path / "unrelated-lock-target"
        unrelated.write_text("unchanged", encoding="utf-8")
        (tmp_path / "config" / "projects.json.lock").symlink_to(unrelated)

        with pytest.raises(ProjectRegistryError, match="lock safely"):
            registry.register("weatherbot", proj)

        assert unrelated.read_text(encoding="utf-8") == "unchanged"

    def test_registry_mutation_lock_hard_link_fails_closed(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        unrelated = tmp_path / "unrelated-lock-target"
        unrelated.write_text("unchanged", encoding="utf-8")
        (tmp_path / "config" / "projects.json.lock").hardlink_to(unrelated)

        with pytest.raises(ProjectRegistryError, match="controller-owned regular file"):
            registry.register("weatherbot", proj)

        assert unrelated.read_text(encoding="utf-8") == "unchanged"

    def test_registry_rejects_lock_replacement_while_acquiring(
        self, tmp_path, monkeypatch
    ):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        lock_path = tmp_path / "config" / "projects.json.lock"
        real_flock = project_module.fcntl.flock
        replaced = False

        def replace_before_lock(descriptor, operation):
            nonlocal replaced
            if operation == project_module.fcntl.LOCK_EX and not replaced:
                replaced = True
                lock_path.unlink()
                lock_path.write_text("replacement", encoding="utf-8")
            return real_flock(descriptor, operation)

        monkeypatch.setattr(project_module.fcntl, "flock", replace_before_lock)

        with pytest.raises(ProjectRegistryError, match="identity changed"):
            registry.register("weatherbot", proj)

        assert replaced

    def test_registry_directory_symlink_fails_closed(self, tmp_path):
        real_config = tmp_path / "real-config"
        real_config.mkdir()
        linked_config = tmp_path / "linked-config"
        linked_config.symlink_to(real_config, target_is_directory=True)

        with pytest.raises(ProjectRegistryError, match="must be a real directory"):
            ProjectRegistry(linked_config).list()

    def test_registry_group_writable_directory_fails_closed(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir(mode=0o700)
        config.chmod(0o770)

        with pytest.raises(ProjectRegistryError, match="group/world writable"):
            ProjectRegistry(config).list()

    def test_registry_non_private_lock_file_fails_closed(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        registry = _make_registry(tmp_path)
        lock_path = tmp_path / "config" / "projects.json.lock"
        lock_path.write_text("", encoding="utf-8")
        lock_path.chmod(0o640)

        with pytest.raises(ProjectRegistryError, match="controller-owned regular file"):
            registry.register("weatherbot", proj)

    def test_registry_key_name_mismatch_fails_closed(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        _make_registry(tmp_path)
        registry_path = tmp_path / "config" / "projects.json"
        registry_path.write_text(
            json.dumps(
                {
                    "weatherbot": {
                        "name": "different",
                        "source_path": str(proj.resolve()),
                    }
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ProjectRegistryError, match="does not match"):
            _make_registry(tmp_path).list()

    def test_atomic_save_failure_preserves_previous_registry(self, tmp_path):
        first = _fake_project(tmp_path, "first")
        second = _fake_project(tmp_path, "second")
        registry = _make_registry(tmp_path)
        registry.register("first", first)

        with patch("core.project.os.replace", side_effect=OSError("replace failed")):
            with pytest.raises(OSError, match="replace failed"):
                registry.register("second", second)

        assert registry.get("first").name == "first"
        assert registry.exists("second") is False
        assert not list((tmp_path / "config").glob(".projects.json.*.tmp"))

    def test_concurrent_registration_cannot_resurrect_completed_removal(
        self, tmp_path, monkeypatch
    ):
        protected = _fake_project(tmp_path, "protected")
        unrelated = _fake_project(tmp_path, "unrelated")
        registry = _make_registry(tmp_path)
        registry.register(
            "protected",
            protected,
            delivery_repository="acme/protected",
        )

        registering = ProjectRegistry(tmp_path / "config")
        removing = ProjectRegistry(tmp_path / "config")
        real_register_load = registering._load
        stale_snapshot_loaded = threading.Event()
        release_registration = threading.Event()
        removal_gate = threading.Barrier(2)
        removal_finished = threading.Event()
        failures: list[BaseException] = []

        def paused_register_load():
            snapshot = real_register_load()
            stale_snapshot_loaded.set()
            if not release_registration.wait(timeout=5):
                raise AssertionError("registration was not released by the test")
            return snapshot

        monkeypatch.setattr(registering, "_load", paused_register_load)

        def register_unrelated() -> None:
            try:
                registering.register("unrelated", unrelated)
            except BaseException as exc:
                failures.append(exc)

        def remove_protected() -> None:
            try:
                removal_gate.wait(timeout=5)
                removing.remove("protected")
            except BaseException as exc:
                failures.append(exc)
            finally:
                removal_finished.set()

        registration_thread = threading.Thread(target=register_unrelated)
        removal_thread = threading.Thread(target=remove_protected)
        registration_thread.start()
        try:
            assert stale_snapshot_loaded.wait(timeout=5)
            removal_thread.start()
            removal_gate.wait(timeout=5)

            # Before mutation locking, removal completed here while registration
            # retained its stale pre-removal snapshot. Releasing registration then
            # restored the revoked delivery entry. It must now remain blocked.
            assert not removal_finished.wait(timeout=1)
        finally:
            release_registration.set()
            registration_thread.join(timeout=5)
            if removal_thread.ident is not None:
                removal_thread.join(timeout=5)

        assert not registration_thread.is_alive()
        assert not removal_thread.is_alive()
        assert failures == []
        assert registry.exists("unrelated") is True
        assert registry.exists("protected") is False


# ===========================================================================
# TestMondayProject
# ===========================================================================

class TestMondayProject:
    def test_register_action(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        r = monday.project("register", name="weatherbot", path=str(proj))
        assert isinstance(r, ProjectResponse)
        assert r.success is True
        assert r.project_name == "weatherbot"

    def test_register_missing_name_fails(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.project("register", path=str(tmp_path))
        assert r.success is False

    def test_register_missing_path_fails(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.project("register", name="wb")
        assert r.success is False

    def test_register_preserves_the_existing_positional_overwrite_contract(
        self, tmp_path
    ):
        monday = _make_monday(tmp_path)
        first = _fake_project(tmp_path, "first")
        second = _fake_project(tmp_path, "second")
        assert monday.project("register", "weatherbot", str(first)).success

        response = monday.project(
            "register",
            "weatherbot",
            str(second),
            "Updated",
            True,
        )

        assert response.success is True
        assert response.data["source_path"] == str(second.resolve())
        assert response.data["description"] == "Updated"

    def test_list_action_empty(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.project("list")
        assert r.success is True
        assert r.data["count"] == 0

    def test_list_action_with_entries(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))
        r = monday.project("list")
        assert r.data["count"] == 1

    def test_get_action(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))
        r = monday.project("get", name="weatherbot")
        assert r.success is True
        assert r.data["name"] == "weatherbot"

    def test_get_and_remove_accept_canonical_identity(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        assert monday.project(
            "register", name="Weather Portal", path=str(proj)
        ).success

        assert monday.project("get", name="weather-portal").success
        assert monday.project("remove", name="WEATHER_PORTAL").success

    @pytest.mark.parametrize("action", ["list", "get", "remove"])
    def test_corrupt_registry_is_a_typed_project_failure(self, tmp_path, action):
        config = tmp_path / "config"
        config.mkdir(parents=True)
        (config / "projects.json").write_text("{not-json", encoding="utf-8")
        monday = _make_monday(tmp_path)

        response = monday.project(action, name="weatherbot")

        assert response.success is False
        assert "malformed" in response.message

    def test_get_not_found(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.project("get", name="missing")
        assert r.success is False

    def test_remove_action(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))
        r = monday.project("remove", name="weatherbot")
        assert r.success is True
        r2 = monday.project("get", name="weatherbot")
        assert r2.success is False

    def test_unknown_action(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.project("frobnicate")
        assert r.success is False
        assert "Unknown action" in r.message


# ===========================================================================
# TestMondayOnboard
# ===========================================================================

def _make_mock_migrate_resp():
    from monday.types import MigrateResponse
    return MigrateResponse(
        action="run", success=True, imported_count=7, skipped_count=1,
        failed_count=0, sources_processed=["changelog", "decisions", "roadmap"],
        message="7 imported, 1 skipped, 0 failed",
    )


def _make_mock_doctor_resp():
    from monday.types import DoctorResponse
    return DoctorResponse(
        action="inspect", success=True, health_score=72, grade="Good",
        summary="2 warning(s), 3 info",
        recommendations=["Add README.md", "Increase test coverage"],
        data={"analyzers": [], "health_score": 72, "grade": "Good",
              "recommendations": ["Add README.md"]},
    )


def _make_mock_advise_resp():
    from monday.types import AdviseResponse
    return AdviseResponse(
        action="advise", success=True, confidence=0.55,
        sprint_goal="Establish knowledge foundation",
        risks=[{"title": "No test entries", "severity": "low", "category": "knowledge",
                "impact": "Test queries return nothing", "recommendation": "Run monday learn",
                "source": "knowledge"}],
        next_actions=[{"title": "Import knowledge base", "priority": 1,
                       "category": "knowledge", "rationale": "Empty KB",
                       "effort": "minutes", "command": "monday migrate"}],
        repository_summary="WeatherBot is a Python weather CLI.",
        data={
            "repository_summary": "WeatherBot is a Python weather CLI.",
            "sprint_goal": "Establish knowledge foundation",
            "sprint_rationale": "The knowledge base is empty.",
            "confidence": 0.55,
            "health_score": 72,
            "health_grade": "Good",
            "risks": [],
            "next_actions": [],
            "technical_debt_summary": "Minor debt.",
            "debt_items": ["TODO markers"],
            "knowledge_gaps": ["No sprint entries"],
            "documentation_gaps": ["Missing README.md"],
            "data_sources": ["doctor"],
        },
    )


class TestMondayOnboard:
    def test_onboard_unknown_project_fails(self, tmp_path):
        monday = _make_monday(tmp_path)
        r = monday.onboard("nonexistent")
        assert isinstance(r, OnboardResponse)
        assert r.success is False
        assert "not registered" in r.message.lower() or "nonexistent" in r.message

    def test_onboard_missing_source_path_fails(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))
        # Delete the project directory to simulate a vanished path
        shutil.rmtree(proj)
        r = monday.onboard("weatherbot")
        assert r.success is False
        assert "no longer exists" in r.message.lower()

    def test_onboard_success(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))

        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            r = monday.onboard("weatherbot")

        assert r.success is True
        assert r.project_name == "weatherbot"
        assert r.health_score == 72
        assert r.grade == "Good"
        assert r.confidence == 0.55
        assert r.sprint_goal == "Establish knowledge foundation"

    def test_onboard_report_file_written(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))

        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            r = monday.onboard("weatherbot")

        assert r.report_path
        report_path = Path(r.report_path)
        assert report_path.exists()
        content = report_path.read_text()
        assert "Weatherbot" in content or "weatherbot" in content.lower()

    def test_onboard_report_contains_all_sections(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))

        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            r = monday.onboard("weatherbot")

        content = Path(r.report_path).read_text()
        required_sections = [
            "Executive Summary",
            "What MondayOS Knows",
            "Documentation Inventory",
            "Knowledge Gaps",
            "Engineering Risks",
            "Health Report",
            "What to Build Next",
            "Technical Debt",
            "Recommended Tasks",
        ]
        for section in required_sections:
            assert section in content, f"Missing section: {section}"

    def test_onboard_custom_reports_dir(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))
        custom_dir = tmp_path / "my_reports"

        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            r = monday.onboard("weatherbot", reports_dir=custom_dir)

        assert Path(r.report_path).parent == custom_dir

    def test_onboard_report_io_failure_returns_typed_response(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))
        reports_file = tmp_path / "not_a_directory"
        reports_file.write_text("occupied", encoding="utf-8")

        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            response = monday.onboard("weatherbot", reports_dir=reports_file)

        assert not response.success
        assert response.action == "onboard"
        assert response.project_name == "weatherbot"
        assert not response.report_path

    def test_onboard_pipeline_failure_returns_typed_response(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))

        with patch("monday.api.Monday.migrate", side_effect=OSError("scan failed")):
            response = monday.onboard("weatherbot")

        assert not response.success
        assert response.action == "onboard"
        assert response.project_name == "weatherbot"
        assert response.message == "scan failed"

    def test_onboard_data_contains_all_subsystems(self, tmp_path):
        monday = _make_monday(tmp_path)
        proj = _fake_project(tmp_path, "wb")
        monday.project("register", name="weatherbot", path=str(proj))

        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            r = monday.onboard("weatherbot")

        assert "migrate" in r.data
        assert "doctor" in r.data
        assert "advisory" in r.data


# ===========================================================================
# TestCLIProject
# ===========================================================================

class TestCLIProject:
    def _run(self, args: list[str]) -> tuple[int, str, str]:
        from monday.cli import main

        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = main(args)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return rc, out.getvalue(), err.getvalue()

    def test_project_register(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        rc, out, _ = self._run([
            "--project-root", str(tmp_path),
            "project", "register", "weatherbot", str(proj),
            "--description", "Weather CLI",
        ])
        assert rc == 0
        assert "weatherbot" in out

    def test_project_list_empty(self, tmp_path):
        rc, out, _ = self._run(["--project-root", str(tmp_path), "project", "list"])
        assert rc == 0
        assert "No projects" in out

    def test_project_list_with_entries(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        self._run(["--project-root", str(tmp_path), "project", "register", "wb", str(proj)])
        rc, out, _ = self._run(["--project-root", str(tmp_path), "project", "list"])
        assert rc == 0
        assert "wb" in out

    def test_project_list_reports_a_corrupt_registry_as_an_error(self, tmp_path):
        config = tmp_path / "config"
        config.mkdir(parents=True)
        (config / "projects.json").write_text("{not-json", encoding="utf-8")

        rc, _out, err = self._run(
            ["--project-root", str(tmp_path), "project", "list"]
        )

        assert rc == 1
        assert "Error" in err

    def test_project_get_found(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        self._run(["--project-root", str(tmp_path), "project", "register", "weatherbot", str(proj)])
        rc, out, _ = self._run(["--project-root", str(tmp_path), "project", "get", "weatherbot"])
        assert rc == 0
        assert "weatherbot" in out

    def test_project_get_not_found(self, tmp_path):
        rc, out, err = self._run(["--project-root", str(tmp_path), "project", "get", "missing"])
        assert rc == 1
        assert "Error" in err or "missing" in err

    def test_project_remove(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        self._run(["--project-root", str(tmp_path), "project", "register", "weatherbot", str(proj)])
        rc, out, _ = self._run(["--project-root", str(tmp_path), "project", "remove", "weatherbot"])
        assert rc == 0
        assert "removed" in out.lower()


# ===========================================================================
# TestCLIOnboard
# ===========================================================================

class TestCLIOnboard:
    def _run(self, args: list[str]) -> tuple[int, str, str]:
        from monday.cli import main

        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = main(args)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return rc, out.getvalue(), err.getvalue()

    def test_onboard_unknown_project(self, tmp_path):
        rc, out, err = self._run(["--project-root", str(tmp_path), "onboard", "missing"])
        assert rc == 1

    def test_onboard_success(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        self._run([
            "--project-root", str(tmp_path),
            "project", "register", "weatherbot", str(proj),
        ])
        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            rc, out, _ = self._run(["--project-root", str(tmp_path), "onboard", "weatherbot"])
        assert rc == 0
        assert "ONBOARDING COMPLETE" in out

    def test_onboard_shows_report_path(self, tmp_path):
        proj = _fake_project(tmp_path, "wb")
        self._run([
            "--project-root", str(tmp_path),
            "project", "register", "weatherbot", str(proj),
        ])
        with patch("monday.api.Monday.migrate", return_value=_make_mock_migrate_resp()), \
             patch("monday.api.Monday.doctor", return_value=_make_mock_doctor_resp()), \
             patch("monday.api.Monday.advise", return_value=_make_mock_advise_resp()):
            rc, out, _ = self._run(["--project-root", str(tmp_path), "onboard", "weatherbot"])
        assert "Report" in out or "ONBOARDING_REPORT" in out
