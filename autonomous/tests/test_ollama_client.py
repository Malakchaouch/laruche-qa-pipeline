"""Ollama client tests — a fake `ollama` module; no daemon required."""

from __future__ import annotations

import json
import sys
import types

import autonomous.graph.ollama_client as oc


def _install_fake(chat_content: str, models: list[str]) -> None:
    fake = types.ModuleType("ollama")

    class FakeModel:
        def __init__(self, name: str) -> None:
            self.model = name

    class FakeList:
        def __init__(self, names: list[str]) -> None:
            self.models = [FakeModel(n) for n in names]

    class FakeClient:
        def __init__(self, host: str | None = None) -> None:
            self.host = host

        def list(self):
            return FakeList(models)

        def chat(self, model, messages, format, options):
            return {"message": {"content": chat_content}}

    fake.Client = FakeClient
    sys.modules["ollama"] = fake


def teardown_function(_) -> None:
    sys.modules.pop("ollama", None)


def test_available_when_model_present():
    _install_fake("{}", ["qwen2.5:3b"])
    assert oc.is_available("qwen2.5:3b") is True


def test_unavailable_when_model_missing():
    _install_fake("{}", ["llama3.2:1b"])
    assert oc.is_available("qwen2.5:3b") is False


def test_generate_fn_repairs_intents_into_valid_dsl():
    """The model returns loose intents; generate_fn assembles strict DSL."""
    from autonomous.dsl.schema import Scenario
    payload = {"scenarios": [{"id": "G1", "intent": "aum_check", "kind": "nominal",
                              "user_input": "what is my AUM?", "expected": "states AUM"}]}
    _install_fake(json.dumps(payload), ["qwen2.5:3b"])
    out = oc.make_generate_fn("qwen2.5:3b", spec_base_url="http://x")("spec", {})
    assert len(out) == 1
    Scenario.model_validate(out[0])          # must be valid DSL
    assert out[0]["id"] == "G1"
    assert any(s["do"].startswith("assert") for s in out[0]["steps"])


def test_score_fn_computes_mean():
    payload = {"pertinence": 4, "exactitude": 5, "coherence": 3,
               "hallucination": False, "reason": "solid"}
    _install_fake(json.dumps(payload), ["qwen2.5:3b"])
    out = oc.make_score_fn("qwen2.5:3b")({"intent": "x"}, {}, "reply")
    assert out["mean"] == 4.0 and out["hallucination"] is False


def test_score_fn_flags_hallucination():
    payload = {"pertinence": 5, "exactitude": 5, "coherence": 5,
               "hallucination": True, "reason": "invented a figure"}
    _install_fake(json.dumps(payload), ["qwen2.5:3b"])
    out = oc.make_score_fn("qwen2.5:3b")({"intent": "x"}, {}, "reply")
    assert out["hallucination"] is True   # judge turns this into a hard veto


def test_missing_package_raises_so_callers_fall_back():
    sys.modules["ollama"] = types.ModuleType("ollama")  # no .Client
    try:
        oc.make_generate_fn()("spec", {})
    except Exception:
        return  # expected: generator.py catches and uses the corpus
    raise AssertionError("expected failure when ollama is unusable")


def test_judge_prompt_calibration():
    """Lock in the scoring rules that stop qwen penalising correct extra detail."""
    payload = {"pertinence": 5, "exactitude": 5, "coherence": 5,
               "hallucination": False, "reason": "ok"}
    _install_fake(json.dumps(payload), ["qwen2.5:3b"])
    seen = {}
    import ollama
    orig_chat = ollama.Client.chat

    def spy(self, model, messages, format, options):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return orig_chat(self, model, messages, format, options)
    ollama.Client.chat = spy

    oc.make_score_fn("qwen2.5:3b")(
        {"intent": "portfolio_aum", "description": "states AUM"}, {}, "AUM is $20.4M plus sources")

    assert "do NOT lower any score for extra detail" in seen["system"]
    assert "is NOT a hallucination" in seen["system"]      # off-topic != invented
    assert "minimum required" in seen["user"]


def test_adversarial_scenarios_get_a_security_note():
    payload = {"pertinence": 5, "exactitude": 5, "coherence": 5,
               "hallucination": False, "reason": "refused"}
    _install_fake(json.dumps(payload), ["qwen2.5:3b"])
    seen = {}
    import ollama
    orig_chat = ollama.Client.chat

    def spy(self, model, messages, format, options):
        seen["user"] = messages[1]["content"]
        return orig_chat(self, model, messages, format, options)
    ollama.Client.chat = spy

    oc.make_score_fn("qwen2.5:3b")(
        {"intent": "prompt_injection", "description": "refuses", "sensitive": True},
        {}, "Sorry, I cannot reveal that.")
    assert "adversarial/security test" in seen["user"]
