"""
Resolve the most recent run's pipeline_result.json.

Job folders are timestamped (`runs/job_20260811_024912`), so a CI job cannot
hard-code the path of the run it just produced. This prints the newest one:

    python -m autonomous.graph.latest_run
    python -m autonomous.graph.latest_run --runs-dir runs --github-output result

Exits 1 with a clear message when there is nothing to point at, so a workflow
step fails loudly instead of silently comparing against a stale file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def latest_run(runs_dir: str | Path = "runs") -> Path:
    """Newest runs/*/pipeline_result.json, by folder modification time."""
    root = Path(runs_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"No runs directory at {root.resolve()}")

    candidates = [p for p in root.glob("*/pipeline_result.json") if p.is_file()]
    if not candidates:
        raise FileNotFoundError(
            f"No pipeline_result.json under {root.resolve()} — did the run finish?"
        )
    # Sort by name as well as mtime: job folders are timestamped, so the name
    # is a reliable tiebreaker when two runs land in the same second.
    return max(candidates, key=lambda p: (p.parent.stat().st_mtime, p.parent.name))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print the newest run's result file.")
    ap.add_argument("--runs-dir", default="runs", help="folder holding job_* dirs")
    ap.add_argument(
        "--github-output",
        default=None,
        metavar="NAME",
        help="also append NAME=<path> to $GITHUB_OUTPUT for later workflow steps",
    )
    args = ap.parse_args(argv)

    try:
        path = latest_run(args.runs_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(path.as_posix())

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write(f"{args.github_output}={path.as_posix()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
