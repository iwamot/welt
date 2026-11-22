from __future__ import annotations

import base64
import json
import logging
from copy import deepcopy
from itertools import product

from app.stream_logic import (
    FileOutput,
    Interrupt,
    StreamError,
    TextDelta,
    ToolResult,
    ToolUse,
    fills_in_tool_name,
    harness_final_stop_error,
    parse_harness_event,
    parse_sse_data_line,
    parse_stream_event,
)


def test_both_stream_dialects_preserve_text_tools_and_errors():
    texts = (
        "",
        "Hello",
        "Hi",
        "日本語",
        "line\nnext",
        '"\\<@U123>',
        " \t ",
        "  padded  ",
    )
    pairs = []
    for text in texts:
        pairs.append(
            (
                {"data": text},
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"text": text},
                    }
                },
                TextDelta(text=text) if text else None,
            )
        )
    for name, tool_id in product(
        (None, "", "get_weather", "web_search", "日本語"),
        (None, "", "tooluse_abc", "tool-1", "呼出し"),
    ):
        tool = {}
        if name is not None:
            tool["name"] = name
        if tool_id is not None:
            tool["toolUseId"] = tool_id
        pairs.append(
            (
                {"current_tool_use": tool},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 1,
                        "start": {"toolUse": tool},
                    }
                },
                ToolUse(name=name, tool_use_id=tool_id),
            )
        )
    for tool_id, status in product(
        (None, "", "tooluse_abc", "tool-1"), (None, "success", "error")
    ):
        result = {}
        if tool_id is not None:
            result["toolUseId"] = tool_id
        if status is not None:
            result["status"] = status
        pairs.append(
            (
                {"tool_result": result},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 2,
                        "start": {"toolResult": result},
                    }
                },
                ToolResult(tool_use_id=tool_id, error=status == "error"),
            )
        )
    for text in (*texts, "division by zero", "the agent crashed"):
        pairs.append(
            (
                {"error": text},
                {"runtimeClientError": {"message": text}},
                StreamError(message=text or "unknown error"),
            )
        )
    assert parse_harness_event({"runtimeClientError": {}}) == StreamError(
        message="unknown error"
    )
    for wire, harness, expected in pairs:
        for metadata in (
            {},
            {"trace": {"id": "ignored"}, "usage": 42},
            {
                "error_type": "ZeroDivisionError",
                "message": "An error occurred during streaming",
            },
        ):
            for event, parse in (
                (wire | metadata, parse_stream_event),
                (harness | metadata, parse_harness_event),
            ):
                before = deepcopy(event)
                assert parse(event) == expected, event
                assert event == before
                for ascii_only, padding in product((False, True), ("", " ", "\t")):
                    decoded = parse_sse_data_line(
                        f"data:{padding}{json.dumps(event, ensure_ascii=ascii_only)}"
                    )
                    assert decoded == event
                    assert parse(decoded) == expected


def test_file_events_restore_the_original_name_and_every_byte():
    payloads = (
        b"\x89PNG\r\n\x1a\n",
        bytes(range(256)),
        b"\x00",
        b"\xff",
        b"abcde" * 101,
    )
    for name, data in product(
        ("image.png", "日本語.txt", "space and [brackets].bin"), payloads
    ):
        event = {
            "file": {"name": name, "bytes": base64.b64encode(data).decode("ascii")}
        }
        before = deepcopy(event)
        decoded = parse_sse_data_line(f"data: {json.dumps(event)}")
        assert decoded == event
        assert parse_stream_event(decoded) == FileOutput(name=name, data=data)
        assert event == before


# --- parse_sse_data_line -----------------------------------------------------


def test_sse_line_ignores_non_data_lines():
    assert parse_sse_data_line(": keep-alive comment") is None
    assert parse_sse_data_line("") is None
    assert parse_sse_data_line("event: message") is None


def test_sse_line_ignores_empty_data_payload():
    assert parse_sse_data_line("data:") is None
    assert parse_sse_data_line("data:   ") is None


def test_sse_line_ignores_malformed_json():
    assert parse_sse_data_line("data: {not json") is None


def test_sse_line_ignores_non_object_json():
    assert parse_sse_data_line("data: 123") is None
    assert parse_sse_data_line('data: ["a", "b"]') is None


# --- parse_stream_event (the reply events docs/wire.md specifies) ------------


def test_file_event_with_missing_name_is_ignored():
    encoded = base64.b64encode(b"data").decode("ascii")

    assert parse_stream_event({"file": {"bytes": encoded}}) is None
    assert parse_stream_event({"file": {"name": "", "bytes": encoded}}) is None
    assert parse_stream_event({"file": {"name": 1, "bytes": encoded}}) is None


def test_file_event_with_missing_or_malformed_bytes_is_ignored():
    assert parse_stream_event({"file": {"name": "image.png"}}) is None
    assert parse_stream_event({"file": {"name": "image.png", "bytes": b"raw"}}) is None
    assert (
        parse_stream_event({"file": {"name": "image.png", "bytes": "not base64!"}})
        is None
    )


def test_file_event_carrying_no_bytes_is_skipped_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        result = parse_stream_event({"file": {"name": "empty.txt", "bytes": ""}})

    assert result is None
    assert "empty.txt" in caplog.text


def test_non_dict_file_event_is_ignored():
    assert parse_stream_event({"file": "not a dict"}) is None


def test_interrupt_event_is_interrupt():
    event = {
        "interrupt": {
            "id": "i-1",
            "name": "deploy-approval",
            "reason": {"message": "Deploy?", "options": [{"value": "y"}]},
        }
    }

    assert parse_stream_event(event) == Interrupt(
        id="i-1",
        name="deploy-approval",
        reason={"message": "Deploy?", "options": [{"value": "y"}]},
    )


def test_interrupt_without_reason_keeps_none():
    event = {"interrupt": {"id": "i-1", "name": "deploy-approval"}}

    assert parse_stream_event(event) == Interrupt(
        id="i-1", name="deploy-approval", reason=None
    )


def test_interrupt_with_malformed_id_or_name_is_ignored_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_stream_event({"interrupt": {"id": 1, "name": "n"}}) is None
        assert parse_stream_event({"interrupt": {"id": "", "name": "n"}}) is None
        assert parse_stream_event({"interrupt": {"name": "n"}}) is None
        assert parse_stream_event({"interrupt": {"id": "i-1", "name": 1}}) is None
        assert parse_stream_event({"interrupt": {"id": "i-1"}}) is None

    assert len(caplog.records) == 5
    assert "malformed id" in caplog.records[0].message
    assert "i-1" in caplog.records[3].message
    assert "malformed name" in caplog.records[3].message


def test_non_dict_interrupt_event_is_ignored():
    assert parse_stream_event({"interrupt": "not a dict"}) is None


def test_an_event_carrying_no_key_welt_reads_is_ignored():
    # Events do carry keys beyond the six: the AgentCore Runtime SDK's own
    # error event arrives with `error_type` and `message` beside the `error`
    # string. On their own they render nothing.
    event = {
        "error_type": "ZeroDivisionError",
        "message": "An error occurred during streaming",
    }

    assert parse_stream_event(event) is None


# --- parse_harness_event -----------------------------------------------------


def test_harness_reasoning_delta_is_ignored():
    event = {
        "contentBlockDelta": {
            "contentBlockIndex": 0,
            "delta": {"reasoningContent": {"text": "thinking..."}},
        }
    }

    assert parse_harness_event(event) is None


def test_harness_delta_without_body_is_ignored():
    assert parse_harness_event({"contentBlockDelta": {"contentBlockIndex": 0}}) is None


def test_harness_non_tool_block_start_is_ignored():
    assert parse_harness_event({"contentBlockStart": {"start": {}}}) is None
    assert parse_harness_event({"contentBlockStart": {}}) is None


def test_harness_lifecycle_and_metadata_events_are_ignored():
    assert parse_harness_event({"messageStart": {"role": "assistant"}}) is None
    assert parse_harness_event({"messageStop": {"stopReason": "end_turn"}}) is None
    assert parse_harness_event({"metadata": {"usage": {"totalTokens": 42}}}) is None


def test_harness_mid_stream_tool_use_stop_is_ignored():
    assert parse_harness_event({"messageStop": {"stopReason": "tool_use"}}) is None


# --- harness_final_stop_error ------------------------------------------------


def test_final_tool_use_stop_is_an_error():
    result = harness_final_stop_error("tool_use")

    assert isinstance(result, StreamError)
    assert "inline function" in result.message


def test_other_final_stops_are_normal():
    assert harness_final_stop_error("end_turn") is None
    assert harness_final_stop_error("tool_result") is None
    assert harness_final_stop_error(None) is None
    assert parse_harness_event({"contentBlockStop": {"contentBlockIndex": 0}}) is None


# --- fills_in_tool_name ------------------------------------------------------


def test_a_later_event_naming_the_tool_fills_the_indicator_in():
    active = ToolUse(name=None, tool_use_id="t-1")
    event = ToolUse(name="get_weather", tool_use_id="t-1")

    assert fills_in_tool_name(active=active, event=event)


def test_an_indicator_that_already_has_a_name_keeps_it():
    active = ToolUse(name="get_weather", tool_use_id="t-1")
    event = ToolUse(name="current_time", tool_use_id="t-1")

    assert not fills_in_tool_name(active=active, event=event)


def test_a_repeat_carrying_no_name_leaves_the_indicator_alone():
    active = ToolUse(name=None, tool_use_id="t-1")

    assert not fills_in_tool_name(
        active=active, event=ToolUse(name=None, tool_use_id="t-1")
    )
    assert not fills_in_tool_name(
        active=active, event=ToolUse(name="", tool_use_id="t-1")
    )


def test_two_tools_that_never_carried_an_id_are_not_the_same_invocation():
    # `toolUseId` is optional, so None == None would otherwise hand the
    # second tool's name to the first one's indicator.
    active = ToolUse(name=None, tool_use_id=None)
    event = ToolUse(name="get_weather", tool_use_id=None)

    assert not fills_in_tool_name(active=active, event=event)


def test_another_invocation_does_not_fill_this_one_in():
    active = ToolUse(name=None, tool_use_id="t-1")
    event = ToolUse(name="get_weather", tool_use_id="t-2")

    assert not fills_in_tool_name(active=active, event=event)
