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
from pathlib import Path

from acceptance.pacing import Pacing
from acceptance.runner import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acceptance", description=__doc__)
    parser.add_argument("--project", action="append", default=[], help="limit to these corpora")
    parser.add_argument("--out", default="acceptance-report", help="output path prefix")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between turns")
    parser.add_argument("--config", default="config", help="directory holding projects.json")
    args = parser.parse_args(argv)

    from monday.api import Monday
    from monday.config import MondayConfig
    from monday.provider_env import provider_config

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

    prefix = Path(args.out)
    prefix.with_suffix(".json").write_text(report.to_json(), encoding="utf-8")
    Path(f"{prefix}-review.md").write_text(report.review_markdown(), encoding="utf-8")
    Path("release_readiness.md").write_text(report.readiness_markdown(), encoding="utf-8")

    print(f"\n{report.verdict}")
    for gate in report.gates:
        print(f"  gate {gate.number:>2}  {gate.verdict.value:<14} {gate.name}")
    print(f"\nwrote {prefix}.json, {prefix}-review.md, release_readiness.md")
    return 0 if report.verdict != "NOT READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
