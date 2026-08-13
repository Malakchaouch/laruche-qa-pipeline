"""
Compare two pipeline runs and report regressions.

    python -m autonomous.graph.compare runs/job_A/pipeline_result.json \\
                                       runs/job_B/pipeline_result.json

    # write a markdown report and fail the build on regressions (CI gate)
    python -m autonomous.graph.compare A.json B.json --markdown report.md --fail-on-regression

Exit codes:  0 = no regressions   1 = regressions found (with --fail-on-regression)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .regression import (
    compare_runs, load_result, render_markdown, render_text, to_dict,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="Detect regressions between two QA runs.")
    ap.add_argument("baseline", help="baseline pipeline_result.json")
    ap.add_argument("candidate", help="candidate pipeline_result.json")
    ap.add_argument("--score-drop", type=float, default=0.5,
                    help="judge-score drop that counts as a regression (default 0.5)")
    ap.add_argument("--latency-pct", type=float, default=50.0,
                    help="%% slower that counts as a latency spike (default 50)")
    ap.add_argument("--markdown", default=None, help="write a Markdown report to this path")
    ap.add_argument("--json", dest="json_out", default=None, help="write the diff as JSON")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit 1 when regressions are found (for CI)")
    args = ap.parse_args()

    report = compare_runs(
        load_result(args.baseline),
        load_result(args.candidate),
        score_drop_threshold=args.score_drop,
        latency_spike_pct=args.latency_pct,
    )

    print(render_text(report))

    if args.markdown:
        Path(args.markdown).write_text(render_markdown(report), encoding="utf-8")
        print(f"\nMarkdown report: {args.markdown}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(to_dict(report), indent=2), encoding="utf-8")
        print(f"JSON diff: {args.json_out}")

    sys.exit(1 if (args.fail_on_regression and report.has_regressions) else 0)


if __name__ == "__main__":
    main()
