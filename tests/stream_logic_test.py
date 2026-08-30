from __future__ import annotations

import base64
import logging

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

# --- parse_sse_data_line -----------------------------------------------------


def test_sse_line_decodes_data_object():
    assert parse_sse_data_line('data: {"data": "hi"}') == {"data": "hi"}


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


def test_text_stream_event_is_text_delta():
    assert parse_stream_event({"data": "Hello"}) == TextDelta(text="Hello")


def test_empty_text_is_ignored():
    assert parse_stream_event({"data": ""}) is None


def test_tool_use_stream_event_is_tool_use():
    event = {"current_tool_use": {"toolUseId": "tooluse_abc", "name": "get_weather"}}

    assert parse_stream_event(event) == ToolUse(
        name="get_weather", tool_use_id="tooluse_abc"
    )


def test_tool_use_with_missing_fields():
    assert parse_stream_event({"current_tool_use": {}}) == ToolUse(
        name=None, tool_use_id=None
    )


def test_tool_result_success_is_tool_result():
    event = {"tool_result": {"toolUseId": "tooluse_abc", "status": "success"}}

    assert parse_stream_event(event) == ToolResult(
        tool_use_id="tooluse_abc", error=False
    )


def test_tool_result_error_is_flagged():
    event = {"tool_result": {"toolUseId": "tooluse_abc", "status": "error"}}

    assert parse_stream_event(event) == ToolResult(
        tool_use_id="tooluse_abc", error=True
    )


def test_tool_result_with_missing_fields():
    assert parse_stream_event({"tool_result": {}}) == ToolResult(
        tool_use_id=None, error=False
    )


def test_file_event_is_file_output():
    data = b"\x89PNG\r\n\x1a\n"
    event = {
        "file": {
            "name": "image.png",
            "bytes": base64.b64encode(data).decode("ascii"),
        }
    }

    assert parse_stream_event(event) == FileOutput(name="image.png", data=data)


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


def test_error_event_maps_to_stream_error():
    event = {
        "error": "division by zero",
        "error_type": "ZeroDivisionError",
        "message": "An error occurred during streaming",
    }

    assert parse_stream_event(event) == StreamError(message="division by zero")


def test_empty_error_gets_the_same_fallback_as_the_harness_dialect():
    assert parse_stream_event({"error": ""}) == StreamError(message="unknown error")


# --- parse_harness_event -----------------------------------------------------


def test_harness_text_delta_is_text_delta():
    event = {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hi"}}}

    assert parse_harness_event(event) == TextDelta(text="Hi")


def test_harness_empty_text_delta_is_ignored():
    event = {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": ""}}}

    assert parse_harness_event(event) is None


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


def test_harness_tool_use_start_is_tool_use():
    event = {
        "contentBlockStart": {
            "contentBlockIndex": 1,
            "start": {"toolUse": {"toolUseId": "tool-1", "name": "web_search"}},
        }
    }

    assert parse_harness_event(event) == ToolUse(
        name="web_search", tool_use_id="tool-1"
    )


def test_harness_tool_use_with_missing_fields():
    event = {"contentBlockStart": {"start": {"toolUse": {}}}}

    assert parse_harness_event(event) == ToolUse(name=None, tool_use_id=None)


def test_harness_tool_result_start_is_tool_result():
    event = {
        "contentBlockStart": {
            "contentBlockIndex": 2,
            "start": {"toolResult": {"toolUseId": "tool-1", "status": "success"}},
        }
    }

    assert parse_harness_event(event) == ToolResult(tool_use_id="tool-1", error=False)


def test_harness_tool_result_error_status_is_error():
    event = {
        "contentBlockStart": {
            "start": {"toolResult": {"toolUseId": "tool-1", "status": "error"}},
        }
    }

    assert parse_harness_event(event) == ToolResult(tool_use_id="tool-1", error=True)


def test_harness_tool_result_with_missing_fields():
    event = {"contentBlockStart": {"start": {"toolResult": {}}}}

    assert parse_harness_event(event) == ToolResult(tool_use_id=None, error=False)


def test_harness_non_tool_block_start_is_ignored():
    assert parse_harness_event({"contentBlockStart": {"start": {}}}) is None
    assert parse_harness_event({"contentBlockStart": {}}) is None


def test_harness_runtime_client_error_maps_to_stream_error():
    event = {"runtimeClientError": {"message": "the agent crashed"}}

    assert parse_harness_event(event) == StreamError(message="the agent crashed")


def test_harness_error_without_message_gets_a_fallback():
    assert parse_harness_event({"runtimeClientError": {}}) == StreamError(
        message="unknown error"
    )


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
