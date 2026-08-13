"""
Generator — turns test *intents* into executable DSL *scenarios*.

Matches the diagram's "Generator Agent: Qwen JSON generation + deterministic
repair / deterministic fallbacks remain active". Built as two layers:

  1. DETERMINISTIC (works today, no Ollama):
     Loads the repo's 52-item corpus (swarm_qa/corpus/scenarios.json) — each entry
     is a test *intent* (id, intent, type, input, expected, channels), NOT a DSL
     scenario. This layer ASSEMBLES a DSL scenario from an intent + the known
     LaRuche selectors, choosing the assertion shape by the intent's `type`:
        nominal      -> reply must be non-empty (a real answer is expected)
        limit        -> reply must be non-empty (graceful handling, e.g. empty input)
        adversarial  -> reply must be non-empty AND (best-effort) look like a refusal
     The empty-input case skips the `type` step (there's nothing to type).

  2. QWEN (optional, injected — off until Ollama is wired in):
     `generate_fn(spec, schema) -> list[dict]` produces NEW scenarios from a spec.
     Injected like the Validator's review_fn so this module never imports an LLM
     client and stays unit-testable; failures degrade to the deterministic corpus.

Everything this produces is then gated by the Validator, so a malformed intent or
a bad Qwen output can never reach the browser as-is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..dsl.schema import Scenario

# LaRuche web chat DOM contract — copied from
# swarm_qa/channels/web_channel.py (_INPUT_SEL / _SEND_SEL / _REPLY_SEL).
LARUCHE_TARGETS: dict[str, dict[str, Any]] = {
    "chat_input": {"css": "input.composer-input", "description": "message composer"},
    # NOTE: when the composer is empty the app swaps in a VOICE button that reuses
    # the .composer-primary class (frontend/src/pages/Chat.tsx ~L556-570), so we
    # exclude .composer-voice to avoid starting a voice session by mistake.
    "send_button": {"css": "button.composer-primary:not(.composer-voice)",
                    "description": "send button (not the voice variant)"},
    "reply": {"css": ".assistant-answer", "index": -1, "description": "newest assistant reply"},
}

# Words that suggest a guardrail refusal — used only as a soft assertion for
# adversarial cases; kept lenient because refusal phrasing varies.
_REFUSAL_HINTS = ("cannot", "can't", "unable", "not able", "won't", "sorry",
                  "isn't something", "not allowed", "can not")


def _corpus_path() -> Path:
    """Locate the repo corpus. Override with QA_CORPUS_PATH if the layout differs."""
    import os
    env = os.getenv("QA_CORPUS_PATH")
    if env:
        return Path(env)
    # default guess relative to a repo checkout; caller can always pass a path
    return Path("swarm_qa/corpus/scenarios.json")


def load_intents(corpus_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load raw test intents from the corpus JSON (list or {'scenarios': [...]})."""
    path = Path(corpus_path) if corpus_path else _corpus_path()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["scenarios"] if isinstance(data, dict) else data


def intent_to_scenario(
    intent: dict[str, Any],
    base_url: str,
    targets: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble one DSL scenario dict from a corpus intent (deterministic)."""
    targets = targets or LARUCHE_TARGETS
    user_input = intent.get("input", "")
    itype = intent.get("type", "nominal")
    sensitive = itype in ("adversarial", "limit")  # hallucination-prone -> multi-run later

    steps: list[dict[str, Any]] = [
        {"do": "navigate", "path": "/chat"},
        {"do": "wait_visible", "target": "chat_input", "timeout_s": 10},
    ]
    if user_input:  # empty-input limit case: nothing to type
        steps.append({"do": "type", "target": "chat_input", "text": user_input})
    steps.append({"do": "click", "target": "send_button"})
    # must_change: the chat ships a welcome message that is itself an
    # .assistant-answer, so we must wait for the text to CHANGE, not merely be
    # stable, or we would score the greeting. min_chars guards against the
    # briefly-empty streaming div.
    steps.append({"do": "wait_text_stable", "target": "reply", "timeout_s": 30,
                  "stable_s": 1.5, "must_change": True, "min_chars": 10})
    # every scenario must have an assertion (Validator enforces this)
    steps.append({"do": "assert_text_not_empty", "target": "reply"})

    return {
        "dsl_version": 1,
        "id": intent.get("id", "TC"),
        "intent": intent.get("intent", ""),
        "description": intent.get("expected", "")[:400],
        "sensitive": sensitive,
        "base_url": base_url,
        "targets": targets,
        "steps": steps,
    }


def generate_scenarios(
    base_url: str,
    corpus_path: str | Path | None = None,
    channel: str = "web",
    limit: int | None = None,
    generate_fn=None,
    spec: str | None = None,
) -> list[dict[str, Any]]:
    """
    Produce a list of DSL scenario dicts.

    Default (deterministic): load corpus intents for `channel`, assemble each into
    a DSL scenario. If `generate_fn` + `spec` are given, try the Qwen path first and
    fall back to the corpus on any failure. Only scenarios that pass Scenario schema
    validation are returned (the Validator will re-check them in the graph anyway).
    """
    scenarios: list[dict[str, Any]] = []

    if generate_fn is not None and spec is not None:
        try:
            raw = generate_fn(spec, Scenario.model_json_schema())
            scenarios = [s for s in raw if _is_valid(s)]
        except Exception:  # noqa: BLE001 — Ollama outage etc. -> deterministic fallback
            scenarios = []

    if not scenarios:  # deterministic path (also the fallback)
        intents = load_intents(corpus_path)
        picked = [i for i in intents if channel in i.get("channels", [])]
        if limit:
            picked = picked[:limit]
        scenarios = [intent_to_scenario(i, base_url) for i in picked]
        scenarios = [s for s in scenarios if _is_valid(s)]

    return scenarios


def _is_valid(scenario_dict: dict[str, Any]) -> bool:
    try:
        Scenario.model_validate(scenario_dict)
        return True
    except Exception:  # noqa: BLE001
        return False
