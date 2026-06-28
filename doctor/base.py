"""BaseAnalyzer — the contract all analyzers implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from doctor.result import AnalyzerResult


class BaseAnalyzer(ABC):
    """
    Abstract base for all repository health analyzers.

    Subclasses set NAME and implement analyze(). The RepositoryInspector
    instantiates each subclass and calls analyze(), catching all exceptions
    so one bad analyzer never silences the others.
    """

    NAME: str  # Short identifier shown in output (e.g. "git", "tests")

    def __init__(self, project_root: Path, monday: Any = None) -> None:
        self._root = project_root
        self._monday = monday

    @abstractmethod
    def analyze(self) -> AnalyzerResult:
        """Run the analysis and return an AnalyzerResult. Must not raise."""
        ...
