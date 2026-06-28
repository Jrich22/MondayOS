"""ConfigAnalyzer — checks pyproject.toml, workflow YAML, and version alignment."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from doctor.base import BaseAnalyzer
from doctor.finding import Finding, Severity
from doctor.result import AnalyzerResult

_CATEGORY = "config"

_REQUIRED_PYPROJECT_SECTIONS = [
    "[project]",
    "[tool.pytest.ini_options]",
]


class ConfigAnalyzer(BaseAnalyzer):
    """
    Validates project configuration files.

    Checks:
      - pyproject.toml parse-validity (CRITICAL if malformed)
      - Required pyproject.toml sections present (WARNING if missing)
      - Python version requirement vs. running interpreter (WARNING if mismatch)
      - Workflow YAML files in workflows/definitions/ (WARNING per malformed file)
    """

    NAME = "config"

    def analyze(self) -> AnalyzerResult:
        start = time.monotonic()
        findings: list[Finding] = []

        findings.extend(_check_pyproject(self._root))
        findings.extend(_check_workflow_yamls(self._root))

        return AnalyzerResult(
            name=self.NAME,
            findings=findings,
            duration_ms=(time.monotonic() - start) * 1000,
        )


def _check_pyproject(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    path = root / "pyproject.toml"

    if not path.exists():
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.CRITICAL,
            title="pyproject.toml not found",
            recommendation="Add pyproject.toml with [project] and [build-system] sections.",
        ))
        return findings

    text = path.read_text(encoding="utf-8", errors="replace")

    # Parse validity
    try:
        import tomllib  # type: ignore[import]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import,no-redef]
        except ImportError:
            tomllib = None  # type: ignore[assignment]

    if tomllib is not None:
        try:
            tomllib.loads(text)
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title="pyproject.toml is valid TOML",
            ))
        except Exception as exc:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.CRITICAL,
                title="pyproject.toml is malformed TOML",
                detail=str(exc),
                recommendation="Fix TOML syntax errors in pyproject.toml.",
            ))
            return findings
    else:
        # Fallback: check that required markers are present in text
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="TOML parser unavailable — skipping pyproject.toml parse validation",
            recommendation="Install `tomli` for full TOML validation: pip install tomli",
        ))

    # Required sections
    missing_sections = [s for s in _REQUIRED_PYPROJECT_SECTIONS if s not in text]
    if missing_sections:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.WARNING,
            title=f"{len(missing_sections)} required section(s) missing from pyproject.toml",
            detail="\n".join(f"  {s}" for s in missing_sections),
            recommendation="Add the missing sections to pyproject.toml.",
            data={"missing_sections": missing_sections},
        ))
    else:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.OK,
            title="All required pyproject.toml sections present",
        ))

    # Python version alignment
    requires = _extract_requires_python(text)
    if requires:
        running = f"{sys.version_info.major}.{sys.version_info.minor}"
        # Simple check: does the running version satisfy requires-python?
        compatible = _version_satisfied(sys.version_info, requires)
        if compatible:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.OK,
                title=f"Running Python {running} satisfies requires-python: {requires}",
                data={"requires_python": requires, "running": running},
            ))
        else:
            findings.append(Finding(
                category=_CATEGORY,
                severity=Severity.WARNING,
                title=(
                    f"Running Python {running} may not satisfy "
                    f"requires-python: {requires}"
                ),
                recommendation=f"Use a Python version that satisfies `{requires}`.",
                data={"requires_python": requires, "running": running},
            ))

    return findings


def _check_workflow_yamls(root: Path) -> list[Finding]:
    definitions_dir = root / "workflows" / "definitions"
    if not definitions_dir.exists():
        return [Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="No workflow definitions directory found",
            recommendation="Create workflows/definitions/ and add YAML workflow files.",
        )]

    yaml_files = list(definitions_dir.glob("*.yaml")) + list(definitions_dir.glob("*.yml"))
    if not yaml_files:
        return [Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="No workflow YAML files found in workflows/definitions/",
            recommendation="Add YAML workflow definitions to workflows/definitions/.",
        )]

    try:
        import yaml
    except ImportError:
        return [Finding(
            category=_CATEGORY,
            severity=Severity.INFO,
            title="PyYAML not available — skipping workflow YAML validation",
        )]

    findings: list[Finding] = []
    malformed: list[str] = []
    for yaml_path in yaml_files:
        try:
            text = yaml_path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if not isinstance(data, dict):
                raise ValueError("Root must be a mapping")
            required_keys = {"name", "version", "steps"}
            missing = required_keys - set(data.keys())
            if missing:
                raise ValueError(f"Missing required keys: {missing}")
        except Exception as exc:
            rel = yaml_path.relative_to(root)
            malformed.append(f"{rel}: {exc}")

    if malformed:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.WARNING,
            title=f"{len(malformed)} malformed workflow YAML file(s)",
            detail="\n".join(f"  {m}" for m in malformed),
            recommendation="Fix YAML syntax or missing required fields (name, version, steps).",
            data={"malformed": malformed},
        ))
    else:
        findings.append(Finding(
            category=_CATEGORY,
            severity=Severity.OK,
            title=f"All {len(yaml_files)} workflow YAML file(s) valid",
            data={"workflow_files": len(yaml_files)},
        ))

    return findings


def _extract_requires_python(toml_text: str) -> str:
    """Extract requires-python value from raw TOML text (simple regex fallback)."""
    import re
    m = re.search(r'requires-python\s*=\s*"([^"]+)"', toml_text)
    return m.group(1) if m else ""


def _version_satisfied(version_info: tuple, requires: str) -> bool:
    """
    Simple version constraint check. Supports >=X.Y and ==X.Y only.
    Returns True when uncertain (e.g. complex specifiers).
    """
    import re
    m = re.match(r"^(>=|==|>)(\d+)\.(\d+)", requires.strip())
    if not m:
        return True  # unknown constraint — assume satisfied
    op, major, minor = m.group(1), int(m.group(2)), int(m.group(3))
    v = (version_info.major, version_info.minor)
    req = (major, minor)
    if op == ">=":
        return v >= req
    if op == ">":
        return v > req
    if op == "==":
        return v == req
    return True
