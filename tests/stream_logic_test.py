from __future__ import annotations

from app.stream_logic import (
    StreamError,
    TextDelta,
    ToolResult,
    ToolUse,
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


# --- parse_stream_event (real Strands stream_async event shapes) ------------


def test_text_stream_event_is_text_delta():
    event = {"data": "Hello", "delta": {"text": "Hello"}}

    assert parse_stream_event(event) == TextDelta(text="Hello")


def test_empty_text_is_ignored():
    assert parse_stream_event({"data": "", "delta": {"text": ""}}) is None


def test_tool_use_stream_event_is_tool_use():
    event = {
        "type": "tool_use_stream",
        "delta": {"toolUse": {"input": '{"city":'}},
        "current_tool_use": {
            "toolUseId": "tooluse_abc",
            "name": "get_weather",
            "input": "",
        },
    }

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


def test_reasoning_event_is_ignored():
    event = {"reasoningText": "thinking...", "delta": {}, "reasoning": True}

    assert parse_stream_event(event) is None


def test_lifecycle_and_result_events_are_ignored():
    assert parse_stream_event({"init_event_loop": True}) is None
    assert parse_stream_event({"result": {"stop_reason": "end_turn"}}) is None


def test_error_event_maps_to_stream_error():
    event = {
        "error": "division by zero",
        "error_type": "ZeroDivisionError",
        "message": "An error occurred during streaming",
    }

    assert parse_stream_event(event) == StreamError(message="division by zero")


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
    assert parse_harness_event({"contentBlockStop": {"contentBlockIndex": 0}}) is None
