"""Regression comparator tests — all five categories, fully offline."""

from __future__ import annotations

from autonomous.graph.regression import (
    compare_runs, render_markdown, render_text, to_dict,
)


def _result(scenario_id, verdict, score=None, ms=1000.0, intent="x", reason=""):
    r = {"scenario_id": scenario_id, "verdict": verdict, "total_ms": ms, "intent": intent}
    if score is not None or reason:
        r["judgment"] = {"verdict": verdict, "reason": reason, "source": "qwen"}
        if score is not None:
            r["judgment"]["score"] = score
    return r


def _run(job_id, results, pass_rate=None):
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    total = len(results)
    return {
        "job_id": job_id, "total": total, "passed": passed,
        "failed": total - passed, "skipped": 0,
        "pass_rate": pass_rate if pass_rate is not None else (
            round(100 * passed / total, 1) if total else 0.0),
        "results": results,
    }


# ── the headline case ─────────────────────────────────────────────────────────


def test_pass_to_fail_flip_is_a_regression():
    base = _run("A", [_result("S01", "PASS", score=4.5)])
    cand = _run("B", [_result("S01", "FAIL", score=2.0, reason="answer is now wrong")])

    rep = compare_runs(base, cand)
    assert rep.has_regressions
    assert len(rep.flips) == 1
    d = rep.flips[0]
    assert d.scenario_id == "S01"
    assert d.baseline_verdict == "PASS" and d.candidate_verdict == "FAIL"
    assert d.score_delta == -2.5
    assert rep.pass_rate_delta == -100.0

def test_identical_runs_have_no_regressions():
    # ... existing setup, unchanged ...
    report = compare_runs(run, run)
    assert report.regressions == []          # was: report.diffs == []
    assert not report.has_regressions


# ── the other categories ──────────────────────────────────────────────────────


def test_score_drop_while_still_passing():
    base = _run("A", [_result("S01", "PASS", score=4.8)])
    cand = _run("B", [_result("S01", "PASS", score=3.5)])
    rep = compare_runs(base, cand)
    assert rep.has_regressions
    assert len(rep.score_drops) == 1
    assert not rep.flips                      # still passing, so not a flip
    assert rep.score_drops[0].score_delta == -1.3


def test_small_score_change_is_ignored():
    base = _run("A", [_result("S01", "PASS", score=4.2)])
    cand = _run("B", [_result("S01", "PASS", score=4.0)])   # -0.2 < threshold 0.5
    assert not compare_runs(base, cand).has_regressions


def test_latency_spike():
    base = _run("A", [_result("S01", "PASS", score=4.0, ms=2000)])
    cand = _run("B", [_result("S01", "PASS", score=4.0, ms=4000)])   # +100%
    rep = compare_runs(base, cand)
    assert len(rep.latency_spikes) == 1
    assert rep.latency_spikes[0].latency_delta_pct == 100.0


def test_faster_is_not_a_spike():
    base = _run("A", [_result("S01", "PASS", score=4.0, ms=4000)])
    cand = _run("B", [_result("S01", "PASS", score=4.0, ms=1000)])
    assert not compare_runs(base, cand).has_regressions


def test_new_failing_scenario_counts_as_regression():
    base = _run("A", [_result("S01", "PASS", score=4.0)])
    cand = _run("B", [_result("S01", "PASS", score=4.0),
                      _result("S99", "FAIL", score=1.0, reason="brand new test fails")])
    rep = compare_runs(base, cand)
    assert rep.has_regressions
    assert len(rep.new_failures) == 1
    assert rep.only_in_candidate == ["S99"]


def test_fix_is_reported_but_not_a_regression():
    base = _run("A", [_result("S01", "FAIL", score=2.0)])
    cand = _run("B", [_result("S01", "PASS", score=4.5)])
    rep = compare_runs(base, cand)
    assert len(rep.fixes) == 1
    assert not rep.has_regressions            # improvements never gate a build
    assert rep.pass_rate_delta == 100.0


def test_scenarios_missing_from_candidate_are_listed_not_dropped():
    base = _run("A", [_result("S01", "PASS", score=4.0), _result("S02", "PASS", score=4.0)])
    cand = _run("B", [_result("S01", "PASS", score=4.0)])
    rep = compare_runs(base, cand)
    assert rep.only_in_baseline == ["S02"]
    assert rep.compared == 1


def test_unjudged_runs_still_compare_verdicts():
    """--no-judge runs have no score; flips must still be detected."""
    base = _run("A", [_result("S01", "PASS")])
    cand = _run("B", [_result("S01", "FAIL")])
    rep = compare_runs(base, cand)
    assert len(rep.flips) == 1
    assert rep.flips[0].score_delta is None


# ── rendering ─────────────────────────────────────────────────────────────────


def test_renderers_produce_output():
    base = _run("A", [_result("S01", "PASS", score=4.5, intent="portfolio_aum")])
    cand = _run("B", [_result("S01", "FAIL", score=1.5, intent="portfolio_aum",
                              reason="wrong figure")])
    rep = compare_runs(base, cand)

    txt = render_text(rep)
    assert "REGRESSIONS DETECTED" in txt and "S01" in txt

    md = render_markdown(rep)
    assert "# Regression Report" in md and "portfolio_aum" in md

    d = to_dict(rep)
    assert d["has_regressions"] is True and d["pass_rate_delta"] == -100.0
