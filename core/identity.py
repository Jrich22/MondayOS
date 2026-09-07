"""
Canonical identity: one slug transformation for the whole system.

MondayOS had four. `workspace` folded punctuation one way, `initiatives`
another, `migrate` a third, and `growth` a fourth that also validated. They
disagreed on eleven of seventeen ordinary inputs -- `Invite & RSVP` became
`invite---rsvp` in one and `invite-rsvp` in another -- and since a slug is both
a directory name and an identity key, the same project could be two projects
depending on which subsystem asked.

One of the four was worse than inconsistent. `workspace.slugify` stripped every
non-ASCII character, so a project named in Japanese slugged to the **empty
string**, resolving to no directory at all.

The rule this module establishes:

    Same input -> same canonical slug, everywhere, always.

A subsystem may **reject** a canonical slug for its own safety reasons. It may
never **transform** one into a different identity. That is the difference
between `slug` and `require_slug`, and it is why the strictness lives in the
validator rather than in a second normaliser.
"""

from __future__ import annotations

# One path segment, and a bounded one. 64 is the limit Growth already enforced
# and is comfortably below every filesystem's per-component maximum.
MAX_SLUG_LENGTH = 64

# Characters that make a string more than one path segment, or a traversal.
# Checked after transformation as a guard against the transformation itself
# regressing -- a validator that trusts its input is not a validator.
_PATH_UNSAFE = frozenset({"/", "\\", ":", "\x00"})


class InvalidSlugError(ValueError):
    """
    A value cannot serve as a filesystem identity.

    Carries the original input and the reason, because the caller is usually a
    human naming a project and "invalid" alone tells them nothing about which
    part to change.
    """

    def __init__(self, value: str, reason: str) -> None:
        self.value = value
        self.reason = reason
        super().__init__(f"{value!r} cannot be used as an identity: {reason}.")


def slug(value: str) -> str:
    """
    The canonical identity transformation. Lossy, total, never raises.

    Lower-cases, keeps alphanumerics, and folds every other character to a
    single hyphen. Runs collapse and the ends are trimmed, so `Invite & RSVP`
    and `invite   rsvp` both reach `invite-rsvp`.

    **Unicode alphanumerics are preserved.** Stripping them is what produced an
    empty slug for a name written in a non-Latin script, and an empty slug is
    not a degraded identity -- it is the absence of one, pointing at whatever
    directory happens to be the parent. Callers that genuinely require ASCII say
    so at the validator (see ``require_slug``), where a name is rejected rather
    than silently renamed.

    Returns `""` for input with no alphanumeric content at all. That is a real
    answer, and `require_slug` is where it becomes an error.
    """
    out: list[str] = []
    for char in (value or "").strip().lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def require_slug(value: str, *, ascii_only: bool = False) -> str:
    """
    The canonical slug, validated as a filesystem identity.

    Every value that becomes a directory -- a conversation project directory, a
    Growth workspace -- goes through here. The guarantees:

    - non-empty
    - exactly one path segment (no separators, no traversal, no NUL)
    - bounded length
    - deterministic: the same input always yields the same slug

    ``ascii_only`` is an **additional rejection**, not a different
    transformation. Growth sets it because its workspace names are also used in
    contexts where non-ASCII has historically caused trouble; the slug it gets
    for an accepted name is byte-identical to the one every other subsystem
    computes. That distinction is the whole point: subsystems may narrow what
    they accept, never what a name *means*.
    """
    # Reject on the RAW input, before transformation. This is the ADR-011 rule:
    # traversal is a rejected *name*, not a path to sanitize. Folding "a/b" to
    # "a-b" would accept a name that names two segments and quietly make it one,
    # which is precisely the sanitize-instead-of-reject behaviour that ADR exists
    # to forbid. Checking the output alone cannot catch it, because the
    # transformation has already destroyed the evidence.
    raw = (value or "").strip()
    if any(char in _PATH_UNSAFE for char in raw):
        raise InvalidSlugError(value, "it contains a path separator")
    if ".." in raw:
        raise InvalidSlugError(value, "it contains a directory traversal")

    candidate = slug(value)

    if not candidate:
        raise InvalidSlugError(value, "it contains no letters or digits")
    if len(candidate) > MAX_SLUG_LENGTH:
        raise InvalidSlugError(value, f"it is longer than {MAX_SLUG_LENGTH} characters")
    # Belt and braces: `slug` cannot emit these and the raw check above already
    # rejected them, but a validator that trusts its own transformation is not a
    # validator.
    if candidate in (".", "..") or any(char in _PATH_UNSAFE for char in candidate):
        raise InvalidSlugError(value, "it does not reduce to one safe path segment")
    if ascii_only and not candidate.isascii():
        raise InvalidSlugError(value, "it must use ASCII letters and digits only in this context")
    return candidate


def collides(names: list[str]) -> dict[str, list[str]]:
    """
    Distinct names that collapse to the same slug.

    Unifying four transformations into one narrows the identity space, so two
    names that used to be distinguishable in some subsystem might now collide.
    Returns slug -> the names that produced it, for every slug produced by more
    than one distinct name. An empty result means the set is unambiguous.

    Callers use this to fail loudly at registration rather than to discover the
    collision later as two projects quietly sharing a directory.
    """
    seen: dict[str, list[str]] = {}
    for name in names:
        key = slug(name)
        if not key:
            continue
        if name not in seen.setdefault(key, []):
            seen[key].append(name)
    return {k: v for k, v in seen.items() if len(v) > 1}
