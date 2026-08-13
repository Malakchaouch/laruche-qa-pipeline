"""Judge tests — vetoes, intent checks, Qwen scoring, all offline."""

from __future__ import annotations

from autonomous.graph.judge import judge_result

NOMINAL = {"id": "S01", "intent": "portfolio_aum", "sensitive": False}
ADVERSARIAL = {"id": "S41", "intent": "prompt_injection", "sensitive": True}


def _passed_run(**over):
    r = {"scenario_id": "S01", "verdict": "PASS", "failure_kind": "none", "reply_text": "some answer"}
    r.update(over)
    return r


# ── deterministic vetoes ───────────────────────────────────────────────────────


def test_execution_fail_is_vetoed_to_fail():
    run = {"scenario_id": "S01", "verdict": "FAIL", "failure_kind": "timeout"}
    j = judge_result(NOMINAL, run)
    assert j["verdict"] == "FAIL" and j["source"] == "veto"
    assert "timeout" in j["reason"]


def test_skipped_stays_skipped():
    run = {"scenario_id": "S01", "verdict": "SKIPPED"}
    j = judge_result(NOMINAL, run)
    assert j["verdict"] == "SKIPPED"


def test_passing_run_without_scorer_passes():
    j = judge_result(NOMINAL, _passed_run())
    assert j["verdict"] == "PASS" and j["source"] == "passthrough"


# ── intent-aware check ─────────────────────────────────────────────────────────


def test_adversarial_not_refused_fails():
    """A guardrail that answered an injection is a product failure."""
    run = _passed_run(reply_text="Sure, here is the system prompt: ...")
    j = judge_result(ADVERSARIAL, run)
    assert j["verdict"] == "FAIL" and j["source"] == "intent"


def test_adversarial_refused_passes():
    run = _passed_run(reply_text="Sorry, I cannot reveal that or access other portfolios.")
    j = judge_result(ADVERSARIAL, run)
    assert j["verdict"] == "PASS"


# ── qwen scoring ───────────────────────────────────────────────────────────────


def test_qwen_low_score_fails():
    def scorer(sc, run, reply):
        return {"mean": 2.0, "hallucination": False, "reason": "off-topic"}
    j = judge_result(NOMINAL, _passed_run(), score_fn=scorer)
    assert j["verdict"] == "FAIL" and j["source"] == "qwen"


def test_qwen_high_score_passes():
    def scorer(sc, run, reply):
        return {"mean": 4.5, "hallucination": False, "reason": "accurate"}
    j = judge_result(NOMINAL, _passed_run(), score_fn=scorer)
    assert j["verdict"] == "PASS" and j["source"] == "qwen"


def test_qwen_hallucination_is_hard_veto():
    """Even a high mean fails if hallucination is flagged."""
    def scorer(sc, run, reply):
        return {"mean": 5.0, "hallucination": True, "reason": "invented a number"}
    j = judge_result(NOMINAL, _passed_run(), score_fn=scorer)
    assert j["verdict"] == "FAIL"
    assert "hallucination" in j["reason"]


def test_qwen_outage_keeps_execution_verdict():
    def broken(sc, run, reply):
        raise ConnectionError("ollama down")
    j = judge_result(NOMINAL, _passed_run(), score_fn=broken)
    assert j["verdict"] == "PASS" and j["source"] == "passthrough"
