"""
Markdown → Confluence storage-format converter.

Confluence stores page bodies in an XHTML dialect ("storage format"). This is
a deliberately small, dependency-free converter that covers the constructs
MondayOS documents actually use: headings, paragraphs, bullet and numbered
lists, fenced code blocks, tables, links, and inline emphasis. It is not a
full CommonMark implementation — the goal is a faithful first version, not
completeness.
"""
from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_HR = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_ULI = re.compile(r"^[-*+]\s+(.*)$")
_OLI = re.compile(r"^\d+\.\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def markdown_to_storage(md: str) -> str:
    """Convert a Markdown string to Confluence storage-format XHTML."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    i, n = 0, len(lines)

    def flush_para() -> None:
        if para:
            text = " ".join(p.strip() for p in para).strip()
            if text:
                out.append(f"<p>{_inline(text)}</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # fenced code block
        if stripped.startswith("```"):
            flush_para()
            lang = stripped[3:].strip()
            code: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # consume closing fence (tolerant of EOF)
            out.append(_code_macro("\n".join(code), lang))
            continue

        # blank line ends a paragraph
        if not stripped:
            flush_para()
            i += 1
            continue

        # heading
        m = _HEADING.match(stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # horizontal rule
        if _HR.match(stripped):
            flush_para()
            out.append("<hr/>")
            i += 1
            continue

        # table: a pipe row followed by a separator row
        if "|" in stripped and i + 1 < n and "-" in lines[i + 1] and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            block, i = _consume_table(lines, i, n)
            out.append(block)
            continue

        # unordered list
        if _ULI.match(stripped):
            flush_para()
            block, i = _consume_list(lines, i, n, ordered=False)
            out.append(block)
            continue

        # ordered list
        if _OLI.match(stripped):
            flush_para()
            block, i = _consume_list(lines, i, n, ordered=True)
            out.append(block)
            continue

        # otherwise accumulate into the current paragraph
        para.append(line)
        i += 1

    flush_para()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------

def _consume_list(lines: list[str], i: int, n: int, ordered: bool) -> tuple[str, int]:
    tag = "ol" if ordered else "ul"
    pattern = _OLI if ordered else _ULI
    items: list[str] = []
    while i < n:
        m = pattern.match(lines[i].strip())
        if not m:
            break
        items.append(f"<li>{_inline(m.group(1).strip())}</li>")
        i += 1
    return f"<{tag}>{''.join(items)}</{tag}>", i


def _consume_table(lines: list[str], i: int, n: int) -> tuple[str, int]:
    header = _split_row(lines[i])
    i += 2  # skip header + separator
    rows: list[list[str]] = []
    while i < n and lines[i].strip() and "|" in lines[i]:
        rows.append(_split_row(lines[i]))
        i += 1
    html = ["<table><tbody>"]
    html.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr>")
    for row in rows:
        html.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    html.append("</tbody></table>")
    return "".join(html), i


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _code_macro(code: str, language: str) -> str:
    lang_param = (
        f'<ac:parameter ac:name="language">{_escape(language)}</ac:parameter>'
        if language
        else ""
    )
    # CDATA keeps code verbatim; guard the rare literal "]]>" sequence.
    safe = code.replace("]]>", "]]]]><![CDATA[>")
    return (
        '<ac:structured-macro ac:name="code">'
        f"{lang_param}"
        f"<ac:plain-text-body><![CDATA[{safe}]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )


# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------

def _inline(text: str) -> str:
    """Apply inline formatting to already-block-split text."""
    text = _escape(text)
    text = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\*\w])\*([^*\n]+)\*(?![\*\w])", r"<em>\1</em>", text)
    return text


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
