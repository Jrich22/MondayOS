"""Command-line entry point: `python -m benchmark [--record]`."""

from __future__ import annotations

import sys
from pathlib import Path

from benchmark.report import save_baseline
from benchmark.runner import run, verdict


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    record = "--record" in args

    report = run(config_dir=Path("config"))
    print(report.json())

    if record:
        save_baseline(report)
        print("Baseline re-recorded.")
        return 0

    result = verdict(report)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
