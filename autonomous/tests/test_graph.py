"""
Graph skeleton tests — prove the state machine's mechanics with NO browser.

We monkeypatch the interpreter call inside the execute node so the loop,
routing, cursor, result-accumulation and finalize can be verified deterministically.
"""

from __future__ import annotations

import autonomous.graph.nodes as nodes
from autonomous.dsl.schema import ScenarioRunResult
from autonomous.graph.build import build_graph
from autonomous.graph.state import new_state

SMOKE = {
    "dsl_version": 1,
    "id": "T",
    "base_url": "http://x",
    "targets": {"reply": {"css": ".assistant-answer"}},
    "steps": [{"do": "assert_text_not_empty", "target": "reply"}],
}


def _fake_execute(verdict="PASS"):
    """Return a stand-in for run_scenario that never opens a browser."""
    def _run(driver, scenario, evidence_dir, poll_s=0.25):
        return ScenarioRunResult(scenario_id=scenario.id, verdict=verdict)
    return _run


def _no_driver(*a, **k):
    class _D:
        def quit(self): ...
    return _D()


def test_full_loop_three_scenarios(monkeypatch):
    monkeypatch.setattr(nodes, "run_scenario", _fake_execute("PASS"))
    monkeypatch.setattr(nodes, "make_driver", _no_driver)

    scenarios = [dict(SMOKE, id=f"T{i}") for i in range(3)]
    state = new_state("job_test", "http://x", scenarios)
    app = build_graph()

    final = app.invoke(state, {"configurable": {"thread_id": "job_test"}, "recursion_limit": 100})

    pr = final["pipeline_result"]
    assert pr["total"] == 3
    assert pr["passed"] == 3
    assert pr["pass_rate"] == 100.0
    assert final["scenario_index"] == 3  # cursor advanced through all


def test_empty_corpus_goes_straight_to_finalize(monkeypatch):
    monkeypatch.setattr(nodes, "make_driver", _no_driver)
    state = new_state("job_empty", "http://x", [])
    app = build_graph()

    final = app.invoke(state, {"configurable": {"thread_id": "job_empty"}})
    assert final["pipeline_result"]["total"] == 0  # "no scenarios" -> finalize


def test_failing_scenario_is_counted(monkeypatch):
    monkeypatch.setattr(nodes, "run_scenario", _fake_execute("FAIL"))
    monkeypatch.setattr(nodes, "make_driver", _no_driver)

    state = new_state("job_fail", "http://x", [SMOKE])
    app = build_graph()
    final = app.invoke(state, {"configurable": {"thread_id": "job_fail"}, "recursion_limit": 50})

    pr = final["pipeline_result"]
    assert pr["failed"] == 1 and pr["passed"] == 0 and pr["pass_rate"] == 0.0


def test_invalid_scenario_is_skipped_not_executed(monkeypatch):
    # If execute is reached for a bad scenario the fake would mark PASS; the
    # validator stub schema-validates, so a malformed scenario must be SKIPPED.
    monkeypatch.setattr(nodes, "run_scenario", _fake_execute("PASS"))
    monkeypatch.setattr(nodes, "make_driver", _no_driver)

    bad = {"id": "BAD", "base_url": "http://x", "targets": {},
           "steps": [{"do": "click", "target": "ghost"}]}  # unknown target
    state = new_state("job_bad", "http://x", [bad])
    app = build_graph()
    final = app.invoke(state, {"configurable": {"thread_id": "job_bad"}, "recursion_limit": 50})

    r = final["pipeline_result"]["results"][0]
    assert r["verdict"] == "SKIPPED" and r["failure_kind"] == "validation"


def test_checkpointer_persists_state(monkeypatch):
    """The compiled graph exposes checkpointed state under its thread_id."""
    monkeypatch.setattr(nodes, "run_scenario", _fake_execute("PASS"))
    monkeypatch.setattr(nodes, "make_driver", _no_driver)

    state = new_state("job_ckpt", "http://x", [SMOKE])
    app = build_graph()
    cfg = {"configurable": {"thread_id": "job_ckpt"}, "recursion_limit": 50}
    app.invoke(state, cfg)

    snapshot = app.get_state(cfg)          # pulled from the checkpointer
    assert snapshot.values["pipeline_result"]["total"] == 1


def test_intent_is_carried_onto_results(monkeypatch):
    """Regression reports label scenarios by intent, so results must carry it."""
    monkeypatch.setattr(nodes, "run_scenario", _fake_execute("PASS"))
    monkeypatch.setattr(nodes, "make_driver", _no_driver)

    sc = dict(SMOKE, id="S01", intent="portfolio_aum", sensitive=True)
    state = new_state("job_intent", "http://x", [sc])
    app = build_graph()
    final = app.invoke(state, {"configurable": {"thread_id": "job_intent"},
                               "recursion_limit": 50})

    r = final["pipeline_result"]["results"][0]
    assert r["intent"] == "portfolio_aum"
    assert r["sensitive"] is True


def test_skipped_results_also_carry_intent(monkeypatch):
    monkeypatch.setattr(nodes, "run_scenario", _fake_execute("PASS"))
    monkeypatch.setattr(nodes, "make_driver", _no_driver)

    bad = {"id": "BAD", "intent": "empty_input", "base_url": "http://x",
           "targets": {}, "steps": [{"do": "click", "target": "ghost"}]}
    state = new_state("job_skip_intent", "http://x", [bad])
    app = build_graph()
    final = app.invoke(state, {"configurable": {"thread_id": "job_skip_intent"},
                               "recursion_limit": 50})

    r = final["pipeline_result"]["results"][0]
    assert r["verdict"] == "SKIPPED"
    assert r["intent"] == "empty_input"
