"""
Extracting the named things a question can be about.

Python goes through `ast`, which is exact: it knows a class from a function, a
method from a module-level def, and where each one ends. TypeScript goes through
regex, which is not exact and is honest about it — a real TS parser is a
dependency this project does not have, and a regex that finds `export class Foo`
and `export interface Bar` covers the overwhelming majority of what anyone asks
about. Anything it misses is absent from the index rather than wrong in it.

The distinction that matters for answering "where is X implemented" is
**definition versus mention**. A symbol here is a definition — a place the thing
comes into existence. Mentions are handled by the term index, which is a
different question with a different answer.
"""

from __future__ import annotations

import ast
import re

from intelligence.models import Symbol, SymbolKind

# Base names that mark a class as something more specific than "class". Checked
# against the last path segment, so `typing.Protocol` and a bare `Protocol` both
# match.
_PROTOCOL_BASES = frozenset({"Protocol"})
_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})

# Decorators that make a class a dataclass, however they were written.
_DATACLASS_DECORATORS = frozenset({"dataclass", "dataclasses.dataclass"})


def from_python(source: str, path: str) -> list[Symbol]:
    """
    Definitions in a Python file, via the standard AST.

    A file that does not parse yields nothing rather than raising: an index that
    fails on one syntactically broken file is an index nobody can build during
    the exact refactor when they most need it.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    symbols: list[Symbol] = []

    def kind_of_class(node: ast.ClassDef) -> SymbolKind:
        for decorator in node.decorator_list:
            if _decorator_name(decorator) in _DATACLASS_DECORATORS:
                return SymbolKind.DATACLASS
        for base in node.bases:
            name = _attr_tail(base)
            if name in _ENUM_BASES:
                return SymbolKind.ENUM
            if name in _PROTOCOL_BASES:
                return SymbolKind.PROTOCOL
        return SymbolKind.CLASS

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(
                Symbol(
                    name=node.name,
                    kind=kind_of_class(node),
                    path=path,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=f"class {node.name}",
                    doc=_first_line(ast.get_docstring(node)),
                )
            )
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(
                        Symbol(
                            name=member.name,
                            kind=SymbolKind.METHOD,
                            path=path,
                            line=member.lineno,
                            end_line=member.end_lineno or member.lineno,
                            parent=node.name,
                            signature=_signature(member),
                            doc=_first_line(ast.get_docstring(member)),
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                Symbol(
                    name=node.name,
                    kind=SymbolKind.FUNCTION,
                    path=path,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=_signature(node),
                    doc=_first_line(ast.get_docstring(node)),
                )
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # Module-level SCREAMING_CASE names are the project's constants, and
            # people genuinely ask where they are set.
            for name in _assigned_names(node):
                if name.isupper() and len(name) > 2:
                    symbols.append(
                        Symbol(
                            name=name,
                            kind=SymbolKind.CONSTANT,
                            path=path,
                            line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            signature=name,
                        )
                    )

    return symbols


# TypeScript definitions. Deliberately anchored to `export`: an internal helper
# is rarely what someone means by "where is X implemented", and unexported names
# would flood the index with local variables.
_TS_PATTERNS: tuple[tuple[re.Pattern[str], SymbolKind], ...] = (
    (re.compile(r"^\s*export\s+(?:abstract\s+)?class\s+(\w+)", re.M), SymbolKind.CLASS),
    (re.compile(r"^\s*export\s+interface\s+(\w+)", re.M), SymbolKind.INTERFACE),
    (re.compile(r"^\s*export\s+type\s+(\w+)", re.M), SymbolKind.TYPE),
    (re.compile(r"^\s*export\s+enum\s+(\w+)", re.M), SymbolKind.ENUM),
    (re.compile(r"^\s*export\s+(?:async\s+)?function\s+(\w+)", re.M), SymbolKind.FUNCTION),
    (re.compile(r"^\s*export\s+const\s+([A-Z][A-Z0-9_]{2,})\s*[:=]", re.M), SymbolKind.CONSTANT),
    (
        re.compile(r"^\s*export\s+const\s+(\w+)\s*[:=]\s*(?:\([^)]*\)|async|function)", re.M),
        SymbolKind.FUNCTION,
    ),
)


def from_typescript(source: str, path: str) -> list[Symbol]:
    """
    Exported definitions in a TypeScript/JavaScript file.

    Regex, not a parser. The tradeoff is stated in the module docstring: this
    finds what people ask about and silently misses exotic forms, which is the
    right failure direction for an index whose job is to point at a file and a
    line the reader will then look at.
    """
    lines = source.splitlines()
    symbols: list[Symbol] = []
    seen: set[tuple[str, int]] = set()

    for pattern, kind in _TS_PATTERNS:
        for match in pattern.finditer(source):
            name = match.group(1)
            line = source.count("\n", 0, match.start()) + 1
            if (name, line) in seen:
                continue
            seen.add((name, line))
            symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    path=path,
                    line=line,
                    # Regex cannot find a body's end; the definition line is what
                    # a citation needs, and claiming a range would be a guess.
                    end_line=line,
                    signature=lines[line - 1].strip()[:120] if line <= len(lines) else "",
                )
            )

    symbols.sort(key=lambda s: (s.line, s.name))
    return symbols


def extract(source: str, path: str) -> list[Symbol]:
    """Definitions in a file, dispatched on extension."""
    lowered = path.lower()
    if lowered.endswith(".py"):
        return from_python(source, path)
    if lowered.endswith((".ts", ".tsx", ".js", ".jsx")):
        return from_typescript(source, path)
    return []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return _dotted(node)


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _attr_tail(node: ast.expr) -> str:
    """The last segment of a dotted base, so `typing.Protocol` matches `Protocol`."""
    return _dotted(node).rsplit(".", 1)[-1]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    names: list[str] = []
    for target in node.targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _first_line(doc: str | None) -> str:
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""
