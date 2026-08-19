"""
QAState — the single state object that flows through the LangGraph state machine
and is what PostgreSQL checkpoints later (one row per node transition,
keyed by thread_id = job_id).

Design notes
------------
- Keep it SMALL. Checkpoint write cost scales with state size; a bloated state
  becomes the bottleneck. We store scenarios + compact results, not screenshots
  (those live on the /data/runs volume; we keep only their paths).
- `scenario_index` is the loop cursor the diagram's "Advance" node increments and
  "Select Scenario" reads. The conditional edge "More test cases?" is just
  `scenario_index < len(scenarios)`.
- Results accumulate in `results`; the reducer appends so a resumed run never
  loses earlier scenarios' outcomes.
- Everything is JSON-serialisable (dicts, not pydantic objects) inside the state,
  because the checkpointer serialises it. Scenarios are validated into pydantic
  at the edges (in the nodes), not stored as pydantic in the state.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


class QAState(TypedDict, total=False):
    # ── inputs ────────────────────────────────────────────────────────────────
    job_id: str
    base_url: str
    remote_url: str | None          # Selenium Grid URL, or None for local Chromium
    channel: str                    # "web" (browser) or "api" (HTTP endpoint)

    # ── discovered / generated corpus ─────────────────────────────────────────
    # In the skeleton these are injected. Later: Discovery -> Vision -> Generator
    # fill `scenarios` (each a Scenario.model_dump() dict).
    discovered_spec: dict[str, Any]
    scenarios: list[dict[str, Any]]

    # ── loop cursor ───────────────────────────────────────────────────────────
    scenario_index: int
    current_scenario: dict[str, Any] | None
    current_validation: dict[str, Any] | None   # Validator output for the current case

    # ── accumulating outputs ──────────────────────────────────────────────────
    results: Annotated[list[dict[str, Any]], operator.add]
    pending_result: dict[str, Any] | None   # scratch: result awaiting the judge

    # ── config / knobs ────────────────────────────────────────────────────────
    judge_enabled: bool
    use_ollama: bool
    discovery_enabled: bool

    # ── terminal ──────────────────────────────────────────────────────────────
    pipeline_result: dict[str, Any] | None


Verdict = Literal["PASS", "FAIL", "SKIPPED"]


def new_state(
    job_id: str,
    base_url: str,
    scenarios: list[dict[str, Any]],
    channel: str = "web",
    remote_url: str | None = None,
    judge_enabled: bool = True,
    use_ollama: bool = False,
    discovery_enabled: bool = False,
) -> QAState:
    """Build the initial state for a run. Judge is on by default.

    `channel` decides how the Executor reaches the chatbot: "web" drives a real
    browser through the DSL interpreter, "api" posts to the HTTP endpoint and
    reassembles the streamed reply. It defaults to "web" so existing callers
    that omit it keep their previous behaviour.
    """
    return QAState(
        job_id=job_id,
        base_url=base_url,
        remote_url=remote_url,
        channel=channel,
        discovered_spec={},
        scenarios=scenarios,
        scenario_index=0,
        current_scenario=None,
        current_validation=None,
        results=[],
        pending_result=None,
        judge_enabled=judge_enabled,
        use_ollama=use_ollama,
        discovery_enabled=discovery_enabled,
        pipeline_result=None,
    )