"""
Graph nodes — one function per box in the architecture diagram.

Each node takes the current QAState and returns a PARTIAL state update (a dict of
just the keys it changes); LangGraph merges it. This is the LangGraph contract.

Skeleton status of each node:
  select_scenario   REAL   — loads scenarios[scenario_index] into current_scenario
  validate          STUB   — always approves; later: schema + target-whitelist + Qwen
  execute           REAL   — runs the DSL interpreter from step 1 in a real browser
  judge             STUB   — pass-through; later: LLM-as-judge on evidence + hard veto
  advance           REAL   — scenario_index += 1
  finalize          REAL   — builds pipeline_result from accumulated results

The two routers (functions returning a string) implement the diamonds:
  route_after_select  -> "validate" or "finalize"   ("Scenarios exist?" / cursor end)
  route_after_judge   -> "advance"                   (kept simple in skeleton)
  route_after_advance -> "select_scenario" or "finalize"  ("More test cases?")
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..dsl.schema import Scenario
from ..executor.driver import make_driver
from ..executor.interpreter import run_scenario
from ..executor.api_client import run_api_scenario
from .state import QAState
from .validator import validate_scenario
from .judge import judge_result

RUNS_ROOT = Path("runs")


# ── nodes ──────────────────────────────────────────────────────────────────────


def discover(state: QAState) -> dict[str, Any]:
    """
    Discovery Agent — read the live UI and build the target table.

    Runs once at the start. Its output (`discovered_spec`) becomes the
    whitelist the Validator checks scenarios against, so from here on a
    scenario referencing a target the app does not have is rejected before
    the browser is touched.

    Skipped when `discovery_enabled` is False (the corpus path uses the
    known LaRuche selectors), and degrades to an empty spec on failure so
    the pipeline still runs.
    """
    if not state.get("discovery_enabled"):
        return {}

    from ..discovery.tools import discover_chat

    driver = make_driver(remote_url=state.get("remote_url"), headless=True)
    try:
        report = discover_chat(driver, state["base_url"])
    except Exception as e:  # noqa: BLE001 — discovery must never break the run
        print(f"  [discover] failed ({type(e).__name__}) — continuing without spec",
              flush=True)
        return {}
    finally:
        driver.quit()

    spec = report.to_spec()
    found = ", ".join(sorted(report.selectors)) or "nothing"
    print(f"  [discover] {'ok' if report.ok else 'partial'}: {found}", flush=True)
    for w in report.warnings:
        print(f"             warning: {w}", flush=True)
    return {"discovered_spec": spec}


def select_scenario(state: QAState) -> dict[str, Any]:
    """Load the scenario at the current cursor into `current_scenario`."""
    idx = state["scenario_index"]
    scenarios = state["scenarios"]
    if idx >= len(scenarios):
        return {"current_scenario": None}
    sc = scenarios[idx]
    print(f"  [select ] {idx + 1}/{len(scenarios)}  {sc.get('id', '?')}", flush=True)
    return {"current_scenario": sc}


def validate(state: QAState) -> dict[str, Any]:
    """
    REAL validator (promoted from stub). Runs deterministic checks —
    schema, target whitelist vs discovered_spec, safety + budget — plus an
    optional thin Qwen review. See graph/validator.py.

    The Qwen review is off here (review_fn=None); it gets wired in when we
    connect Ollama. Deterministic stages run now and gate the Executor.
    """
    sc = state["current_scenario"]
    verdict = validate_scenario(
        sc,
        discovered_spec=state.get("discovered_spec") or {},
        review_fn=None,
    )
    flag = "approved" if verdict["approved"] else "REJECTED"
    print(f"  [validate] {flag}: {verdict['reason']}", flush=True)
    return {"current_validation": verdict}


def execute(state: QAState) -> dict[str, Any]:
    """
    REAL executor node — wraps the step-1 interpreter.

    If validation rejected the scenario, we record a SKIPPED result and never
    touch the browser (mirrors the diagram's "Valid and execute? NO/SKIP" path).
    """
    sc = state["current_scenario"]
    val = state.get("current_validation") or {"approved": True}

    if not val.get("approved", False):
        result = {
            "scenario_id": sc.get("id", "?"),
            "intent": sc.get("intent", ""),
            "sensitive": bool(sc.get("sensitive", False)),
            "verdict": "SKIPPED",
            "failure_kind": "validation",
            "reason": val.get("reason", ""),
        }
        print(f"  [execute ] SKIPPED ({result['reason']})", flush=True)
        return {"results": [result]}

    evidence = RUNS_ROOT / state["job_id"] / f"{datetime.now():%H%M%S}_{sc.get('id', 'x')}"

    if state.get("channel") == "api":
        result = run_api_scenario(
            sc,
            state.get("base_url") or "http://localhost:8000",
            evidence,
        )
        print(f"  [execute ] {result['verdict']}  ({result['total_ms']:.0f} ms, api)",
              flush=True)
    else:
        scenario = Scenario.model_validate(sc)
        if state.get("base_url"):
            scenario = scenario.model_copy(update={"base_url": state["base_url"]})

        driver = make_driver(remote_url=state.get("remote_url"), headless=True)
        try:
            run_result = run_scenario(driver, scenario, evidence)
        finally:
            driver.quit()

        print(
            f"  [execute ] {run_result.verdict}  "
            f"({run_result.total_ms:.0f} ms, {len(run_result.steps)} steps)",
            flush=True,
        )
        result = run_result.model_dump()
    # Carry scenario metadata onto the result so the Judge, the Reporter and the
    # regression diff can label scenarios by intent rather than by bare id.
    result["intent"] = sc.get("intent", "")
    result["sensitive"] = bool(sc.get("sensitive", False))
    # If the judge runs next, hand it the result to finalize+append; otherwise
    # append directly here. Keeps each result appended exactly once.
    if state.get("judge_enabled"):
        return {"pending_result": result}
    return {"results": [result]}


def judge(state: QAState) -> dict[str, Any]:
    """
    REAL judge. Judges the scenario result held in scratch (`pending_result`),
    applies its verdict, and appends the finalized result to `results` exactly
    once. When the judge is disabled the execute node appends directly and this
    node is skipped by the conditional edge.
    """
    pending = state.get("pending_result")
    if not pending:
        return {}
    result = dict(pending)
    sc = state.get("current_scenario") or {}
    score_fn = None
    if state.get("use_ollama"):
        try:
            from .ollama_client import is_available, make_score_fn
            if is_available():
                score_fn = make_score_fn()
        except Exception:
            score_fn = None
    judgment = judge_result(sc, result, score_fn=score_fn)
    result["judgment"] = judgment
    result["verdict"] = judgment["verdict"]
    print(f"  [judge   ] {judgment['verdict']} ({judgment['source']}: {judgment['reason']})",
          flush=True)
    return {"results": [result], "pending_result": None}


def advance(state: QAState) -> dict[str, Any]:
    """Move the loop cursor forward. (diagram: 'Advance', scenario_index += 1)"""
    nxt = state["scenario_index"] + 1
    print(f"  [advance ] index -> {nxt}", flush=True)
    return {"scenario_index": nxt}


def finalize(state: QAState) -> dict[str, Any]:
    """Build the PipelineResult from accumulated results. (diagram: 'Finalize', END)"""
    results = state.get("results", [])
    total = len(results)
    passed = sum(1 for r in results if r.get("verdict") == "PASS")
    failed = sum(1 for r in results if r.get("verdict") == "FAIL")
    skipped = sum(1 for r in results if r.get("verdict") == "SKIPPED")
    pass_rate = round(100 * passed / total, 1) if total else 0.0

    pipeline_result = {
        "job_id": state["job_id"],
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": pass_rate,
        "results": results,
    }
    print(
        f"\n  [finalize] {passed}/{total} PASS ({pass_rate}%)  "
        f"failed={failed} skipped={skipped}",
        flush=True,
    )
    return {"pipeline_result": pipeline_result}


# ── routers (the diamonds) ─────────────────────────────────────────────────────


def route_after_select(state: QAState) -> str:
    """'Scenarios exist?' + end-of-corpus guard."""
    return "validate" if state.get("current_scenario") is not None else "finalize"


def route_after_judge(state: QAState) -> str:
    """Skeleton always advances; kept as a seam for future judge-driven routing."""
    return "advance"


def route_after_advance(state: QAState) -> str:
    """'More test cases?' — loop back or finish."""
    return "select_scenario" if state["scenario_index"] < len(state["scenarios"]) else "finalize"
