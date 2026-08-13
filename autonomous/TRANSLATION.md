# From v1 Playwright to v2 DSL — the translation, explained

This shows exactly how the **repo's proven web test** became our **first DSL scenario**.
Nothing here is invented: the left column is real code from
`qa-swarm/swarm_qa/channels/web_channel.py` (the `_fallback_script` — your
colleague's known-good Playwright script). The right column is our
`smoke_chat.json`. Read it top to bottom to see the one-to-one mapping.

---

## The core idea in one line

> v1 makes an AI **write browser code**, then guards it with `ast.parse` and a
> subprocess sandbox because generated code is dangerous.
> v2 makes an AI **fill in structured data** (the DSL), which can't be dangerous
> in the first place — a fixed interpreter is the only thing that touches the browser.

---

## Side by side

| # | v1 — repo Playwright (`_fallback_script`) | v2 — our DSL step | What it does |
|---|---|---|---|
| 1 | `page.goto(URL, timeout=30000)` | `{"do": "navigate", "path": "/chat"}` | Open the chat page |
| 2 | `page.wait_for_selector(_INPUT_SEL, timeout=15000)` | `{"do": "wait_visible", "target": "chat_input"}` | Wait for the input box |
| 3 | `page.fill(_INPUT_SEL, MESSAGE)` | `{"do": "type", "target": "chat_input", "text": "..."}` | Type the question |
| 4 | `page.click(_SEND_SEL)` | `{"do": "click", "target": "send_button"}` | Click Send |
| 5 | the `while` loop counting 3 stable reads | `{"do": "wait_text_stable", "target": "reply", "stable_s": 1.5}` | Wait for the streamed reply to finish |
| 6 | `out["actual"] = last` (implicitly non-empty) | `{"do": "assert_text_not_empty", "target": "reply"}` | Check a reply actually arrived |
| 7 | `page.screenshot(path=SHOT)` | `{"do": "screenshot", "label": "final"}` | Capture evidence |

The **selectors** (`input.composer-input`, `button.composer-primary`,
`.assistant-answer`) are copied verbatim from the repo — same three constants
`_INPUT_SEL`, `_SEND_SEL`, `_REPLY_SEL`. That's why our scenario targets the
real LaRuche UI correctly.

---

## The one row that carries the whole architecture: step 5

The repo handles streaming with a hand-rolled loop — read the reply text every
400 ms, and if it's the same 3 times in a row, assume it stopped streaming:

```python
last, stable = "", 0
while time.time() - t0 < 60:
    txt = page.locator(_REPLY_SEL).last.inner_text().strip()
    if txt and txt != "Thinking..." and txt == last:
        stable += 1
        if stable >= 3:
            break
    else:
        stable = 0
    last = txt
    page.wait_for_timeout(400)
```

Every scenario that waited for a reply had to re-implement this loop. In our
DSL, that entire pattern is **one reusable verb** — `wait_text_stable` — and the
loop lives once, inside the interpreter. The AI never rewrites it; it just says
"wait for the reply to stabilise." This is the payoff of a DSL: the tricky,
error-prone logic is written once by a human and reused, instead of regenerated
(and possibly broken) by an SLM on every scenario.

---

## What each file in the package is (brief)

- **`dsl/schema.py`** — the contract. The 13 allowed verbs, the named-target
  rule, the safety caps (30 s max wait, relative paths only). This one file is
  simultaneously the Generator's output format and the Validator's stage-0 check.
- **`executor/interpreter.py`** — the only code allowed to touch the browser.
  Runs a scenario step by step, screenshots each step, and on failure says *why*
  (`assertion` / `timeout` / `target_not_found` / `tool_error`).
- **`executor/driver.py`** — one switch: local Chromium for dev, or the remote
  Selenium Grid (`:4444`, watchable on noVNC `:7900`) for the diagram's setup.
- **`executor/run_scenario.py`** — the command-line runner.
- **`dsl/examples/smoke_chat.json`** — the scenario above.
- **`mock_sut/`** — a fake chat page that streams a reply char-by-char, so you
  can test the interpreter offline without the real app or Ollama.
- **`tests/`** — 10 tests proving the contract holds, no browser needed.

---

## Why v2 is safer, in the terms your supervisor will ask about

| Concern | v1 (repo) | v2 (this) |
|---|---|---|
| Can a generated test run arbitrary code? | Yes — mitigated by `ast.parse` + sandbox | No — only 13 fixed verbs exist |
| Can it invent a fake selector? | Yes | No — steps reference names from a discovered table |
| Can it navigate off-site / hang forever? | Possible | Blocked by the schema (relative paths, 30 s cap) |
| Is a broken test blamed on the chatbot? | Risk of it | No — `tool_error` is a separate failure class |
| Streaming-wait logic | Re-generated per scenario | Written once, reused as one verb |
