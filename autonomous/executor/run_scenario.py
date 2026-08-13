"""
Run one DSL scenario file.

    python -m autonomous.executor.run_scenario dsl/examples/smoke_chat.json
    python -m autonomous.executor.run_scenario s.json --remote http://localhost:4444
    python -m autonomous.executor.run_scenario s.json --base-url http://localhost:5173 --headed
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from ..dsl.schema import Scenario
from .driver import make_driver
from .interpreter import run_scenario


def main() -> None:
    ap = argparse.ArgumentParser(description="Execute a Selenium-DSL scenario.")
    ap.add_argument("scenario", help="path to a scenario .json")
    ap.add_argument("--remote", default=None, help="Selenium Grid URL, e.g. http://localhost:4444")
    ap.add_argument("--base-url", default=None, help="override the scenario's base_url")
    ap.add_argument("--evidence", default=None, help="evidence directory (default: runs/<ts>_<id>)")
    ap.add_argument("--headed", action="store_true", help="visible browser (local only)")
    args = ap.parse_args()

    scenario = Scenario.model_validate_json(Path(args.scenario).read_text(encoding="utf-8"))
    if args.base_url:
        scenario = scenario.model_copy(update={"base_url": args.base_url})

    evidence = args.evidence or (
        f"runs/{datetime.now():%Y%m%d_%H%M%S}_{scenario.id}"
    )

    driver = make_driver(remote_url=args.remote, headless=not args.headed)
    try:
        result = run_scenario(driver, scenario, evidence)
    finally:
        driver.quit()

    print(f"\nScenario {scenario.id} -> {result.verdict}  ({result.total_ms:.0f} ms)")
    for s in result.steps:
        mark = "ok " if s.status == "ok" else "FAIL"
        print(f"  [{mark}] {s.index:02d} {s.do:22} {s.ms:8.1f} ms  {s.detail[:70]}")
    if result.verdict == "FAIL":
        print(f"\n  failure: {result.failure_kind} at step {result.failure_step}")
    print(f"  evidence: {result.evidence_dir}")
    sys.exit(0 if result.verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
