"""
Initiatives a human named, including ones nothing implements yet.

Discovery can only see what exists. That is a hard ceiling on reasoning about a
roadmap: a capability agreed in a planning conversation and not yet started is
invisible to every signal in the repository, and it is precisely the thing a
founder most needs Monday to keep in view. "We committed to Billing and have
written nothing" is a sentence a repository-derived system cannot say.

Declarations close that gap. They are the only part of initiative intelligence
that is a system of record rather than a derivation, so they live in `config/`
beside the project registry, in a small JSON file a human can read and edit.

The reader is deliberately forgiving. A malformed entry is skipped rather than
raising, because a typo in one initiative should not take down reasoning about
the other nine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from initiatives.models import Seed

FILENAME = "initiatives.json"

# Declared initiatives outrank every derived signal: a human naming a capability
# is the strongest statement available about what the product is.
DECLARED_AUTHORITY = 10


def path_for(config_dir: Path) -> Path:
    return Path(config_dir) / FILENAME


def load(config_dir: Path) -> list[Seed]:
    """
    Declared initiatives, or an empty list when none are configured.

    A missing file is the normal case, not an error: most projects will run on
    derived initiatives alone until someone wants to track something unbuilt.
    """
    target = path_for(config_dir)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    entries = raw.get("initiatives") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []

    seeds: list[Seed] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        keywords = [str(k).lower() for k in entry.get("keywords") or [] if str(k).strip()]
        seeds.append(
            Seed(
                name=name,
                because="declared in config/initiatives.json",
                declared=True,
                paths=[str(p) for p in entry.get("paths") or [] if str(p).strip()],
                # The name itself is always a keyword: declaring "Billing" should
                # match work that says Billing without anyone repeating it.
                keywords=sorted({name.lower(), *keywords}),
                summary=str(entry.get("summary", "")),
                authority=DECLARED_AUTHORITY,
            )
        )
    return seeds


def save(config_dir: Path, seeds: list[Seed]) -> Path:
    """Write declarations back, preserving only the human-authored fields."""
    target = path_for(config_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "initiatives": [
            {
                "name": seed.name,
                "summary": seed.summary,
                "paths": list(seed.paths),
                # The name is implied; storing it back as a keyword would grow the
                # file on every round trip.
                "keywords": [k for k in seed.keywords if k != seed.name.lower()],
            }
            for seed in seeds
        ]
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def declare(
    config_dir: Path,
    name: str,
    summary: str = "",
    paths: list[str] | None = None,
    keywords: list[str] | None = None,
) -> list[Seed]:
    """Add or replace one declaration, returning the full set."""
    seeds = [s for s in load(config_dir) if s.name.lower() != name.strip().lower()]
    seeds.append(
        Seed(
            name=name.strip(),
            because="declared in config/initiatives.json",
            declared=True,
            paths=list(paths or []),
            keywords=sorted({name.strip().lower(), *(k.lower() for k in keywords or [])}),
            summary=summary,
            authority=DECLARED_AUTHORITY,
        )
    )
    seeds.sort(key=lambda s: s.name.lower())
    save(config_dir, seeds)
    return seeds
