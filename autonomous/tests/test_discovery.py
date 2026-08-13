"""Discovery tests — a fake DOM stands in for the live app. No browser needed."""

from __future__ import annotations

from autonomous.discovery.tools import discover_chat


class El:
    def __init__(self, text="", displayed=True):
        self._text, self._displayed = text, displayed
        self.typed = []

    @property
    def text(self):
        return self._text

    def is_displayed(self):
        return self._displayed

    def clear(self):
        self.typed.append("<clear>")

    def send_keys(self, k):
        self.typed.append(k)


class Driver:
    """dom maps css -> [elements]. `swap_on_type` emulates LaRuche's composer:
    the send button only exists once the input has text."""

    def __init__(self, dom, swap_on_type=False):
        self.dom = dom
        self.swap_on_type = swap_on_type
        self.has_text = False
        self.visited = []

    def get(self, url):
        self.visited.append(url)

    def find_elements(self, _by, css):
        if self.swap_on_type:
            if css == "button[aria-label*='send' i]" and not self.has_text:
                return []            # voice button is showing instead
            if css == "button.composer-primary:not(.composer-voice)" and not self.has_text:
                return []
        els = self.dom.get(css, [])
        if css.endswith("composer-input") or "composer-input" in css:
            for e in els:            # typing flips the composer state
                if e.typed and any(t != "<clear>" for t in e.typed):
                    self.has_text = True
        return els

    def quit(self):
        pass


LARUCHE_DOM = {
    "input.composer-input": [El()],
    "button[aria-label*='send' i]": [El()],
    "button.composer-primary:not(.composer-voice)": [El()],
    ".assistant-answer": [El("Hello! I'm your LaRuche advisor.")],
}


def test_discovers_the_three_core_targets():
    d = Driver(dict(LARUCHE_DOM))
    r = discover_chat(d, "http://localhost:5173", settle_s=0)

    assert r.ok
    assert set(r.selectors) == {"chat_input", "send_button", "reply"}
    assert d.visited == ["http://localhost:5173/chat"]


def test_reply_target_addresses_the_newest_message():
    r = discover_chat(Driver(dict(LARUCHE_DOM)), "http://x", settle_s=0)
    assert r.targets["reply"]["index"] == -1     # newest reply, not the first


def test_welcome_message_is_recorded_as_a_placeholder():
    """So scenarios know to wait for the reply to CHANGE from the greeting."""
    r = discover_chat(Driver(dict(LARUCHE_DOM)), "http://x", settle_s=0)
    assert any("LaRuche advisor" in p for p in r.placeholders)
    assert "thinking..." in r.placeholders          # streaming placeholder too


def test_probe_typing_reveals_the_send_button():
    """The voice-button trap: send only renders once the composer has text."""
    d = Driver(dict(LARUCHE_DOM), swap_on_type=True)
    r = discover_chat(d, "http://x", settle_s=0)

    assert "send_button" in r.selectors      # found only because we typed first
    assert r.ok


def test_probe_text_is_cleared_afterwards():
    d = Driver(dict(LARUCHE_DOM))
    discover_chat(d, "http://x", probe_text="hello", settle_s=0)
    typed = d.dom["input.composer-input"][0].typed
    assert "hello" in typed
    assert typed[-1] == "<clear>"            # page left as we found it


def test_aria_label_preferred_over_class():
    """Semantic signals rank above structural ones."""
    r = discover_chat(Driver({
        "input[aria-label*='message' i]": [El()],
        "input.composer-input": [El()],
        "button[aria-label*='send' i]": [El()],
        ".assistant-answer": [El("hi")],
    }), "http://x", settle_s=0)
    assert r.selectors["chat_input"] == "input[aria-label*='message' i]"


def test_missing_input_reports_not_ok():
    r = discover_chat(Driver({".assistant-answer": [El("hi")]}), "http://x", settle_s=0)
    assert not r.ok
    assert any("no chat input" in w for w in r.warnings)


def test_hidden_elements_lose_to_visible_ones():
    r = discover_chat(Driver({
        "input.composer-input": [El(displayed=False)],
        "input[type='text']": [El(displayed=True)],
        "button[aria-label*='send' i]": [El()],
        ".assistant-answer": [El("hi")],
    }), "http://x", settle_s=0)
    assert r.selectors["chat_input"] == "input[type='text']"


def test_spec_shape_is_what_the_graph_expects():
    spec = discover_chat(Driver(dict(LARUCHE_DOM)), "http://x", settle_s=0).to_spec()
    assert set(spec) >= {"base_url", "routes", "selectors", "targets"}
    assert spec["routes"]["chat"] == "/chat"
    # the Validator whitelists against spec["selectors"]
    assert "chat_input" in spec["selectors"]
