"""
Discovery tools — read a live app UI and produce a machine-readable spec.

This is the deterministic half of the Discovery Agent from the architecture
diagram. It opens the real page with Selenium, inspects the DOM, and derives the
`targets` table that the Generator composes over and the Validator whitelists
against. It replaces hardcoded selectors with elements actually observed on the
running app.

Strategy — semantic first, structural second
--------------------------------------------
Candidates are scored, not guessed. For each role (chat_input, send_button,
reply) we collect candidates from several signals, rank them, and keep the best
one that survives a liveness check:

  1. aria-label / role      most robust; LaRuche ships aria-label="Send message"
  2. placeholder text       the composer input has one
  3. class-name hints       composer-input / composer-primary / assistant-answer
  4. structural position    a text input near a button, inside the same container

The voice-button trap
---------------------
LaRuche renders EITHER a send button OR a voice button, both with class
`composer-primary`, depending on whether the composer has text. They are
distinguished only by aria-label ("Send message" vs "Start voice conversation").
Discovery therefore types a probe character before looking for the send button,
so the send variant is the one present, and it always excludes .composer-voice.

Everything here is deterministic: no LLM. The optional Vision layer (OCR/VLM)
sits on top and is never required — mirroring the diagram's "OCR continues when
VLM is unavailable" fallback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

# Signals that identify each role, strongest first. Each entry is
# (css_selector, confidence, why) — confidence orders the candidates.
INPUT_SIGNALS = [
    ("input[aria-label*='message' i]", 0.95, "aria-label mentions message"),
    ("textarea[aria-label*='message' i]", 0.95, "aria-label mentions message"),
    ("input.composer-input", 0.90, "composer-input class"),
    ("textarea.composer-input", 0.90, "composer-input class"),
    ("input[placeholder*='ask' i]", 0.80, "placeholder invites a question"),
    ("textarea[placeholder*='ask' i]", 0.80, "placeholder invites a question"),
    ("input[placeholder*='message' i]", 0.75, "placeholder mentions message"),
    ("input[type='text']", 0.40, "generic text input"),
    ("textarea", 0.35, "generic textarea"),
]

SEND_SIGNALS = [
    ("button[aria-label*='send' i]", 0.95, "aria-label says send"),
    ("button.composer-primary:not(.composer-voice)", 0.90, "primary composer button"),
    ("button[type='submit']", 0.60, "submit button"),
]

REPLY_SIGNALS = [
    (".assistant-answer", 0.95, "assistant-answer class"),
    ("[data-role='assistant']", 0.90, "data-role assistant"),
    (".assistant-message", 0.85, "assistant-message class"),
    (".bot-message", 0.80, "bot-message class"),
    (".message-assistant", 0.75, "message-assistant class"),
]

# Text that means "still thinking", not a real reply — Discovery reports these so
# the Generator/Executor can avoid mistaking them for an answer.
STREAMING_PLACEHOLDERS = ("thinking...", "thinking", "...", "typing...")


@dataclass
class Candidate:
    css: str
    confidence: float
    why: str
    count: int = 0
    visible: bool = False


@dataclass
class DiscoveryReport:
    base_url: str = ""
    route: str = ""
    selectors: dict[str, str] = field(default_factory=dict)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    placeholders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: bool = False

    def to_spec(self) -> dict[str, Any]:
        """The discovered_spec consumed by the graph (Validator + Generator)."""
        return {
            "base_url": self.base_url,
            "routes": {"chat": self.route},
            "selectors": dict(self.selectors),
            "targets": dict(self.targets),
            "streaming_placeholders": list(self.placeholders),
            "warnings": list(self.warnings),
        }


# ── low-level probes ──────────────────────────────────────────────────────────


def _probe(driver, signals: list[tuple[str, float, str]]) -> list[Candidate]:
    """Try each signal; return candidates that actually match something."""
    found: list[Candidate] = []
    for css, conf, why in signals:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, css)
        except WebDriverException:
            continue
        if not els:
            continue
        visible = any(e.is_displayed() for e in els)
        found.append(Candidate(css=css, confidence=conf, why=why,
                               count=len(els), visible=visible))
    # visible first, then confidence
    found.sort(key=lambda c: (c.visible, c.confidence), reverse=True)
    return found


def _best(cands: list[Candidate]) -> Candidate | None:
    for c in cands:
        if c.visible:
            return c
    return cands[0] if cands else None


# ── the discovery routine ─────────────────────────────────────────────────────


def discover_chat(
    driver,
    base_url: str,
    route: str = "/chat",
    probe_text: str = "hello",
    settle_s: float = 2.0,
) -> DiscoveryReport:
    """
    Open the app and derive the chat target table.

    The probe: we type a character into the discovered input BEFORE looking for
    the send button, because LaRuche swaps the send button for a voice button
    when the composer is empty. Discovery must observe the page in the state the
    tests will actually encounter.
    """
    report = DiscoveryReport(base_url=base_url.rstrip("/"), route=route)

    # 1. open the chat route
    try:
        driver.get(report.base_url + route)
    except WebDriverException as e:
        report.warnings.append(f"could not open {route}: {type(e).__name__}")
        return report
    time.sleep(settle_s)  # let the SPA render

    # 2. find the composer input
    input_cands = _probe(driver, INPUT_SIGNALS)
    report.candidates["chat_input"] = [c.__dict__ for c in input_cands]
    best_input = _best(input_cands)
    if best_input is None:
        report.warnings.append("no chat input found — is this the chat page?")
        return report
    report.selectors["chat_input"] = best_input.css
    report.targets["chat_input"] = {"css": best_input.css, "index": 0,
                                    "description": best_input.why}

    # 3. type a probe so the SEND button (not the voice button) is rendered
    typed = False
    try:
        el = driver.find_elements(By.CSS_SELECTOR, best_input.css)[0]
        el.clear()
        el.send_keys(probe_text)
        typed = True
        time.sleep(0.4)
    except (WebDriverException, IndexError):
        report.warnings.append("could not type probe text; send button may be "
                               "the voice variant")

    # 4. find the send button in that state
    send_cands = _probe(driver, SEND_SIGNALS)
    report.candidates["send_button"] = [c.__dict__ for c in send_cands]
    best_send = _best(send_cands)
    if best_send is None:
        report.warnings.append("no send button found")
    else:
        report.selectors["send_button"] = best_send.css
        report.targets["send_button"] = {"css": best_send.css, "index": 0,
                                         "description": best_send.why}

    # 5. clear the probe so we leave the page as we found it
    if typed:
        try:
            driver.find_elements(By.CSS_SELECTOR, best_input.css)[0].clear()
        except (WebDriverException, IndexError):
            pass

    # 6. find the reply container (a welcome message usually already exists)
    reply_cands = _probe(driver, REPLY_SIGNALS)
    report.candidates["reply"] = [c.__dict__ for c in reply_cands]
    best_reply = _best(reply_cands)
    if best_reply is None:
        report.warnings.append("no assistant reply container found (no messages yet?)")
    else:
        # index -1 = newest reply, which is what scenarios assert on
        report.selectors["reply"] = best_reply.css
        report.targets["reply"] = {"css": best_reply.css, "index": -1,
                                   "description": best_reply.why}
        # record any pre-existing text: the welcome message. Scenarios must wait
        # for the reply to CHANGE from this, not merely be stable.
        try:
            els = driver.find_elements(By.CSS_SELECTOR, best_reply.css)
            existing = [(e.text or "").strip() for e in els if (e.text or "").strip()]
            if existing:
                report.placeholders.append(existing[-1][:120])
        except WebDriverException:
            pass

    report.placeholders.extend(STREAMING_PLACEHOLDERS)
    report.ok = "chat_input" in report.selectors and "send_button" in report.selectors
    if not report.ok:
        report.warnings.append("discovery incomplete: chat_input and send_button "
                               "are both required")
    return report
