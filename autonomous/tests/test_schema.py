"""Contract tests: these are the guarantees Generator/Validator rely on."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autonomous.dsl.schema import Scenario

EXAMPLE = Path(__file__).parents[1] / "dsl" / "examples" / "smoke_chat.json"


def _base(**over):
    doc = {
        "id": "T1",
        "base_url": "http://localhost:5599",
        "targets": {"input": {"css": "input.composer-input"}},
        "steps": [{"do": "wait_visible", "target": "input"}],
    }
    doc.update(over)
    return doc


def test_example_scenario_is_valid():
    s = Scenario.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))
    assert s.id == "SMOKE_CHAT_001"
    assert s.targets["reply"].index == -1  # newest reply addressing


def test_unknown_verb_rejected():
    with pytest.raises(ValidationError):
        Scenario.model_validate(_base(steps=[{"do": "hack_the_planet"}]))


def test_unknown_target_reference_rejected():
    """Validator stage 0 lives in the schema itself."""
    with pytest.raises(ValidationError, match="unknown targets"):
        Scenario.model_validate(
            _base(steps=[{"do": "click", "target": "ghost_button"}])
        )


def test_timeout_capped_at_30s():
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            _base(steps=[{"do": "wait_visible", "target": "input", "timeout_s": 120}])
        )


def test_navigate_must_be_relative_path():
    """A scenario can never send the browser off-site."""
    with pytest.raises(ValidationError):
        Scenario.model_validate(_base(steps=[{"do": "navigate", "path": "https://evil.com"}]))


def test_schema_export_for_generator():
    """The Generator will receive this JSON Schema for constrained generation."""
    js = Scenario.model_json_schema()
    assert "steps" in js["properties"]
    json.dumps(js)  # must be serialisable to embed in a prompt
