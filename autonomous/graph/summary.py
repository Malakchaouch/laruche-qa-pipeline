"""
Summary builders for pipeline_result.json — split "did it run?" from "was it good?"

The problem this fixes: `execute()` records the raw technical outcome of running
a scenario (did Selenium/the HTTP call succeed?), but when the Judge runs next it
overwrites `result["verdict"]` with its own semantic decision (kept on purpose —
everything that already reads `verdict`, e.g. `compare.py` and the dashboard,
must keep seeing "the one verdict that matters", so `finalize()`'s existing
`passed`/`failed`/`skipped`/`pass_rate` fields are untouched). That overwrite,
though, means the ORIGINAL technical result is no longer visible anywhere once a
run is judged: "13/13 executed" and "8/13 judged correct" collapse into a single
number, which is exactly the misleading "100% success" the project's own README
warns about for passthrough runs (no `--ollama-judge`: only execution is checked).

`execute()` now also stamps a scenario's raw outcome onto a SEPARATE field,
`execution_verdict`, before the Judge ever touches the result. This module reads
that field (falling back to `verdict` when it is absent — old results, or a
result that never went through `execute()`'s new code path) to reconstruct the
two views cleanly, without changing what `verdict` itself means anywhere.

Both functions are pure: list of result dicts in, small summary dict out. No
LangGraph, no I/O — trivially unit-testable, and safe to call a second time on
results loaded back from an old `pipeline_result.json` on disk (the dashboard
does exactly that), since neither function requires `execution_verdict` or
`judgment` to be present.
"""

from __future__ import annotations

from typing import Any

# Same three-way split used throughout the project's own vocabulary
# (ScenarioRunResult.verdict, Judgment.verdict): PASS / FAIL / SKIPPED.


def execution_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """How many scenarios did the Executor actually manage to run?

    Reads `execution_verdict` (the technical outcome, stamped by `execute()`
    before any judging happens). Falls back to `verdict` for results produced
    before this field existed, so this also works on old, already-judged
    `pipeline_result.json` files where the technical outcome was overwritten —
    an approximation in that one case (a judged PASS/FAIL is reported as if it
    were the technical outcome), but the closest available signal, and correct
    whenever the scenario was vetoed for a technical reason (FAIL) or skipped.
    """
    total = len(results)
    successful = failed = skipped = 0
    for r in results:
        outcome = r.get("execution_verdict", r.get("verdict"))
        if outcome == "PASS":
            successful += 1
        elif outcome == "FAIL":
            failed += 1
        elif outcome == "SKIPPED":
            skipped += 1
    rate = round(100 * successful / total, 1) if total else 0.0
    return {
        "successful": successful,
        "failed": failed,
        "skipped": skipped,
        "success_rate": rate,
    }


def judgment_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """How many of the answers obtained were judged functionally correct?

    Reads `judgment.verdict` when a judgment exists. When it does not (the
    judge node never ran for this scenario — `judge_enabled=False`, or an old
    result predating the `judgment` field), falls back to the scenario's own
    `verdict`, which in that situation IS the technical verdict: there is
    nothing more meaningful to report, and `judge_mode` below makes that
    explicit rather than silently passing off execution success as a
    functional PASS.

    `judge_mode` mirrors `regression.py`'s own `_judge_mode()` classification
    (qwen / passthrough / mixed / veto_only / unknown) so a reader — or the
    dashboard — never mistakes a passthrough run's inflated rate for a
    genuine semantic evaluation.
    """
    total = len(results)
    passed = failed = skipped = 0
    sources: set[str] = set()
    for r in results:
        judgment = r.get("judgment")
        verdict = judgment.get("verdict") if judgment else r.get("verdict")
        if verdict == "PASS":
            passed += 1
        elif verdict == "FAIL":
            failed += 1
        elif verdict == "SKIPPED":
            skipped += 1
        source = judgment.get("source") if judgment else None
        if source:
            sources.add(source)
    rate = round(100 * passed / total, 1) if total else 0.0

    graded = sources - {"veto", "passthrough"}
    if graded and "passthrough" in sources:
        judge_mode = "mixed"
    elif graded:
        judge_mode = sorted(graded)[0]          # e.g. "qwen"
    elif "passthrough" in sources:
        judge_mode = "passthrough"
    elif sources:                                # only "veto" ever fired
        judge_mode = "veto_only"
    else:                                        # no judgment anywhere (judge disabled)
        judge_mode = "unknown"

    return {
        "pass": passed,
        "fail": failed,
        "skipped": skipped,
        "pass_rate": rate,
        "judge_mode": judge_mode,
    }
