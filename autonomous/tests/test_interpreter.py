"""Interpreter tests — no browser: a FakeDriver simulates the DOM and streaming."""

from __future__ import annotations

from pathlib import Path

from autonomous.dsl.schema import Scenario
from autonomous.executor.interpreter import run_scenario


# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeElement:
    """Element whose .text replays a sequence (simulates SSE streaming)."""

    def __init__(self, texts: list[str] | str = "", displayed: bool = True):
        self._texts = [texts] if isinstance(texts, str) else list(texts)
        self._i = 0
        self._displayed = displayed
        self.actions: list[str] = []

    @property
    def text(self) -> str:
        t = self._texts[min(self._i, len(self._texts) - 1)]
        self._i += 1
        return t

    def is_displayed(self):
        return self._displayed

    def click(self):
        self.actions.append("click")

    def clear(self):
        self.actions.append("clear")

    def send_keys(self, keys):
        self.actions.append(f"keys:{keys}")


class FakeDriver:
    def __init__(self, dom: dict[str, list[FakeElement]]):
        self.dom = dom
        self.visited: list[str] = []
        self.scripts: list[str] = []

    def find_elements(self, _by, css):
        return self.dom.get(css, [])

    def get(self, url):
        self.visited.append(url)

    def execute_script(self, script, *_):
        self.scripts.append(script)

    def save_screenshot(self, path) -> bool:
        Path(path).write_bytes(b"PNG")
        return True


def _scenario(steps, targets=None):
    return Scenario.model_validate(
        {
            "id": "T",
            "base_url": "http://localhost:5599",
            "targets": targets
            or {
                "input": {"css": "input.composer-input"},
                "send": {"css": "button.composer-primary"},
                "reply": {"css": ".assistant-answer", "index": -1},
            },
            "steps": steps,
        }
    )


# ── tests ─────────────────────────────────────────────────────────────────────


def test_happy_path_with_streaming_reply(tmp_path):
    reply = FakeElement(
        ["", "Your", "Your total AUM", "Your total AUM is $20.4M"]  # then stays stable
    )
    inp, btn = FakeElement(), FakeElement()
    driver = FakeDriver(
        {
            "input.composer-input": [inp],
            "button.composer-primary": [btn],
            ".assistant-answer": [FakeElement("old reply"), reply],  # index -1 -> newest
        }
    )
    s = _scenario(
        [
            {"do": "navigate", "path": "/chat"},
            {"do": "wait_visible", "target": "input", "timeout_s": 1},
            {"do": "type", "target": "input", "text": "AUM?"},
            {"do": "click", "target": "send"},
            {"do": "wait_text_stable", "target": "reply", "timeout_s": 2,
             "stable_s": 0.05, "min_chars": 5},
            {"do": "assert_text_contains", "target": "reply", "text": "20.4m"},
        ]
    )
    result = run_scenario(driver, s, tmp_path, poll_s=0.01)

    assert result.verdict == "PASS", result.steps[-1].detail
    assert driver.visited == ["http://localhost:5599/chat"]
    assert "clear" in inp.actions and "keys:AUM?" in inp.actions
    assert "click" in btn.actions
    shots = sorted(p.name for p in (tmp_path / "screenshots").iterdir())
    assert shots[0] == "000_navigate.png" and len(shots) == 6  # one per step
    assert (tmp_path / "result.json").exists()


def test_assertion_failure_stops_and_classifies(tmp_path):
    driver = FakeDriver({".assistant-answer": [FakeElement("The AUM is unknown")]})
    s = _scenario(
        [
            {"do": "assert_text_contains", "target": "reply", "text": "$20.4M"},
            {"do": "screenshot", "label": "never_reached"},
        ],
        targets={"reply": {"css": ".assistant-answer"}},
    )
    result = run_scenario(driver, s, tmp_path, poll_s=0.01)

    assert result.verdict == "FAIL"
    assert result.failure_kind == "assertion"
    assert result.failure_step == 0
    assert len(result.steps) == 1  # halted immediately
    assert result.steps[0].screenshot  # failure evidence captured


def test_wait_visible_timeout(tmp_path):
    driver = FakeDriver({})  # element never appears
    s = _scenario(
        [{"do": "wait_visible", "target": "reply", "timeout_s": 0.05}],
        targets={"reply": {"css": ".assistant-answer"}},
    )
    result = run_scenario(driver, s, tmp_path, poll_s=0.01)
    assert result.verdict == "FAIL" and result.failure_kind == "timeout"


def test_click_on_missing_target_is_target_not_found(tmp_path):
    driver = FakeDriver({})
    s = _scenario(
        [{"do": "click", "target": "send", "timeout_s": 0.05}],
        targets={"send": {"css": "button.composer-primary"}},
    )
    result = run_scenario(driver, s, tmp_path, poll_s=0.01)
    assert result.verdict == "FAIL" and result.failure_kind == "target_not_found"


def test_reply_text_is_captured_for_the_judge(tmp_path):
    """The Judge scores content, so the interpreter must capture the reply."""
    reply = FakeElement(["Your total AUM is $20.4M"] * 6)
    driver = FakeDriver({".assistant-answer": [reply]})
    s = _scenario(
        [{"do": "assert_text_not_empty", "target": "reply"}],
        targets={"reply": {"css": ".assistant-answer"}},
    )
    result = run_scenario(driver, s, tmp_path, poll_s=0.01)
    assert result.verdict == "PASS"
    assert "20.4M" in result.reply_text


def test_must_change_ignores_the_welcome_message(tmp_path):
    """Regression: the chat's greeting is also .assistant-answer and is already
    stable, so without must_change the wait returns instantly on the greeting."""
    greeting = "Hello! I'm your LaRuche advisor. How can I help you today?"
    # element text: greeting for a while, then the real streamed reply
    el = FakeElement([greeting] * 4 + ["AUM: $20.4M across 48 deals"] * 8)
    driver = FakeDriver({".assistant-answer": [el]})
    s = _scenario(
        [{"do": "wait_text_stable", "target": "reply", "timeout_s": 5,
          "stable_s": 0.02, "must_change": True, "min_chars": 10}],
        targets={"reply": {"css": ".assistant-answer"}},
    )
    result = run_scenario(driver, s, tmp_path, poll_s=0.01)
    assert result.verdict == "PASS"
    assert "20.4M" in result.reply_text          # the real reply, not the greeting
    assert "advisor" not in result.reply_text
