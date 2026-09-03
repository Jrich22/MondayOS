"""
Domain types for project intelligence.

The whole subsystem exists to answer questions *from the project* — what is being
built, why a decision was made, where something lives — and to show its working.
Two commitments shape every type here.

**Deterministic.** No embeddings, no vector store, no model in the indexing or
retrieval path. Identical project state produces an identical index and identical
answers. That is not a purity preference: retrieval that cannot be explained
cannot be trusted, and an answer that cannot name its source is
indistinguishable from one that was invented.

**Derived.** The index is a cache, never a system of record. Every fact in it is
recoverable by re-reading the project, and deleting it costs a rebuild and
nothing else. Tasks live in the TaskManager, knowledge in the KnowledgeStore,
history in git — this subsystem reads them and builds no second copy that could
disagree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FileKind(Enum):
    """
    What a file is, for retrieval purposes.

    Coarser than a language classification on purpose: "is this a decision
    record or a test" is the question that changes how an answer is assembled,
    and `.py` alone does not answer it.
    """

    SOURCE = "source"
    TEST = "test"
    DOCUMENTATION = "documentation"
    DECISION = "decision"
    CONFIG = "config"
    PROMPT = "prompt"
    OTHER = "other"


class SymbolKind(Enum):
    """A named thing a question can be about."""

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    PROTOCOL = "protocol"
    DATACLASS = "dataclass"
    ENUM = "enum"
    CONSTANT = "constant"
    TYPE = "type"


@dataclass(frozen=True)
class Symbol:
    """
    One named definition, and where to find it.

    ``line`` and ``end_line`` make a citation navigable — "workspace/service.py
    lines 82-140" is checkable, "workspace/service.py" is a hint.
    """

    name: str
    kind: SymbolKind
    path: str
    line: int
    end_line: int
    # The enclosing class for a method; empty otherwise.
    parent: str = ""
    signature: str = ""
    doc: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified": self.qualified,
            "kind": self.kind.value,
            "path": self.path,
            "line": self.line,
            "end_line": self.end_line,
            "parent": self.parent,
            "signature": self.signature,
            "doc": self.doc,
        }


@dataclass
class IndexedFile:
    """
    One file, with everything retrieval needs and nothing it does not.

    Content is deliberately **not** retained. The index stores where things are,
    not what they say: keeping 5MB of source in memory to answer a question that
    ends in "open this file" trades a lot of memory for nothing. Line-level
    excerpts are read from disk when an answer actually cites them.
    """

    path: str
    kind: FileKind
    size: int
    mtime: int
    lines: int
    digest: str
    symbols: list[Symbol] = field(default_factory=list)
    # Identifiers and words appearing in the file, lowercased and deduplicated.
    # This is the retrieval surface: a term index over real content, not over
    # filenames.
    terms: set[str] = field(default_factory=set)
    # Cross-references this file makes: ADR-017, TASK-0073, #39.
    references: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "size": self.size,
            "mtime": self.mtime,
            "lines": self.lines,
            "digest": self.digest,
            "symbols": [s.to_dict() for s in self.symbols],
            "terms": sorted(self.terms),
            "references": sorted(self.references),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndexedFile:
        return cls(
            path=str(data["path"]),
            kind=FileKind(str(data.get("kind", "other"))),
            size=int(data.get("size", 0)),
            mtime=int(data.get("mtime", 0)),
            lines=int(data.get("lines", 0)),
            digest=str(data.get("digest", "")),
            symbols=[
                Symbol(
                    name=str(s["name"]),
                    kind=SymbolKind(str(s["kind"])),
                    path=str(s["path"]),
                    line=int(s["line"]),
                    end_line=int(s["end_line"]),
                    parent=str(s.get("parent", "")),
                    signature=str(s.get("signature", "")),
                    doc=str(s.get("doc", "")),
                )
                for s in data.get("symbols") or []
            ],
            terms=set(data.get("terms") or []),
            references=set(data.get("references") or []),
        )


class NodeKind(Enum):
    """What a node in the relationship graph represents."""

    TASK = "task"
    DECISION = "decision"
    FILE = "file"
    SYMBOL = "symbol"
    TEST = "test"
    COMMIT = "commit"
    PULL_REQUEST = "pull-request"
    KNOWLEDGE = "knowledge"


class EdgeKind(Enum):
    """
    How two nodes are related.

    Every edge is derived from something written down — a task naming an ADR, a
    commit naming a task, a docstring naming a decision. None is inferred by
    similarity, because an inferred edge is a claim the project never made.
    """

    REFERENCES = "references"
    IMPLEMENTS = "implements"
    TESTS = "tests"
    TOUCHES = "touches"
    MERGES = "merges"
    DOCUMENTS = "documents"


@dataclass(frozen=True)
class Node:
    """One addressable thing in the project."""

    id: str
    kind: NodeKind
    label: str
    path: str = ""
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "path": self.path,
            "line": self.line,
        }


@dataclass(frozen=True)
class Edge:
    """
    A relationship, with the evidence that produced it.

    ``because`` is not decoration. An edge a human cannot check is an edge they
    have to take on faith, and the point of this graph is that they never have to.
    """

    source: str
    target: str
    kind: EdgeKind
    because: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind.value,
            "because": self.because,
        }


def digest_of(text: str) -> str:
    """A short content digest, for staleness checks."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
