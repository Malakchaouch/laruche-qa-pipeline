"""
Selenium scenario DSL — the contract shared by Generator, Validator and Executor.

Design principles
-----------------
1. Steps reference elements by *name* ("target": "chat_input"), never by raw
   selector. The scenario carries a `targets` table (produced by Discovery)
   mapping names to selectors. The Generator composes verbs over names, so it
   cannot invent selectors; the Validator's "known targets" check is set
   membership; and this schema already enforces it (`_check_target_refs`).
2. Closed verb set, bounded parameters. Every wait is capped at
   MAX_STEP_TIMEOUT_S (the 30 s FAIL-taxonomy threshold). `navigate` only
   accepts a relative path joined to `base_url` — a scenario can never send
   the browser off-site.
3. `wait_text_stable` is the streaming-aware wait: the SUT streams its reply
   over SSE, so "the answer is ready" means "the element's text is non-empty
   and has stopped changing for `stable_s` seconds".

This module doubles as the Generator's output contract: feed
`Scenario.model_json_schema()` to the SLM for constrained JSON generation,
and validate + deterministically repair against these models.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

DSL_VERSION = 1

MAX_STEP_TIMEOUT_S = 30.0   # FAIL taxonomy: timeout threshold
MAX_SLEEP_S = 3.0           # sleep is an escape valve for animations, not a wait
MAX_STEPS = 40              # sanity cap on scenario length

TimeoutS = Annotated[float, Field(gt=0, le=MAX_STEP_TIMEOUT_S)]


# ── Targets ───────────────────────────────────────────────────────────────────


class Target(BaseModel):
    """A named UI element. Discovery produces these; steps reference them by name."""

    css: str = Field(min_length=1, description="CSS selector")
    index: int = Field(
        default=0,
        description="Which match to use: 0 = first, -1 = last (e.g. newest chat reply).",
    )
    description: str = ""


# ── Steps (discriminated union on `do`) ───────────────────────────────────────


class Navigate(BaseModel):
    do: Literal["navigate"]
    path: str = Field(pattern=r"^/", description="Path relative to base_url, must start with /")


class WaitVisible(BaseModel):
    do: Literal["wait_visible"]
    target: str
    timeout_s: TimeoutS = 10.0


class Click(BaseModel):
    do: Literal["click"]
    target: str
    timeout_s: TimeoutS = 10.0


class Type(BaseModel):
    do: Literal["type"]
    target: str
    text: str
    clear: bool = True
    timeout_s: TimeoutS = 10.0


class Press(BaseModel):
    do: Literal["press"]
    target: str
    key: Literal["ENTER", "ESCAPE", "TAB"]
    timeout_s: TimeoutS = 10.0


class ScrollIntoView(BaseModel):
    do: Literal["scroll_into_view"]
    target: str
    timeout_s: TimeoutS = 10.0


class ScrollPage(BaseModel):
    do: Literal["scroll_page"]
    dy: int = Field(description="Pixels to scroll vertically; negative scrolls up.")


class WaitTextStable(BaseModel):
    """Wait until the target's text is non-empty and unchanged for `stable_s`.

    `must_change=True` first records the text present when the step starts and
    requires the observed text to DIFFER from it before stability counts. This is
    essential for chat UIs that ship a welcome message: without it, the "last
    .assistant-answer" is the greeting, which is already stable, so the wait
    returns instantly and the Judge ends up scoring the greeting instead of the
    real reply.
    """

    do: Literal["wait_text_stable"]
    target: str
    timeout_s: TimeoutS = MAX_STEP_TIMEOUT_S
    stable_s: Annotated[float, Field(gt=0, le=10.0)] = 1.5
    min_chars: int = Field(default=1, ge=1)
    must_change: bool = False


class AssertVisible(BaseModel):
    do: Literal["assert_visible"]
    target: str


class AssertTextNotEmpty(BaseModel):
    do: Literal["assert_text_not_empty"]
    target: str


class AssertTextContains(BaseModel):
    do: Literal["assert_text_contains"]
    target: str
    text: str = Field(min_length=1)
    case_sensitive: bool = False


class Screenshot(BaseModel):
    do: Literal["screenshot"]
    label: str = "shot"


class Sleep(BaseModel):
    do: Literal["sleep"]
    seconds: Annotated[float, Field(gt=0, le=MAX_SLEEP_S)]


Step = Annotated[
    Union[
        Navigate,
        WaitVisible,
        Click,
        Type,
        Press,
        ScrollIntoView,
        ScrollPage,
        WaitTextStable,
        AssertVisible,
        AssertTextNotEmpty,
        AssertTextContains,
        Screenshot,
        Sleep,
    ],
    Field(discriminator="do"),
]

# Verbs that must reference a target defined in Scenario.targets
_TARGETED = {
    "wait_visible", "click", "type", "press", "scroll_into_view",
    "wait_text_stable", "assert_visible", "assert_text_not_empty",
    "assert_text_contains",
}


# ── Scenario ──────────────────────────────────────────────────────────────────


class Scenario(BaseModel):
    dsl_version: Literal[1] = DSL_VERSION
    id: str = Field(min_length=1)
    intent: str = ""
    description: str = ""
    sensitive: bool = Field(
        default=False, description="Hallucination-prone; candidates for multi-run later."
    )
    base_url: str = Field(pattern=r"^https?://")
    targets: dict[str, Target]
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _check_target_refs(self) -> "Scenario":
        """Every targeted step must reference a declared target — Validator stage 0."""
        unknown = [
            f"step {i} ({s.do}) -> '{s.target}'"
            for i, s in enumerate(self.steps)
            if s.do in _TARGETED and s.target not in self.targets
        ]
        if unknown:
            raise ValueError(f"steps reference unknown targets: {'; '.join(unknown)}")
        return self


# ── Execution results (Executor output; Judge + Reporter input) ───────────────

StepStatus = Literal["ok", "assert_failed", "timeout", "target_not_found", "tool_error"]


class StepResult(BaseModel):
    index: int
    do: str
    status: StepStatus
    ms: float = 0.0
    detail: str = ""
    screenshot: str = ""


class ScenarioRunResult(BaseModel):
    scenario_id: str
    verdict: Literal["PASS", "FAIL"]
    failure_kind: Literal[
        "none", "assertion", "timeout", "target_not_found", "tool_error"
    ] = "none"
    failure_step: int | None = None
    total_ms: float = 0.0
    evidence_dir: str = ""
    reply_text: str = ""   # captured assistant reply — what the Judge scores
    steps: list[StepResult] = Field(default_factory=list)
