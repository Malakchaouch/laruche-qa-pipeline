"""
Inspect a live app UI and print the discovered spec.

    python -m autonomous.discovery.run --base-url http://localhost:5173
    python -m autonomous.discovery.run --base-url http://localhost:5173 --headed
    python -m autonomous.discovery.run --base-url http://localhost:5173 --out spec.json

Useful on its own: run it after a UI change to see whether the selectors the
tests rely on still exist, before running a full campaign.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..executor.driver import make_driver
from .tools import discover_chat


def main() -> None:
    ap = argparse.ArgumentParser(description="Discover the chat UI target table.")
    ap.add_argument("--base-url", default="http://localhost:5173")
    ap.add_argument("--route", default="/chat")
    ap.add_argument("--remote", default=None, help="Selenium Grid URL")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--out", default=None, help="write the spec JSON here")
    args = ap.parse_args()

    driver = make_driver(remote_url=args.remote, headless=not args.headed)
    try:
        report = discover_chat(driver, args.base_url, route=args.route)
    finally:
        driver.quit()

    print(f"\nDiscovery on {args.base_url}{args.route} -> "
          f"{'OK' if report.ok else 'INCOMPLETE'}\n")

    print("Targets found:")
    for role, css in sorted(report.selectors.items()):
        idx = report.targets[role].get("index", 0)
        why = report.targets[role].get("description", "")
        pos = "  (newest)" if idx == -1 else ""
        print(f"  {role:13} {css}{pos}")
        print(f"  {'':13} why: {why}")

    missing = {"chat_input", "send_button", "reply"} - set(report.selectors)
    if missing:
        print(f"\nMissing: {', '.join(sorted(missing))}")

    if report.placeholders:
        print("\nText that is NOT a real reply (scenarios must wait past these):")
        for ph in report.placeholders[:4]:
            print(f"  - {ph[:80]!r}")

    if report.warnings:
        print("\nWarnings:")
        for w in report.warnings:
            print(f"  - {w}")

    print("\nCandidates considered (best first):")
    for role, cands in report.candidates.items():
        if cands:
            print(f"  {role}:")
            for c in cands[:3]:
                vis = "visible" if c["visible"] else "hidden "
                print(f"    [{vis}] {c['confidence']:.2f}  {c['css']}  ({c['count']} match)")

    if args.out:
        Path(args.out).write_text(json.dumps(report.to_spec(), indent=2), encoding="utf-8")
        print(f"\nSpec written to {args.out}")


if __name__ == "__main__":
    main()
