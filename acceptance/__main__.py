"""
`python -m acceptance` — the live run.

Deliberately not part of `pytest`. It needs a configured provider, a network in
most configurations, and minutes of wall clock; a CI suite that depends on a
provider is a CI suite that fails for reasons unrelated to the code. The harness
itself is unit-tested offline against a scripted provider, and this entry point
is invoked on purpose, before a release.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from acceptance.pacing import Pacing
from acceptance.runner import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acceptance", description=__doc__)
    parser.add_argument("--project", action="append", default=[], help="limit to these corpora")
    parser.add_argument(
        "--out",
        default="reports/acceptance-report",
        help="output path prefix (default: under reports/, which the index excludes)",
    )
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between turns")
    parser.add_argument("--config", default="config", help="directory holding projects.json")
    args = parser.parse_args(argv)

    from monday.api import Monday
    from monday.config import MondayConfig
    from monday.provider_env import load_env_file, provider_config

    # The same project-local `.env` the dashboard reads, through the same loader.
    # Without this the documented way to configure a provider worked for one
    # entry point and silently did not for the other: a key present in `.env`
    # left this runner falling through to whatever local daemon was answering,
    # which reads as "Anthropic is unavailable" rather than "nobody loaded the
    # file". An exported value still wins -- `load_env_file` fills gaps only, and
    # returns variable names, never values.
    project_root = Path(os.environ.get("MONDAYOS_ROOT", ".")).resolve()
    load_env_file(project_root / ".env")

    config = provider_config()
    if config is None:
        print("No AI provider is configured. See docs/PROVIDERS.md.")
        return 2

    from brain.providers.factory import create_provider

    provider = create_provider(config)

    def build(root: Path) -> Monday:
        return Monday(MondayConfig(project_root=root, provider_config=config))

    report = run(
        config_dir=Path(args.config),
        build_monday=build,
        provider=provider,
        only=tuple(args.project),
        pacing=Pacing(between_turns=args.pause, after_executive=args.pause * 2),
    )

    # Everything a run writes lands under `reports/`, which the scanner excludes
    # by directory. A report about MondayOS is not part of MondayOS, and putting
    # one where the index can see it changes the project's own measurements.
    prefix = Path(args.out)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    readiness = prefix.parent / "release_readiness.md"
    prefix.with_suffix(".json").write_text(report.to_json(), encoding="utf-8")
    Path(f"{prefix}-review.md").write_text(report.review_markdown(), encoding="utf-8")
    readiness.write_text(report.readiness_markdown(), encoding="utf-8")

    print(f"\n{report.verdict}")
    for gate in report.gates:
        print(f"  gate {gate.number:>2}  {gate.verdict.value:<14} {gate.name}")
    print(f"\nwrote {prefix}.json, {prefix}-review.md, {readiness}")
    return 0 if report.verdict != "NOT READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
