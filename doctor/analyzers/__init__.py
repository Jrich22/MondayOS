"""Pluggable repository health analyzers."""
from doctor.analyzers.code_quality import CodeQualityAnalyzer
from doctor.analyzers.config import ConfigAnalyzer
from doctor.analyzers.documentation import DocumentationAnalyzer
from doctor.analyzers.git import GitAnalyzer
from doctor.analyzers.knowledge_health import KnowledgeHealthAnalyzer
from doctor.analyzers.task_health import TaskHealthAnalyzer
from doctor.analyzers.tests import TestAnalyzer

__all__ = [
    "GitAnalyzer",
    "TestAnalyzer",
    "CodeQualityAnalyzer",
    "KnowledgeHealthAnalyzer",
    "DocumentationAnalyzer",
    "TaskHealthAnalyzer",
    "ConfigAnalyzer",
]
