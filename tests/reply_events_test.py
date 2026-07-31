"""Checks that the reply events docs/wire.md documents are ones Welt renders.

This direction has no machine-readable specification — Welt is the receiving
side, and what arrives is decided by the agent frameworks and AWS — so the
page is the whole of it, and these tests are what holds the page and the code
together. Each event the page documents goes through the parsing a real
stream gets, and an interrupt's reason carries on into the rendering that
decides its widgets: the seam between the two modules, which neither
module's own tests cross.
"""

from __future__ import annotations

import pytest

from app.interrupt_logic import (
    DEFAULT_OPTIONS,
    InterruptInput,
    InterruptOption,
    derive_interrupt_prompt,
)
from app.stream_logic import Interrupt, parse_stream_event


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
def test_an_event_the_contract_documents_is_one_welt_renders(event: dict):
    assert parse_stream_event(event) is not None


def test_the_documented_structured_reason_renders_as_its_widgets():
    event = {
        "interrupt": {
            "id": "i-1",
            "name": "deploy",
            "reason": {
                "message": "Deploy to prod?",
                "options": [
                    {"value": "approve", "label": "Deploy", "style": "primary"},
                    {"value": "reject", "label": "Cancel"},
                ],
                "input": {"label": "Or tell me what to change", "multiline": False},
            },
        }
    }

    interrupt = parse_stream_event(event)

    assert isinstance(interrupt, Interrupt)
    prompt = derive_interrupt_prompt(interrupt.reason)
    assert prompt.text == "Deploy to prod?"
    assert prompt.options == (
        InterruptOption(value="approve", label="Deploy", style="primary"),
        InterruptOption(value="reject", label="Cancel"),
    )
    assert prompt.input == InterruptInput(
        label="Or tell me what to change", multiline=False
    )


def test_a_reason_the_page_calls_non_structured_gets_the_default_buttons():
    interrupt = parse_stream_event(
        {"interrupt": {"id": "i-1", "name": "deploy", "reason": "Deploy?"}}
    )

    assert isinstance(interrupt, Interrupt)
    prompt = derive_interrupt_prompt(interrupt.reason)
    assert prompt.text == "Deploy?"
    assert prompt.options == DEFAULT_OPTIONS
    assert prompt.input is None
