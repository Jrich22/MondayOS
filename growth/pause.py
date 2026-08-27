"""
Pause controls for Growth publishing.

Four scopes, evaluated outermost first: a global emergency stop, then project,
platform, and post. The emergency stop is deliberately the first gate in the
dispatcher and overrides everything, including an explicit operator publish -
that is what makes it an emergency control rather than a preference.

Scope placement follows the isolation boundary (ADR-011). Project, platform, and
post pauses live inside the workspace they govern, so one project cannot read or
change another's controls. Only the emergency stop sits above the workspaces,
and it holds a flag and a reason - no project data, no content, no account.

A paused item is never mutated: pausing holds publication, it does not cancel,
edit, or unapprove anything. Resuming restores exactly the prior state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PAUSES_FILENAME = "pauses.json"
_EMERGENCY_FILENAME = "emergency_stop.json"

# Scope names, outermost first. The dispatcher evaluates them in this order.
SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
SCOPE_PLATFORM = "platform"
SCOPE_POST = "post"

SCOPES: tuple[str, ...] = (SCOPE_GLOBAL, SCOPE_PROJECT, SCOPE_PLATFORM, SCOPE_POST)


@dataclass(frozen=True)
class PauseState:
    """Whether publication is held, and by which scope."""

    paused: bool
    scope: str = ""
    reason: str = ""
    since: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "scope": self.scope,
            "reason": self.reason,
            "since": self.since,
        }

    def describe(self) -> str:
        """One line an operator can act on."""
        if not self.paused:
            return "Not paused."
        if self.scope == SCOPE_GLOBAL:
            return (
                "EMERGENCY STOP is active portfolio-wide"
                + (f": {self.reason}" if self.reason else "")
                + ". Nothing publishes until it is cleared."
            )
        return (
            f"Publishing is paused at the {self.scope} scope"
            + (f": {self.reason}" if self.reason else "")
            + "."
        )


class PauseController:
    """
    Reads and writes the pause state for one project, plus the global stop.

    Constructed with both the MondayOS root (for the emergency stop) and one
    workspace directory (for everything else), so a controller can only ever
    change the project it was opened for.
    """

    def __init__(self, project_root: Path, workspace_dir: Path) -> None:
        self._emergency_path = Path(project_root) / "growth" / _EMERGENCY_FILENAME
        self._workspace_dir = Path(workspace_dir)
        self._pauses_path = self._workspace_dir / _PAUSES_FILENAME

    # ------------------------------------------------------------------
    # Emergency stop (portfolio-wide)
    # ------------------------------------------------------------------

    def emergency_stop(self) -> PauseState:
        """Current global stop state."""
        data = _read_json(self._emergency_path)
        if not data.get("active"):
            return PauseState(paused=False)
        return PauseState(
            paused=True,
            scope=SCOPE_GLOBAL,
            reason=str(data.get("reason", "")),
            since=str(data.get("since", "")),
        )

    def set_emergency_stop(self, active: bool, reason: str = "") -> PauseState:
        """Engage or clear the portfolio-wide stop."""
        if not active:
            _write_json(self._emergency_path, {"active": False, "reason": "", "since": ""})
            return PauseState(paused=False)
        payload = {"active": True, "reason": reason, "since": _now()}
        _write_json(self._emergency_path, payload)
        return PauseState(
            paused=True, scope=SCOPE_GLOBAL, reason=reason, since=str(payload["since"])
        )

    # ------------------------------------------------------------------
    # Workspace-scoped pauses
    # ------------------------------------------------------------------

    def set_pause(
        self,
        scope: str,
        active: bool,
        target: str = "",
        reason: str = "",
    ) -> PauseState:
        """
        Pause or resume one scope within this workspace.

        ``target`` is the platform slug for a platform pause and the content id for
        a post pause; it is ignored for a project pause.
        """
        if scope == SCOPE_GLOBAL:
            return self.set_emergency_stop(active, reason)
        if scope not in (SCOPE_PROJECT, SCOPE_PLATFORM, SCOPE_POST):
            raise ValueError(f"Unknown pause scope {scope!r}. Valid: {', '.join(SCOPES)}")
        if scope in (SCOPE_PLATFORM, SCOPE_POST) and not target:
            raise ValueError(f"A {scope} pause requires a target.")

        data = _read_json(self._pauses_path)
        key = scope if scope == SCOPE_PROJECT else f"{scope}:{target}"
        if active:
            data[key] = {"reason": reason, "since": _now()}
        else:
            data.pop(key, None)
        _write_json(self._pauses_path, data)
        return PauseState(
            paused=active,
            scope=scope if active else "",
            reason=reason if active else "",
            since=str(data.get(key, {}).get("since", "")) if active else "",
        )

    def list_pauses(self) -> dict[str, Any]:
        """Every active pause visible to this workspace, including the global stop."""
        rows: dict[str, Any] = dict(_read_json(self._pauses_path))
        stop = self.emergency_stop()
        if stop.paused:
            rows[SCOPE_GLOBAL] = {"reason": stop.reason, "since": stop.since}
        return rows

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, platform: str = "", content_id: str = "") -> PauseState:
        """
        Resolve the effective pause state, outermost scope first.

        Order matters: the emergency stop must win even when a narrower scope is
        clear, and the scope reported back is the one an operator has to clear.
        """
        stop = self.emergency_stop()
        if stop.paused:
            return stop

        data = _read_json(self._pauses_path)

        entry = data.get(SCOPE_PROJECT)
        if isinstance(entry, dict):
            return PauseState(
                paused=True,
                scope=SCOPE_PROJECT,
                reason=str(entry.get("reason", "")),
                since=str(entry.get("since", "")),
            )

        if platform:
            entry = data.get(f"{SCOPE_PLATFORM}:{platform}")
            if isinstance(entry, dict):
                return PauseState(
                    paused=True,
                    scope=SCOPE_PLATFORM,
                    reason=str(entry.get("reason", "")),
                    since=str(entry.get("since", "")),
                )

        if content_id:
            entry = data.get(f"{SCOPE_POST}:{content_id}")
            if isinstance(entry, dict):
                return PauseState(
                    paused=True,
                    scope=SCOPE_POST,
                    reason=str(entry.get("reason", "")),
                    since=str(entry.get("since", "")),
                )

        return PauseState(paused=False)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # An unreadable control file must not be read as "not paused"; the caller
        # cannot distinguish that here, so fail loud rather than silently open.
        raise
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
