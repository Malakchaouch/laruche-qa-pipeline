"""
Validator — the gate in front of the Executor (diagram: "Validator Agent",
"Valid and execute?"). This promotes the step-2 stub into real checks.

It is the DSL-native successor to the repo's `_is_runnable` gate in
`swarm_qa/channels/web_channel.py`: that one used `ast.parse` to reject
un-runnable generated *code*; we don't generate code, so instead we validate a
declarative scenario against four deterministic checks, then (optionally) a thin
Qwen review. Deterministic stages are pure Python — no Ollama, fully offline-testable.

Stages (cheapest first, short-circuit on first blocking failure):
  0. schema        — is it valid DSL? (Scenario model; already enforced upstream)
  1. targets       — does every referenced target exist in the discovered spec?
  2. safety+budget — step count, timeouts, no off-site navigation, no absurd waits
  3. qwen review   — (optional) does the scenario plausibly match its intent?

Output is a dict (JSON-friendly, so it can live in QAState / be checkpointed):
  {"approved": bool, "reason": str, "checks": [ {stage, ok, detail}, ... ]}
"""

from __future__ import annotations

from typing import Any

from ..dsl.schema import MAX_STEPS, MAX_STEP_TIMEOUT_S, Scenario

# A scenario that references targets NOT present in the discovered spec is the
# single most important thing to catch: it means the Generator hallucinated a
# selector the page doesn't have. This is the "known targets" box in the diagram.


def _check_schema(raw: dict[str, Any]) -> tuple[bool, str, Scenario | None]:
    try:
        return True, "valid DSL", Scenario.model_validate(raw)
    except Exception as e:  # noqa: BLE001
        return False, f"schema invalid: {e}", None


def _check_targets(scenario: Scenario, discovered_spec: dict[str, Any]) -> tuple[bool, str]:
    """Every target the scenario declares must exist in what Discovery found.

    If no discovered spec is available yet (skeleton / hand-written scenarios),
    this check is skipped rather than failing — Discovery isn't wired in until a
    later step. Once Discovery runs, `discovered_spec["selectors"]` (or
    ["targets"]) becomes the whitelist.
    """
    known = discovered_spec.get("selectors") or discovered_spec.get("targets") or {}
    if not known:
        return True, "no discovered spec yet — target check skipped"

    known_names = set(known.keys())
    used = set(scenario.targets.keys())
    unknown = used - known_names
    if unknown:
        return False, f"targets not in discovered spec: {sorted(unknown)}"
    return True, f"all {len(used)} targets known"


def _check_safety_budget(scenario: Scenario) -> tuple[bool, str]:
    """Deterministic guardrails. Most are already enforced by the schema; we
    re-assert here so the Validator is a single, auditable place for the rules
    (and so tightening a threshold doesn't require a schema change)."""
    problems: list[str] = []

    if len(scenario.steps) > MAX_STEPS:
        problems.append(f"{len(scenario.steps)} steps > cap {MAX_STEPS}")

    for i, step in enumerate(scenario.steps):
        # navigate must stay on-site (schema enforces leading '/', we double-check)
        if step.do == "navigate" and not step.path.startswith("/"):
            problems.append(f"step {i}: off-site navigate {step.path!r}")
        # any wait longer than the FAIL-taxonomy timeout is nonsensical
        timeout = getattr(step, "timeout_s", None)
        if timeout is not None and timeout > MAX_STEP_TIMEOUT_S:
            problems.append(f"step {i}: timeout {timeout}s > {MAX_STEP_TIMEOUT_S}s")

    # a scenario with no assertion proves nothing — warn-as-block, since an
    # always-green test is worse than no test (silent false PASS).
    if not any(s.do.startswith("assert") for s in scenario.steps):
        problems.append("no assertion step — scenario cannot FAIL, so it proves nothing")

    if problems:
        return False, "; ".join(problems)
    return True, "safety + budget ok"


def _check_qwen_review(scenario: Scenario, review_fn) -> tuple[bool, str]:
    """Optional thin LLM review. `review_fn(scenario_dict) -> (ok, reason)` is
    injected so this module never imports an LLM client directly (keeps it
    unit-testable and lets Ollama outages degrade gracefully to 'approved')."""
    if review_fn is None:
        return True, "qwen review disabled"
    try:
        ok, reason = review_fn(scenario.model_dump())
        return bool(ok), f"qwen: {reason}"
    except Exception as e:  # noqa: BLE001 — never let a model outage block the pipeline
        return True, f"qwen review unavailable ({type(e).__name__}) — passing"


def validate_scenario(
    raw: dict[str, Any],
    discovered_spec: dict[str, Any] | None = None,
    review_fn=None,
) -> dict[str, Any]:
    """Run all stages, short-circuiting on the first blocking failure.

    Returns a JSON-friendly verdict dict suitable for QAState.current_validation.
    """
    discovered_spec = discovered_spec or {}
    checks: list[dict[str, Any]] = []

    def record(stage: str, ok: bool, detail: str) -> None:
        checks.append({"stage": stage, "ok": ok, "detail": detail})

    ok, detail, scenario = _check_schema(raw)
    record("schema", ok, detail)
    if not ok:
        return {"approved": False, "reason": detail, "checks": checks}

    ok, detail = _check_targets(scenario, discovered_spec)
    record("targets", ok, detail)
    if not ok:
        return {"approved": False, "reason": detail, "checks": checks}

    ok, detail = _check_safety_budget(scenario)
    record("safety_budget", ok, detail)
    if not ok:
        return {"approved": False, "reason": detail, "checks": checks}

    ok, detail = _check_qwen_review(scenario, review_fn)
    record("qwen_review", ok, detail)
    if not ok:
        return {"approved": False, "reason": detail, "checks": checks}

    return {"approved": True, "reason": "all checks passed", "checks": checks}
