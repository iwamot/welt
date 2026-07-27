"""Checks that what the reply schema describes is what Welt renders.

`schema/reply-events.schema.json` is the target an agent-side adapter emits
against, so an event the schema calls renderable had better render. The
reverse does not hold: Welt takes what it recognizes and ignores the rest,
which is looser than the schema describes.

The structured interrupt reason is the exception — Welt matches it
all-or-nothing, so the schema and the rendering agree in both directions,
and the boundaries are checked from both sides here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.interrupt_logic import DEFAULT_OPTIONS, derive_interrupt_prompt
from app.stream_logic import parse_stream_event

SCHEMA = json.loads(
    (Path(__file__).parent.parent / "schema" / "reply-events.schema.json").read_text()
)
REASON_SCHEMA = {"$ref": "#/$defs/structuredReason", "$defs": SCHEMA["$defs"]}


def is_valid(event: dict) -> bool:
    return Draft202012Validator(SCHEMA).is_valid(event)


def is_valid_reason(reason: object) -> bool:
    return Draft202012Validator(REASON_SCHEMA).is_valid(reason)


def renders_structured(reason: object) -> bool:
    return derive_interrupt_prompt(reason).options != DEFAULT_OPTIONS


def test_the_schema_itself_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize(
    "event",
    [
        {"data": "hello"},
        {"current_tool_use": {"name": "current_time", "toolUseId": "t-1"}},
        {"tool_result": {"toolUseId": "t-1", "status": "success"}},
        {"tool_result": {"toolUseId": "t-1", "status": "error"}},
        {"file": {"name": "chart.png", "bytes": "aW1n"}},
        {"interrupt": {"id": "i-1", "name": "deploy", "reason": "Deploy?"}},
        {"error": "the agent raised"},
    ],
)
def test_an_event_the_schema_describes_is_one_welt_renders(event: dict):
    assert is_valid(event)
    assert parse_stream_event(event) is not None


def test_an_event_may_carry_keys_beyond_the_ones_welt_reads():
    event = {"current_tool_use": {"name": "t", "toolUseId": "t-1", "input": {"q": 1}}}

    assert is_valid(event)
    assert parse_stream_event(event) is not None


@pytest.mark.parametrize(
    "event",
    [{}, {"result": {"stop_reason": "end_turn"}}],
)
def test_an_event_welt_reads_nothing_from_is_still_an_event(event: dict):
    assert is_valid(event)
    assert parse_stream_event(event) is None


@pytest.mark.parametrize(
    "event",
    [
        {"data": ""},
        {"file": {"name": "", "bytes": "aW1n"}},
        {"file": {"name": "chart.png"}},
        {"interrupt": {"name": "deploy"}},
        {"tool_result": {"toolUseId": "t-1", "status": "cancelled"}},
    ],
)
def test_a_key_welt_reads_carrying_the_wrong_shape_is_not_valid(event: dict):
    assert not is_valid(event)


@pytest.mark.parametrize(
    "reason",
    [
        {"message": "Deploy?", "options": [{"value": "approve"}]},
        {
            "message": "Deploy?",
            "options": [{"value": "approve", "label": "Ship", "style": "primary"}],
        },
        {"message": "Deploy?", "input": {}},
        {"message": "Deploy?", "input": {"label": "Why", "multiline": True}},
        {
            "message": "Deploy?",
            "options": [{"value": "approve"}],
            "input": {"label": "Or say why"},
        },
        {"message": "Deploy?", "options": [{"value": "v"}] * 25},
        {"message": "Deploy?", "options": [{"value": "v" * 1800}]},
    ],
)
def test_a_reason_the_schema_describes_renders_as_widgets(reason: dict):
    assert is_valid_reason(reason)
    assert renders_structured(reason)


@pytest.mark.parametrize(
    "reason",
    [
        "Deploy?",
        {"message": "Deploy?"},
        {"options": [{"value": "approve"}]},
        {"message": "", "options": [{"value": "approve"}]},
        {"message": "Deploy?", "options": []},
        {"message": "Deploy?", "options": [{"value": "v"}] * 26},
        {"message": "Deploy?", "options": [{"value": "v" * 1801}]},
        {"message": "Deploy?", "options": [{"value": ""}]},
        {"message": "Deploy?", "options": [{"value": "v", "style": "warning"}]},
        {"message": "Deploy?", "options": [{"value": "v", "labl": "typo"}]},
        {"message": "Deploy?", "input": {"multiline": "yes"}},
        {"message": "Deploy?", "input": {"lable": "typo"}},
        {"message": "Deploy?", "options": [{"value": "v"}], "extra": 1},
    ],
)
def test_a_reason_the_schema_rejects_falls_back_to_the_default_rendering(
    reason: object,
):
    assert not is_valid_reason(reason)
    assert not renders_structured(reason)
