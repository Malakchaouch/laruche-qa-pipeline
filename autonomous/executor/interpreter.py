"""
DSL interpreter — executes a validated Scenario in a real browser.

Deterministic by construction: no LLM anywhere in this module. The SLMs
propose scenarios; this interpreter is the only thing allowed to touch the
browser, so an invalid scenario can fail *validation* but can never execute
arbitrary code (contrast with v1's generated-Playwright-script approach).

Evidence discipline (matches the architecture diagram):
- screenshot after every step -> <evidence_dir>/screenshots/NNN_<verb>.png
- elements are visually highlighted (orange outline) before interaction
- full ScenarioRunResult persisted as <evidence_dir>/result.json

Failure classification:
- assert_failed / timeout      -> product-side signal (SUT misbehaved or too slow)
- target_not_found             -> a *validated* target vanished (UI changed / crashed)
- tool_error                   -> WebDriver/infra problem, never counted as a SUT bug
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from ..dsl.schema import Scenario, ScenarioRunResult, StepResult

DEFAULT_POLL_S = 0.25
_KEYMAP = {"ENTER": Keys.ENTER, "ESCAPE": Keys.ESCAPE, "TAB": Keys.TAB}

_HIGHLIGHT_JS = (
    "arguments[0].style.outline='3px solid #f97316';"
    "arguments[0].style.outlineOffset='2px';"
)


class _StepFailure(Exception):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolve(driver, scenario: Scenario, name: str):
    """Return the element for a named target, or None if absent right now."""
    tgt = scenario.targets[name]
    els = driver.find_elements(By.CSS_SELECTOR, tgt.css)
    if not els:
        return None
    idx = tgt.index if tgt.index >= 0 else len(els) + tgt.index
    return els[idx] if 0 <= idx < len(els) else None


def _poll(fn, timeout_s: float, poll_s: float):
    """Poll fn() until truthy or deadline; return value or None on expiry."""
    deadline = time.monotonic() + timeout_s
    while True:
        val = fn()
        if val:
            return val
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


def _require(driver, scenario, name, timeout_s, poll_s):
    """Element must appear within timeout_s or the target is declared missing."""
    el = _poll(lambda: _resolve(driver, scenario, name), timeout_s, poll_s)
    if el is None:
        raise _StepFailure(
            "target_not_found",
            f"target '{name}' ({scenario.targets[name].css}) not found within {timeout_s}s",
        )
    return el


def _highlight(driver, el) -> None:
    try:
        driver.execute_script(_HIGHLIGHT_JS, el)
    except WebDriverException:
        pass  # highlighting is evidence sugar, never a reason to fail


# ── step execution ────────────────────────────────────────────────────────────


def _exec_step(driver, scenario: Scenario, step, poll_s: float) -> str:
    """Execute one step; return an info string. Raise _StepFailure on failure."""
    do = step.do

    if do == "navigate":
        driver.get(scenario.base_url.rstrip("/") + step.path)
        return step.path

    if do == "wait_visible":
        def visible():
            el = _resolve(driver, scenario, step.target)
            return el if (el is not None and el.is_displayed()) else None
        el = _poll(visible, step.timeout_s, poll_s)
        if el is None:
            raise _StepFailure(
                "timeout", f"'{step.target}' not visible within {step.timeout_s}s"
            )
        _highlight(driver, el)
        return step.target

    if do == "click":
        el = _require(driver, scenario, step.target, step.timeout_s, poll_s)
        _highlight(driver, el)
        el.click()
        return step.target

    if do == "type":
        el = _require(driver, scenario, step.target, step.timeout_s, poll_s)
        _highlight(driver, el)
        if step.clear:
            el.clear()
        el.send_keys(step.text)
        return f"{step.target} <- {step.text[:40]!r}"

    if do == "press":
        el = _require(driver, scenario, step.target, step.timeout_s, poll_s)
        el.send_keys(_KEYMAP[step.key])
        return f"{step.key} -> {step.target}"

    if do == "scroll_into_view":
        el = _require(driver, scenario, step.target, step.timeout_s, poll_s)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        return step.target

    if do == "scroll_page":
        driver.execute_script(f"window.scrollBy(0, {int(step.dy)});")
        return f"dy={step.dy}"

    if do == "wait_text_stable":
        deadline = time.monotonic() + step.timeout_s
        # Baseline: text present when the step begins. With must_change=True we
        # refuse to settle until the text differs from this (e.g. a chat welcome
        # message must be replaced by a genuinely new reply).
        baseline = ""
        if getattr(step, "must_change", False):
            el0 = _resolve(driver, scenario, step.target)
            baseline = ((el0.text or "").strip() if el0 is not None else "")
        last_text, last_change = "", time.monotonic()
        while True:
            el = _resolve(driver, scenario, step.target)
            text = (el.text or "").strip() if el is not None else ""
            now = time.monotonic()
            if text != last_text:
                last_text, last_change = text, now
            elif (
                len(text) >= step.min_chars
                and (not getattr(step, "must_change", False) or text != baseline)
                and (now - last_change) >= step.stable_s
            ):
                return f"{step.target} stable ({len(text)} chars)"
            if now >= deadline:
                raise _StepFailure(
                    "timeout",
                    f"'{step.target}' text not stable within {step.timeout_s}s "
                    f"(last: {text[:60]!r})",
                )
            time.sleep(poll_s)

    if do == "assert_visible":
        el = _resolve(driver, scenario, step.target)
        if el is None or not el.is_displayed():
            raise _StepFailure("assert_failed", f"'{step.target}' is not visible")
        return step.target

    if do == "assert_text_not_empty":
        el = _resolve(driver, scenario, step.target)
        text = (el.text or "").strip() if el is not None else ""
        if not text:
            raise _StepFailure("assert_failed", f"'{step.target}' has no text")
        return f"{step.target} ({len(text)} chars)"

    if do == "assert_text_contains":
        el = _resolve(driver, scenario, step.target)
        text = (el.text or "") if el is not None else ""
        hay, needle = (
            (text, step.text) if step.case_sensitive else (text.lower(), step.text.lower())
        )
        if needle not in hay:
            raise _StepFailure(
                "assert_failed",
                f"'{step.target}' does not contain {step.text!r} (got {text[:60]!r})",
            )
        return step.target

    if do == "screenshot":
        return step.label  # the auto-capture below does the actual shot

    if do == "sleep":
        time.sleep(step.seconds)
        return f"{step.seconds}s"

    raise _StepFailure("tool_error", f"unknown verb {do!r}")  # unreachable if validated


_FAILURE_KIND = {
    "assert_failed": "assertion",
    "timeout": "timeout",
    "target_not_found": "target_not_found",
    "tool_error": "tool_error",
}


# ── scenario execution ────────────────────────────────────────────────────────


def run_scenario(
    driver,
    scenario: Scenario,
    evidence_dir: str | Path,
    poll_s: float = DEFAULT_POLL_S,
) -> ScenarioRunResult:
    evidence = Path(evidence_dir)
    shots = evidence / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)

    result = ScenarioRunResult(
        scenario_id=scenario.id, verdict="PASS", evidence_dir=str(evidence)
    )
    t_run = time.monotonic()

    for i, step in enumerate(scenario.steps):
        t0 = time.monotonic()
        status, detail = "ok", ""
        try:
            detail = _exec_step(driver, scenario, step, poll_s) or ""
        except _StepFailure as f:
            status, detail = f.status, f.detail
        except WebDriverException as e:
            status, detail = "tool_error", f"{type(e).__name__}: {e}"

        label = getattr(step, "label", None) if step.do == "screenshot" else None
        shot = shots / f"{i:03d}_{step.do}{'_' + label if label else ''}.png"
        try:
            driver.save_screenshot(str(shot))
        except Exception:
            shot = Path("")  # never fail a step because a screenshot failed

        # Capture the assistant reply text so the Judge can score real content.
        # Any step that targets an element whose text we read is a good moment.
        if step.do in ("wait_text_stable", "assert_text_not_empty", "assert_text_contains"):
            try:
                el = _resolve(driver, scenario, step.target)
                if el is not None:
                    text = (el.text or "").strip()
                    if text:
                        result.reply_text = text
            except Exception:
                pass  # evidence capture must never break execution

        result.steps.append(
            StepResult(
                index=i,
                do=step.do,
                status=status,  # type: ignore[arg-type]
                ms=round((time.monotonic() - t0) * 1000, 1),
                detail=detail,
                screenshot=str(shot) if str(shot) else "",
            )
        )

        if status != "ok":
            result.verdict = "FAIL"
            result.failure_kind = _FAILURE_KIND[status]  # type: ignore[assignment]
            result.failure_step = i
            break

    result.total_ms = round((time.monotonic() - t_run) * 1000, 1)
    (evidence / "result.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    return result
