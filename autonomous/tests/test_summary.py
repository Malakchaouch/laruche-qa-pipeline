"""
Summary builder tests — prove execution health and judgment quality are
reported as two separate numbers, never conflated into one "success rate".

No LangGraph, no browser, no Ollama: execution_summary() and judgment_summary()
are pure functions over plain result dicts, exactly like judge_result() in
test_judge.py.
"""

from __future__ import annotations

from autonomous.graph.summary import execution_summary, judgment_summary


def _executed(scenario_id: str, execution_verdict: str, judgment_verdict: str | None,
              source: str = "qwen", score: float = 4.0) -> dict:
    """A scenario that ran (execution_verdict set) and was judged (or not)."""
    result: dict = {
        "scenario_id": scenario_id,
        "verdict": judgment_verdict if judgment_verdict is not None else execution_verdict,
        "execution_verdict": execution_verdict,
    }
    if judgment_verdict is not None:
        result["judgment"] = {"verdict": judgment_verdict, "source": source, "score": score}
    return result


# ── the essential case from the bug report ──────────────────────────────────


def test_execution_pass_judgment_fail_is_the_core_case():
    """Scenario B: Selenium/HTTP succeeded, but the answer was judged FAIL.
    This must NOT be counted as a technical failure."""
    scenario_a = _executed("S01", "PASS", "PASS")
    scenario_b = _executed("S10", "PASS", "FAIL")

    exec_sum = execution_summary([scenario_a, scenario_b])
    assert exec_sum == {"successful": 2, "failed": 0, "skipped": 0, "success_rate": 100.0}

    judge_sum = judgment_summary([scenario_a, scenario_b])
    assert judge_sum["pass"] == 1
    assert judge_sum["fail"] == 1
    assert judge_sum["skipped"] == 0
    assert judge_sum["pass_rate"] == 50.0
    assert judge_sum["judge_mode"] == "qwen"


# ── execution_summary: technical outcomes ───────────────────────────────────


def test_technical_execution_failure_is_not_a_judgment_failure():
    """An executor-level failure (timeout, transport...) must show up as a
    technical failure — judgment_summary should still report it as FAIL too,
    since judge_result() vetoes it, but for the right reason (source=veto)."""
    r = {
        "scenario_id": "S41", "execution_verdict": "FAIL", "verdict": "FAIL",
        "judgment": {"verdict": "FAIL", "source": "veto", "reason": "execution failure: timeout"},
    }
    exec_sum = execution_summary([r])
    assert exec_sum == {"successful": 0, "failed": 1, "skipped": 0, "success_rate": 0.0}
    judge_sum = judgment_summary([r])
    assert judge_sum["fail"] == 1 and judge_sum["judge_mode"] == "veto_only"


def test_skipped_scenario_counts_as_skipped_not_failed():
    r = {"scenario_id": "S99", "execution_verdict": "SKIPPED", "verdict": "SKIPPED"}
    exec_sum = execution_summary([r])
    assert exec_sum == {"successful": 0, "failed": 0, "skipped": 1, "success_rate": 0.0}
    judge_sum = judgment_summary([r])
    assert judge_sum["skipped"] == 1
    assert judge_sum["pass"] == 0 and judge_sum["fail"] == 0


# ── judgment_summary: absent/None judgment (judge disabled) ────────────────


def test_missing_judgment_falls_back_to_verdict_and_reports_unknown_mode():
    """judge_enabled=False: execute() appends straight to results, no
    `judgment` key exists at all. judgment_summary must not crash, and must
    make clear this was never a real semantic evaluation."""
    r = {"scenario_id": "S01", "execution_verdict": "PASS", "verdict": "PASS"}
    judge_sum = judgment_summary([r])
    assert judge_sum["pass"] == 1
    assert judge_sum["judge_mode"] == "unknown"


def test_explicit_none_judgment_is_handled_like_absent():
    r = {"scenario_id": "S01", "execution_verdict": "PASS", "verdict": "PASS", "judgment": None}
    judge_sum = judgment_summary([r])
    assert judge_sum["pass"] == 1
    assert judge_sum["judge_mode"] == "unknown"


def test_passthrough_mode_is_flagged_explicitly():
    """No --ollama-judge (or Ollama unreachable): judge_result() returns
    source="passthrough". The rate is real, but judge_mode says so it can
    never be mistaken for a genuine semantic pass rate."""
    r = {"scenario_id": "S01", "execution_verdict": "PASS", "verdict": "PASS",
         "judgment": {"verdict": "PASS", "source": "passthrough", "reason": "execution passed, no veto"}}
    judge_sum = judgment_summary([r])
    assert judge_sum["pass"] == 1
    assert judge_sum["judge_mode"] == "passthrough"


def test_mixed_run_reports_mixed_mode():
    qwen_scored = _executed("S01", "PASS", "PASS", source="qwen")
    fell_back = {"scenario_id": "S02", "execution_verdict": "PASS", "verdict": "PASS",
                 "judgment": {"verdict": "PASS", "source": "passthrough",
                              "reason": "qwen unavailable (ConnectionError); kept execution verdict"}}
    judge_sum = judgment_summary([qwen_scored, fell_back])
    assert judge_sum["judge_mode"] == "mixed"


# ── backward compatibility: old pipeline_result.json without the new fields ─


def test_old_style_result_without_execution_verdict_falls_back_to_verdict():
    """A result produced by the pre-fix finalize() has no `execution_verdict`
    key at all (only `verdict`, already possibly overwritten by the judge).
    execution_summary must degrade gracefully rather than KeyError."""
    old = {"scenario_id": "S01", "verdict": "FAIL",
           "judgment": {"verdict": "FAIL", "source": "qwen", "score": 1.3}}
    exec_sum = execution_summary([old])
    # Best-available approximation: the judged verdict stands in for the
    # (unrecoverable) technical one -- documented in summary.py's docstring.
    assert exec_sum == {"successful": 0, "failed": 1, "skipped": 0, "success_rate": 0.0}


def test_empty_results_list_does_not_divide_by_zero():
    assert execution_summary([]) == {"successful": 0, "failed": 0, "skipped": 0, "success_rate": 0.0}
    judge_sum = judgment_summary([])
    assert judge_sum["pass_rate"] == 0.0 and judge_sum["judge_mode"] == "unknown"
