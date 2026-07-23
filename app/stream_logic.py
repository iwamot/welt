"""Pure logic for parsing the agent's reply stream into render events.

An AgentCore Runtime agent yields Strands `stream_async` event dicts, which
the Runtime emits as SSE (`data: {json}\\n\\n`). A managed harness returns a
typed event stream (`contentBlockDelta` / `contentBlockStart` /
`runtimeClientError`) instead. Both dialects are parsed into the same small
render model — a text delta, a tool-use indicator, a generated file, or a
stream error — and everything else is ignored.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class TextDelta:
    """A chunk of assistant text to append to the streaming reply."""

    text: str


@dataclass(frozen=True)
class ToolUse:
    """A tool the agent is invoking, for a "using tool" indicator."""

    name: str | None
    tool_use_id: str | None


@dataclass(frozen=True)
class ToolResult:
    """A tool invocation that finished, for closing its indicator."""

    tool_use_id: str | None
    error: bool


@dataclass(frozen=True)
class FileOutput:
    """A file the agent generated, to upload to the thread."""

    name: str
    data: bytes


@dataclass(frozen=True)
class Interrupt:
    """A question the agent stopped on, to render as buttons in the thread."""

    id: str
    name: str
    reason: object


@dataclass(frozen=True)
class StreamError:
    """An error the AgentCore Runtime SDK reported mid-stream."""

    message: str


RenderEvent = TextDelta | ToolUse | ToolResult | FileOutput | Interrupt | StreamError


def parse_sse_data_line(line: str) -> dict | None:
    """
    Parse one SSE line into its JSON object.

    AgentCore Runtime emits each yielded event as a `data: {json}` line. Returns
    the decoded object for a `data:` line carrying a JSON object, or None for
    anything else (comments, blank lines, non-object or malformed JSON).

    Args:
        line (str): A single line from the SSE response stream.

    Returns:
        dict | None: The decoded event object, or None if not applicable.
    """
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload:
        return None
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def parse_stream_event(event: dict) -> RenderEvent | None:
    """
    Classify one Strands `stream_async` event into a render model.

    A `data` string is assistant text; a `current_tool_use` dict is a tool
    invocation (name for the indicator); a `tool_result` dict closes that
    indicator (the agent derives it from the Strands tool-result message,
    keeping only the toolUseId and status); a `file` dict is a generated file
    (the agent base64-encodes the raw bytes for the JSON wire — the inbound
    file encoding in reverse); an `interrupt` dict is a question the agent
    stopped on (id and name must be strings; the reason stays whatever JSON
    value the agent sent, since interpreting it is the rendering layer's
    job); an `error` string is the AgentCore Runtime SDK reporting that the
    agent raised mid-stream. Reasoning, citations, lifecycle, and the final
    result carry no key we render, so they map to None.

    Args:
        event (dict): One decoded Strands stream event.

    Returns:
        RenderEvent | None: A text delta, a tool use, a tool result, a
            generated file, an error, or None.
    """
    data = event.get("data")
    if isinstance(data, str) and data:
        return TextDelta(text=data)
    tool_use = event.get("current_tool_use")
    if isinstance(tool_use, dict):
        name = tool_use.get("name")
        tool_use_id = tool_use.get("toolUseId")
        return ToolUse(
            name=name if isinstance(name, str) else None,
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
        )
    tool_result = event.get("tool_result")
    if isinstance(tool_result, dict):
        tool_use_id = tool_result.get("toolUseId")
        return ToolResult(
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
            error=tool_result.get("status") == "error",
        )
    file = event.get("file")
    if isinstance(file, dict):
        return _parse_file_output(file)
    interrupt = event.get("interrupt")
    if isinstance(interrupt, dict):
        return _parse_interrupt(interrupt)
    error = event.get("error")
    if isinstance(error, str):
        return StreamError(message=error)
    return None


def _parse_interrupt(interrupt: dict) -> Interrupt | None:
    """
    Validate an `interrupt` event's envelope into a render event.

    Args:
        interrupt (dict): The `interrupt` value of a stream event.

    Returns:
        Interrupt | None: The interrupt, or None when the id or name is not
            a string (the reason is kept as-is — any JSON value is legal,
            and the rendering layer decides how to show it).
    """
    interrupt_id = interrupt.get("id")
    name = interrupt.get("name")
    if not isinstance(interrupt_id, str) or not interrupt_id:
        return None
    if not isinstance(name, str):
        return None
    return Interrupt(id=interrupt_id, name=name, reason=interrupt.get("reason"))


def _parse_file_output(file: dict) -> FileOutput | None:
    """
    Decode a `file` event's base64 bytes into an upload-ready render event.

    Args:
        file (dict): The `file` value of a stream event.

    Returns:
        FileOutput | None: The named file content, or None when the name or
            the base64 payload is missing or malformed.
    """
    name = file.get("name")
    data = file.get("bytes")
    if not isinstance(name, str) or not name or not isinstance(data, str):
        return None
    try:
        decoded = base64.b64decode(data, validate=True)
    except binascii.Error:
        return None
    return FileOutput(name=name, data=decoded)


def parse_harness_event(event: dict) -> RenderEvent | None:
    """
    Classify one `invoke_harness` stream event into a render model.

    A `contentBlockDelta` carrying a text delta is assistant text; a
    `contentBlockStart` opening a toolUse block is a tool invocation (name and
    ID for the indicator); a `contentBlockStart` opening a toolResult block is
    that tool finishing (the harness runs its tools server-side and streams
    the result back, so the block's status closes the indicator); a
    `runtimeClientError` is the harness reporting a failure mid-stream. The
    other event types (message / content-block lifecycle, reasoning and
    toolUse-input deltas, metadata) carry nothing we render, so they map to
    None — including `messageStop`, which recurs mid-stream as the agent
    loop iterates; only the stream-final stop reason is meaningful, and
    judging it is `harness_final_stop_error`'s job.

    Args:
        event (dict): One event from the InvokeHarness response stream.

    Returns:
        RenderEvent | None: A text delta, a tool use, a tool result, an
        error, or None.
    """
    block_delta = event.get("contentBlockDelta")
    if isinstance(block_delta, dict):
        delta = block_delta.get("delta")
        text = delta.get("text") if isinstance(delta, dict) else None
        if isinstance(text, str) and text:
            return TextDelta(text=text)
        return None
    block_start = event.get("contentBlockStart")
    if isinstance(block_start, dict):
        start = block_start.get("start")
        tool_use = start.get("toolUse") if isinstance(start, dict) else None
        if isinstance(tool_use, dict):
            name = tool_use.get("name")
            tool_use_id = tool_use.get("toolUseId")
            return ToolUse(
                name=name if isinstance(name, str) else None,
                tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
            )
        tool_result = start.get("toolResult") if isinstance(start, dict) else None
        if isinstance(tool_result, dict):
            tool_use_id = tool_result.get("toolUseId")
            return ToolResult(
                tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
                error=tool_result.get("status") == "error",
            )
        return None
    error = event.get("runtimeClientError")
    if isinstance(error, dict):
        message = error.get("message")
        return StreamError(
            message=message if isinstance(message, str) and message else "unknown error"
        )
    return None


def harness_final_stop_error(stop_reason: object) -> StreamError | None:
    """
    Judge the stop reason a harness stream ended on.

    A stream whose last `messageStop` carries `tool_use` ended because the
    harness expects the caller to run an inline function tool and send its
    result back. Welt runs no client-side tools, so the turn cannot proceed,
    and reporting that beats ending the reply silently. Mid-stream `tool_use`
    stops are normal (the agent loop pauses each iteration on them before
    running its own tools), which is why only the final one is judged.

    Args:
        stop_reason (object): The `stopReason` of the stream's last
            `messageStop` event, or None if none was seen.

    Returns:
        StreamError | None: The error to render, or None for a normal end.
    """
    if stop_reason == "tool_use":
        return StreamError(
            message="the harness called a client-side (inline function) "
            "tool, which Welt cannot run"
        )
    return None
