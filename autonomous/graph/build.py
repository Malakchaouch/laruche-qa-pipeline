"""
graph.py — the state machine from the architecture diagram, in LangGraph.

    START
      -> select_scenario
           -(scenarios exist?)-> validate        | finalize
      validate -> execute
      execute  -(judge enabled?)-> judge          | advance
      judge    -> advance
      advance  -(more test cases?)-> select_scenario | finalize
      finalize -> END

Compiled with a checkpointer so every transition is persisted under
thread_id = job_id. MemorySaver for the skeleton (in-process); swap to
PostgresSaver later with zero changes to nodes or edges.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import QAState


def route_after_execute(state: QAState) -> str:
    """'Judge enabled?' diamond."""
    return "judge" if state.get("judge_enabled") else "advance"


def build_graph(checkpointer=None):
    """Build and compile the QA state machine. Pass a checkpointer or get MemorySaver."""
    g = StateGraph(QAState)

    g.add_node("discover", nodes.discover)
    g.add_node("select_scenario", nodes.select_scenario)
    g.add_node("validate", nodes.validate)
    g.add_node("execute", nodes.execute)
    g.add_node("judge", nodes.judge)
    g.add_node("advance", nodes.advance)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "discover")
    g.add_edge("discover", "select_scenario")
    g.add_conditional_edges(
        "select_scenario", nodes.route_after_select,
        {"validate": "validate", "finalize": "finalize"},
    )
    g.add_edge("validate", "execute")
    g.add_conditional_edges(
        "execute", route_after_execute,
        {"judge": "judge", "advance": "advance"},
    )
    g.add_edge("judge", "advance")
    g.add_conditional_edges(
        "advance", nodes.route_after_advance,
        {"select_scenario": "select_scenario", "finalize": "finalize"},
    )
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())
