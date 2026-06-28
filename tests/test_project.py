"""Tests for Project Registry, Monday.project(), Monday.onboard(), and CLI (Initiative 010)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monday.project import (
    ProjectAlreadyExistsError,
    ProjectEntry,
    ProjectNotFoundError,
    ProjectRegistry,
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

    def test_register_missing_path_raises(self, tmp_path):
        registry = _make_registry(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            registry.register("x", tmp_path / "nonexistent")

    def test_get_not_found_raises(self, tmp_path):
        registry = _make_registry(tmp_path)
        with pytest.raises(ProjectNotFoundError):
            registry.get("nonexistent")

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
        import shutil; shutil.rmtree(proj)
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
        import io
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
        import io
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
