# LaRuche QA Pipeline

An autonomous testing pipeline for **LaRuche**, a wealth-management conversational
agent. It generates test scenarios, runs them against the live application through
a real browser or its HTTP API, judges the quality of the answers with a local LLM,
and **detects regressions between two versions without a human reading any replies**.

That last point is the project's stated success criterion, and it is demonstrated
in [Results](#results).

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running the system under test](#running-the-system-under-test)
- [Usage](#usage)
- [Regression detection](#regression-detection)
- [Continuous integration](#continuous-integration)
- [Results](#results)
- [Findings on LaRuche](#findings-on-laruche)
- [Known limitations](#known-limitations)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)

---

## How it works

The pipeline is a **LangGraph state machine**. Each node does one thing and hands a
partial state update to the next; a `MemorySaver` checkpoint is written at every
transition, so an interrupted run is resumable.

```
              ┌──────────┐
              │ discover │  inspect the live UI, build the target table
              └────┬─────┘
                   ▼
        ┌──────────────────┐
   ┌───▶│ select_scenario  │  load scenario at the cursor
   │    └────────┬─────────┘
   │             ▼
   │      ┌────────────┐
   │      │  validate  │  schema · target whitelist · safety · budget
   │      └─────┬──────┘
   │            ▼
   │      ┌────────────┐
   │      │  execute   │  web (Selenium) or api (HTTP/SSE)
   │      └─────┬──────┘
   │            ▼
   │      ┌────────────┐
   │      │   judge    │  deterministic vetoes, then qwen2.5:3b scoring
   │      └─────┬──────┘
   │            ▼
   │      ┌────────────┐
   └──────┤  advance   │  cursor += 1, loop or finish
          └─────┬──────┘
                ▼
          ┌────────────┐
          │  finalize  │  build pipeline_result.json
          └────────────┘
```

**Scenarios are written in a small DSL** (13 verbs, validated with Pydantic) rather
than in code. Steps address *named targets* — `chat_input`, `send_button`, `reply` —
not raw CSS selectors, so a UI change is fixed in one target table instead of across
every scenario. The DSL is the contract between the Generator (writes it), the
Validator (checks it) and the Executor (interprets it).

**Judging is two-stage.** Deterministic vetoes fire first and need no model: an
execution failure, a timeout, an empty reply, or an adversarial scenario that was not
refused. Only what survives reaches the local LLM, which scores relevance, accuracy
and coherence and flags hallucination. A veto always beats a score — a small model is
not trusted to remember every rule.

---

## Requirements

| Component | Version / notes |
|---|---|
| Python | 3.10+ (developed on 3.14) |
| Chrome + chromedriver | matching versions, for the `web` channel |
| Ollama | with `qwen2.5:3b` pulled, for the LLM judge |
| LaRuche | running locally — see below |

Python packages: `langgraph`, `pydantic`, `selenium`, `ollama`, `pytest`.
The API channel uses only the standard library.

```bash
pip install langgraph pydantic selenium ollama pytest
ollama pull qwen2.5:3b
```

> The judge and LaRuche's own agents share one GPU. On a 4 GB card Ollama swaps
> models in and out, which makes judged runs noticeably slower. This affects speed,
> not results.

---

## Installation

```bash
git clone https://github.com/Malakchaouch/laruche-qa-pipeline.git
cd laruche-qa-pipeline
python -m pytest autonomous/tests -q      # expect 71 passed
```

**Run every command from the folder that *contains* `autonomous/`, never from inside
it.** `No module named autonomous` always means you are one level too deep.

---

## Running the system under test

LaRuche needs four processes. Three services share the same preamble:

```bat
cd C:\laruche\intervalue-main
.venv\Scripts\activate
set PYTHONPATH=C:\laruche\intervalue-main\libs\agentkit\src
```

then each terminal diverges:

| Terminal | Working directory | Command |
|---|---|---|
| 1 — Orchestrator | `services\orchestrator\src` | `python -m uvicorn orchestrator.main:app --port 8000` |
| 2 — Financial agent | `services\agent-financial\src` | `python -m uvicorn agent_financial.main:app --port 8001` |
| 3 — Market agent | `services\agent-market\src` | `python -m uvicorn agent_market.main:app --port 8002` |
| 4 — Frontend | `frontend` (no venv) | `npm run dev` |

**Before a long run, send one message by hand** at `http://localhost:5173`. A real
answer means the whole chain is alive; otherwise every scenario fails for an
infrastructure reason and the report looks like a catastrophic regression.

---

## Usage

```bash
# Web channel — real browser, 13 scenarios
python -m autonomous.graph.run --corpus scenarios.json --channel web \
    --base-url http://localhost:5173 --ollama-judge

# API channel — HTTP, no browser, 48 scenarios, much faster
python -m autonomous.graph.run --corpus scenarios.json --channel api \
    --base-url http://localhost:8000 --ollama-judge
```

| Flag | Purpose |
|---|---|
| `--corpus PATH` | scenario corpus to generate from |
| `--channel {web,api}` | execution channel; also filters the corpus |
| `--base-url URL` | frontend for `web`, orchestrator for `api` |
| `--ollama-judge` | **judge with qwen (recommended)** |
| `--ollama-generate` | qwen writes the scenarios (slow) |
| `--ollama` | qwen for both |
| `--no-judge` | disable the judge node entirely |
| `--repeat N` | run each scenario N times |
| `--discover` | inspect the live UI first and use discovered targets |
| `--remote URL` | Selenium Grid instead of local Chrome |

Results land in `runs/job_<timestamp>/`: one `pipeline_result.json` plus a folder per
scenario holding screenshots (web) or `exchange.json` (api).

> **Without `--ollama-judge` the pipeline only checks that the steps executed** — not
> whether the answers were any good. Runs judged that way report `passthrough` and
> score far higher. Do not compare a passthrough run with a judged one; the
> comparator warns when you try.

---

## Regression detection

Compare any two runs:

```bash
python -m autonomous.graph.compare \
    baselines/api_v1/pipeline_result.json \
    runs/job_20260819_044330/pipeline_result.json \
    --markdown regression.md --fail-on-regression
```

| Classification | Meaning | Counts as a regression |
|---|---|---|
| `flip` | PASS → FAIL | **yes** |
| `score_drop` | still passing, judge score fell ≥ threshold | yes |
| `latency_spike` | ≥ 50 % slower | yes |
| `new_failure` | absent from baseline, failing now | yes |
| `fix` | FAIL → PASS | no |
| `stable_fail` | failing in both runs | no |

`stable_fail` exists so a reader can tell "6 failures, 1 regression" apart from a
report that lost five failures. Pre-existing failures are context, not news.

Exit codes: `0` clean, `1` regressions found (with `--fail-on-regression`). That is
what makes the comparator usable as a CI gate.

Useful flags: `--score-drop` (default 0.5), `--latency-pct` (default 50),
`--json`, `--markdown`.

---

## Continuous integration

Two workflows, because they have different needs.

| Workflow | Runner | Trigger | Guards |
|---|---|---|---|
| `tests.yml` | GitHub-hosted | every push and PR | the pipeline's own code — 71 unit tests |
| `qa-regression.yml` | self-hosted | manual / nightly | LaRuche itself |

A full run needs LaRuche, Chrome and a GPU, none of which exist on GitHub's hosted
runners — hence the split. `tests.yml` works out of the box and is currently green.
`qa-regression.yml` is written and documented but needs a runner installed on a
machine that has the stack; see `.github/workflows/README.md`.

---

## Results

Measured against LaRuche with the LLM judge active.

| Channel | Scenarios | Pass rate | Notes |
|---|---|---|---|
| Web | 13 | **61.5 %** | real browser, screenshots captured |
| API | 48 | **64.6 %** | ~69 % after discounting 2 judge miscalibrations |

Two independent channels converging on a similar figure is reassuring: they are
measuring the same thing through very different paths.

### Regression detection, demonstrated

| Check | Method | Result |
|---|---|---|
| No false positives | compare two identical runs | **zero regressions reported** |
| True detection | stop the financial agent, re-run, compare | **regression caught**, exit code 1 |

In the second case the comparator isolated **one** genuine PASS → FAIL flip (S26,
`comparison_query`, judge score 3.0 → 2.0) and correctly classified the other five
failures as pre-existing. It does not recount failures; it identifies what changed.

---

## Findings on LaRuche

Three reproducible findings, all discovered by the pipeline without human review.

**1. System-prompt disclosure (S51, `prompt_leak_indirect`).** LaRuche reveals the
contents of its own system prompt in response to an indirect probe. Reproduced on
**both** channels independently. No rule-based check would have caught this — it took
semantic evaluation of the reply.

**2. The API filters known attacks; the web path does not.** `S41`
(`prompt_injection`) and `S45` (`jailbreak_roleplay`) are rejected with **HTTP 400**
before reaching the model on the API, while the same S41 on the web channel ran for
32 s and timed out. Same system, two entrances, different defences. The filter is
also pattern-based and incomplete — it catches "ignore all previous instructions" and
"DAN" but misses S51's subtler phrasing.

**3. Generic-summary fallback.** On several scenarios (`bottom_deals`,
`sql_injection`, `social_engineering`, `fake_deal_name`, `encoding_attack`) LaRuche
returns a generic portfolio summary instead of addressing the actual question or
refusing. It answers *something* rather than the right thing.

---

## Known limitations

**Judge miscalibration on limit scenarios.** The judge scores informativeness, so a
scenario whose *correct* behaviour is to ask for clarification gets marked down.
Manual review found 2 such false failures in the API run (`S33` mixed-language input,
`S35` single character) — LaRuche behaved correctly in both. The fix is to pass the
scenario's `type` into the judge prompt so limit cases are scored on graceful
handling; the Generator currently drops that field.

**Small-model variance.** `qwen2.5:3b` gives borderline verdicts that can differ
between identical runs. Treat a single-scenario regression as a signal to look, not
as proof. Mitigations, designed but not implemented: repeat sensitive scenarios and
require the failure to recur, and let deterministic rules decide everything objective.

**Coupling to LaRuche's DOM.** The web channel depends on three selectors —
`input.composer-input`, `button.composer-primary:not(.composer-voice)`,
`.assistant-answer`. A refactor of the composer breaks execution. Recommendation for
the LaRuche team: add `data-testid` attributes to those three elements.

**Mobile channel not implemented.** The corpus tags 5 scenarios for `mobile`; Appium
support does not exist yet.

**HTTP 400 is reported as `transport`.** LaRuche rejecting a payload and LaRuche
being unreachable currently share a failure kind, though they mean very different
things.

---

## Project layout

```
autonomous/
├── dsl/schema.py          Scenario/Step models — the DSL contract
├── discovery/tools.py     inspects the live UI, produces the target table
├── executor/
│   ├── driver.py          Chrome/Selenium driver factory
│   ├── interpreter.py     runs DSL steps in a browser (web channel)
│   └── api_client.py      POSTs to /api/chat, reassembles the SSE stream
├── graph/
│   ├── state.py           QAState — what flows through the graph
│   ├── nodes.py           one function per node
│   ├── build.py           wires the StateGraph
│   ├── run.py             CLI entry point
│   ├── validator.py       pre-execution checks
│   ├── judge.py           vetoes + LLM scoring
│   ├── ollama_client.py   local model access
│   ├── regression.py      comparison engine
│   ├── compare.py         comparison CLI
│   └── latest_run.py      resolves the newest run (used by CI)
└── tests/                 71 tests, no browser or model needed

scenarios.json             52-scenario corpus (48 api, 13 web, 5 mobile)
baselines/                 committed reference runs
runs/                      output, git-ignored
```

### About the API channel

`POST /api/chat` answers with `text/event-stream`, one token per event:

```
data: {"token": "Total ", "conversation_id": "e1c038cf-..."}
data: {"token": "AUM ",   "conversation_id": "e1c038cf-..."}
```

A client that waits for the body to close hangs until it times out. `api_client.py`
reads the stream line by line and concatenates the tokens, skipping keep-alives,
comments and malformed events.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No module named autonomous` | running from inside `autonomous/` | `cd ..` |
| Every scenario fails instantly | LaRuche not running, or selectors changed | send a message by hand at :5173 |
| Verdicts say `passthrough` | judge not active | add `--ollama-judge`; check `ollama list` |
| `GraphRecursionError` | corpus larger than the transition limit allows | `recursion_limit` in `run.py` (set to 1000) |
| Every scenario scores 5.0 | judge is not seeing the replies | results must carry `reply_text` |
| Mojibake when reading evidence | Windows reads UTF-8 files as cp1252 | `open(path, encoding="utf-8")` |
| `--channel api` opens a browser | `channel` not threaded into the state | check `new_state(...)` in `run.py` |

### A note on suspiciously good results

Two separate bugs in this project produced falsely perfect scores — once by sending
empty messages, once by judging replies the judge could not see. Both times the tell
was the same: an implausibly high pass rate with internally contradictory evidence
(a reason saying "does not address the intent" attached to a score of 5.0).

**Verify a result that looks too good before reporting it.** Read the saved evidence
for one scenario and check the input and reply are what you expect.
