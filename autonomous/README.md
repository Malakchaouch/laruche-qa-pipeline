# swarm_qa/autonomous — step 1: Selenium DSL + Executor interpreter

First brick of the QA-Swarm Autonomous LangGraph architecture: the constrained
scenario DSL (the contract between Generator, Validator and Executor) and the
deterministic interpreter that executes it in a real browser with full
evidence capture.

```
autonomous/
├── dsl/
│   ├── schema.py            # THE contract: verbs, targets, caps, results
│   └── examples/smoke_chat.json
├── executor/
│   ├── driver.py            # local Chromium <-> Selenium Grid (:4444)
│   ├── interpreter.py       # DSL -> browser, evidence, failure classes
│   └── run_scenario.py      # CLI
├── mock_sut/                # offline chat page with simulated streaming
└── tests/                   # 10 tests, no browser or Ollama needed
```

## Design decisions (why it looks like this)

- **Named targets, not selectors in steps.** Steps say `"target": "chat_input"`;
  the `targets` table maps names to CSS. Discovery produces the table, the
  Generator can only compose verbs over names -> it cannot hallucinate a
  selector. The schema itself rejects unknown target references.
- **Closed verb set, bounded everything.** 13 verbs, timeouts capped at 30 s
  (FAIL taxonomy), `navigate` accepts relative paths only, max 40 steps.
- **`wait_text_stable`** = streaming-aware wait: text non-empty and unchanged
  for `stable_s`. This is how "the SSE reply finished" is expressed.
- **Evidence discipline.** Auto-screenshot after every step
  (`screenshots/000_navigate.png`, ...), orange highlight before interactions,
  `result.json` persisted per run.
- **Failure classes** keep triage honest: `assertion`/`timeout` are
  product-side, `tool_error` is infra, `target_not_found` means a validated
  target vanished (UI changed or crashed).

## Run it

```bash
# 0) deps (or add selenium/pydantic/pytest to qa-swarm's pyproject extras)
pip install pydantic selenium pytest

# 1) unit tests — no browser needed
python -m pytest autonomous/tests -q

# 2) offline end-to-end against the mock chat (needs local Chrome/Chromium)
python -m autonomous.mock_sut.serve &          # http://localhost:5599/chat
python -m autonomous.executor.run_scenario \
    autonomous/dsl/examples/smoke_chat.json --base-url http://localhost:5599

# 3) the real thing — Selenium Grid + the LaRuche SUT
docker run -d -p 4444:4444 -p 7900:7900 --shm-size=2g \
    --name selenium selenium/standalone-chromium:latest
cd frontend && npm run dev                      # SUT web on :5173
python -m autonomous.executor.run_scenario \
    autonomous/dsl/examples/smoke_chat.json --remote http://localhost:4444
# watch the browser live: http://localhost:7900  (password: secret)
```

Note: with `--remote`, `localhost` inside the Selenium container is the
container itself — use `--base-url http://host.docker.internal:5173`
(Mac/Windows) or your host LAN IP (Linux) so the containerised browser can
reach the SUT.

When nesting this under the repo as `qa-swarm/swarm_qa/autonomous/`, update
the two test imports: `autonomous.` -> `swarm_qa.autonomous.`.

## What builds on this next

- **Validator agent**: schema validation is already stage 0; add the
  target-whitelist-vs-Discovery check, step-budget rules, and the Qwen review.
- **Graph skeleton**: QAState + Select -> Validate -> Execute -> Advance ->
  Finalize loop with this interpreter as the Executor node.
- **Generator**: `Scenario.model_json_schema()` is its output contract for
  constrained JSON generation + deterministic repair.
