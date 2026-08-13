"""Validator tests — every stage, all offline (no Ollama)."""

from __future__ import annotations

from autonomous.graph.validator import validate_scenario

GOOD = {
    "dsl_version": 1,
    "id": "OK",
    "base_url": "http://localhost:5599",
    "targets": {
        "chat_input": {"css": "input.composer-input"},
        "reply": {"css": ".assistant-answer", "index": -1},
    },
    "steps": [
        {"do": "navigate", "path": "/chat"},
        {"do": "wait_visible", "target": "chat_input"},
        {"do": "type", "target": "chat_input", "text": "hi"},
        {"do": "wait_text_stable", "target": "reply"},
        {"do": "assert_text_not_empty", "target": "reply"},
    ],
}


def test_good_scenario_approved_without_discovered_spec():
    v = validate_scenario(GOOD)
    assert v["approved"] is True
    # target check is skipped when no spec is available yet
    tgt = next(c for c in v["checks"] if c["stage"] == "targets")
    assert "skipped" in tgt["detail"]


def test_schema_failure_short_circuits():
    bad = {"id": "X", "base_url": "http://x", "targets": {}, "steps": []}  # empty steps
    v = validate_scenario(bad)
    assert v["approved"] is False
    assert v["checks"][0]["stage"] == "schema"
    assert len(v["checks"]) == 1  # stopped at stage 0


def test_target_whitelist_rejects_hallucinated_selector():
    """The key check: a scenario using a target Discovery never found is rejected."""
    spec = {"selectors": {"chat_input": "input.composer-input", "reply": ".assistant-answer"}}
    scenario = {
        **GOOD,
        "targets": {**GOOD["targets"], "ghost": {"css": ".does-not-exist"}},
        "steps": GOOD["steps"] + [{"do": "click", "target": "ghost"}],
    }
    v = validate_scenario(scenario, discovered_spec=spec)
    assert v["approved"] is False
    assert "ghost" in v["reason"]


def test_target_whitelist_passes_when_all_known():
    spec = {"selectors": {"chat_input": "input.composer-input", "reply": ".assistant-answer"}}
    v = validate_scenario(GOOD, discovered_spec=spec)
    assert v["approved"] is True


def test_scenario_without_assertion_is_rejected():
    """An always-green test proves nothing — worse than no test."""
    no_assert = {**GOOD, "steps": [
        {"do": "navigate", "path": "/chat"},
        {"do": "wait_visible", "target": "chat_input"},
    ]}
    v = validate_scenario(no_assert)
    assert v["approved"] is False
    assert "assertion" in v["reason"]


def test_qwen_review_can_reject():
    def reviewer(_scenario_dict):
        return False, "intent mismatch"
    v = validate_scenario(GOOD, review_fn=reviewer)
    assert v["approved"] is False
    assert "intent mismatch" in v["reason"]


def test_qwen_outage_degrades_to_pass():
    """A model outage must never block the pipeline."""
    def broken_reviewer(_scenario_dict):
        raise ConnectionError("ollama down")
    v = validate_scenario(GOOD, review_fn=broken_reviewer)
    assert v["approved"] is True
    qwen = next(c for c in v["checks"] if c["stage"] == "qwen_review")
    assert "unavailable" in qwen["detail"]
