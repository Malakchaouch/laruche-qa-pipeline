"""
Run the full QA state machine on a scenario file (or the built-in smoke scenario).

In the skeleton the corpus is INJECTED (as if Discovery->Generator had produced
it). Later, upstream nodes fill state["scenarios"] and this injection goes away.

    # smoke scenario x3 against the mock SUT
    python -m autonomous.graph.run --base-url http://127.0.0.1:5599 --repeat 3

    # a real scenario file against the Selenium Grid + LaRuche
    python -m autonomous.graph.run --scenario autonomous/dsl/examples/smoke_chat.json \
        --base-url http://host.docker.internal:5173 --remote http://localhost:4444
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .build import build_graph
from .state import new_state

DEFAULT_SCENARIO = Path(__file__).parents[1] / "dsl" / "examples" / "smoke_chat.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the QA-Swarm autonomous state machine.")
    ap.add_argument("--scenario", default=str(DEFAULT_SCENARIO), help="scenario .json to inject")
    ap.add_argument("--corpus", default=None,
                    help="generate scenarios from a corpus JSON instead of --scenario")
    ap.add_argument("--channel", default="web", help="corpus channel filter (with --corpus)")
    ap.add_argument("--base-url", default="http://127.0.0.1:5599")
    ap.add_argument("--remote", default=None, help="Selenium Grid URL")
    ap.add_argument("--repeat", type=int, default=1, help="inject the scenario N times")
    ap.add_argument("--no-judge", action="store_true", help="disable the Judge node")
    ap.add_argument("--discover", action="store_true",
                    help="inspect the live UI first and use the discovered targets")
    ap.add_argument("--ollama", action="store_true",
                    help="qwen for BOTH generation and judging (generation is slow)")
    ap.add_argument("--ollama-judge", action="store_true",
                    help="qwen for judging only (fast, recommended)")
    ap.add_argument("--ollama-generate", action="store_true",
                    help="qwen for scenario generation (slow: large schema grammar)")
    args = ap.parse_args()

    if args.corpus:
        from .generator import generate_scenarios
        gen_fn = spec = None
        if args.ollama or args.ollama_generate:
            from .ollama_client import is_available, make_generate_fn
            if is_available():
                gen_fn = make_generate_fn(spec_base_url=args.base_url)
                spec = (
                    "Web chat UI for a wealth assistant. Available target names: "
                    "chat_input, send_button, reply. Produce 6 scenarios covering "
                    "nominal financial questions, empty input, and prompt injection."
                )
                print("Ollama available - generating with qwen2.5:3b")
            else:
                print("Ollama requested but unavailable - using corpus")
        scenarios = generate_scenarios(
            args.base_url, corpus_path=args.corpus,
            channel=args.channel, limit=args.repeat if args.repeat > 1 else None,
            generate_fn=gen_fn, spec=spec,
        )
        print(f"Generated {len(scenarios)} scenario(s) from corpus "
              f"({args.channel} channel)")
    else:
        scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
        scenarios = [dict(scenario, id=f"{scenario['id']}_{i}") if args.repeat > 1 else scenario
                     for i in range(args.repeat)]

    job_id = f"job_{datetime.now():%Y%m%d_%H%M%S}"
    state = new_state(
        job_id=job_id,
        base_url=args.base_url,
        scenarios=scenarios,
        remote_url=args.remote,
        judge_enabled=not args.no_judge,
        use_ollama=args.ollama or args.ollama_judge,
        discovery_enabled=args.discover,
    )

    app = build_graph()
    config = {"configurable": {"thread_id": job_id}, "recursion_limit": 100}

    print(f"Job {job_id}: {len(scenarios)} scenario(s) on {args.base_url}\n")
    final = app.invoke(state, config)

    pr = final["pipeline_result"]
    out = Path("runs") / job_id / "pipeline_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pr, indent=2), encoding="utf-8")
    print(f"\nPipelineResult: {out}")


if __name__ == "__main__":
    main()
