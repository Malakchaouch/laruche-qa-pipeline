# swarm_qa/autonomous — step 2: LangGraph skeleton

The state machine from the architecture diagram, as runnable LangGraph code, with
the step-1 interpreter wired in as the Execute node. Everything else is a stub —
the whole loop walks with zero AI.

```
graph/
├── state.py     # QAState: the object that flows through the graph + gets checkpointed
├── nodes.py     # one function per diagram box (select/validate/execute/judge/advance/finalize)
├── build.py     # wires nodes + conditional edges, compiles with a checkpointer
└── run.py       # CLI: inject a scenario, run the whole machine
```

## The loop (matches the diagram)

```
START -> select_scenario -(scenarios exist?)-> validate | finalize
validate -> execute -(judge enabled?)-> judge | advance
judge -> advance -(more cases?)-> select_scenario | finalize -> END
```

## Node status

| Node | Status | Now | Later |
|------|--------|-----|-------|
| select_scenario | real | loads scenarios[index] | (unchanged) |
| validate | **stub** | schema-validates + approves | + target whitelist, safety rules, Qwen review |
| execute | **real** | runs the DSL interpreter in a browser | (unchanged — this is the payoff of building it first) |
| judge | **stub** | no-op | LLM-as-judge on evidence + hard deterministic veto |
| advance | real | index += 1 | (unchanged) |
| finalize | real | builds PipelineResult | + Reporter (MD + Plotly) |

## Design notes

- **State stays small.** Screenshots live on disk (`runs/`), only their paths go in
  state — checkpoint writes scale with state size.
- **`scenario_index`** is the loop cursor; the "more cases?" edge is just
  `index < len(scenarios)`.
- **`results`** uses an append-reducer, so a resumed run never loses earlier outcomes.
- **MemorySaver** now (in-process). Swapping to **PostgresSaver** later is a one-line
  change in `build.py` — nodes and edges don't change. That's the whole point of
  putting the loop on LangGraph: durable, resumable, inspectable runs keyed by
  `thread_id = job_id`.

## Run it

```bash
pip install langgraph          # plus pydantic selenium from step 1

# unit tests — no browser, no Ollama (fakes the executor to prove the loop)
python -m pytest autonomous/tests -q          # 15 tests

# the whole machine, 3x smoke scenario against the mock SUT
python -m autonomous.mock_sut.serve &                      # terminal 1
python -m autonomous.graph.run --base-url http://127.0.0.1:5599 --repeat 3   # terminal 2

# against the real LaRuche SUT via Selenium Grid
python -m autonomous.graph.run \
    --scenario autonomous/dsl/examples/smoke_chat.json \
    --base-url http://host.docker.internal:5173 \
    --remote http://localhost:4444
```

Each run writes `runs/<job_id>/pipeline_result.json` (the aggregate) plus the
per-scenario evidence folders from step 1.

## What's next

- **Validator brain**: promote the stub to real deterministic checks + Qwen review.
- **Generator**: fill `state["scenarios"]` from a spec, using
  `Scenario.model_json_schema()` as the constrained-output contract.
- **Discovery -> Vision**: replace the injected scenario with a live-discovered
  spec (Selenium + OCR/LLaVA), feeding the Generator.
- **PostgresSaver + api.py + event timeline**, then Dockerise into the role-scoped
  containers.
