"""Adapters for the two directions of Welt's wire contract — copy as-is.

The wire between Welt and the agent is JSON, and plain Strands values do not
fit it in either direction:

- Inbound, JSON cannot carry raw bytes, so Welt base64-encodes the `bytes`
  slot of the Converse image/document/video blocks it builds from Slack
  uploads. `decode_file_blocks` restores them before Strands (Bedrock
  Converse) sees the messages; without uploads it is a no-op.
- Outbound, raw `stream_async` events carry values that are not
  JSON-serializable (the Agent itself, UUIDs, traces), which the AgentCore
  Runtime SDK would degrade to a plain string on the SSE wire.
  `renderable_events` reduces the stream to the events Welt renders.
"""

import base64
from collections.abc import AsyncIterator


def decode_file_blocks(messages: list) -> None:
    """
    Decode base64 image/document/video bytes back to raw bytes, in place.

    Args:
        messages (list): The Converse-shaped messages from Welt's payload.

    Returns:
        None
    """
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            for key in ("image", "document", "video"):
                media = block.get(key)
                if not isinstance(media, dict):
                    continue
                source = media.get("source")
                if isinstance(source, dict) and isinstance(source.get("bytes"), str):
                    source["bytes"] = base64.b64decode(source["bytes"])


async def renderable_events(events: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """
    Reduce Strands `stream_async` events to the subset Welt renders.

    Args:
        events (AsyncIterator[dict]): Raw `stream_async` events.

    Yields:
        dict: A `data` event per text chunk, a `current_tool_use` event per
            tool-use update, and a `tool_result` event — slimmed to the
            toolUseId and status, so tool output (arbitrarily large, possibly
            raw bytes) stays off the wire — per completed tool.
    """
    async for event in events:
        if "data" in event:
            yield {"data": event["data"]}
        elif "current_tool_use" in event:
            yield {"current_tool_use": event["current_tool_use"]}
        elif "message" in event:
            for tool_result in _tool_results(event["message"]):
                yield {"tool_result": tool_result}


def _tool_results(message: object) -> list[dict]:
    """
    Extract slimmed toolResult entries from a Strands message event.

    Strands adds tool results to the conversation as a message whose content
    blocks each carry a `toolResult`; model messages carry none, so they
    yield an empty list.

    Args:
        message (object): The `message` value of a stream event.

    Returns:
        list[dict]: One `{"toolUseId", "status"}` entry per tool result.
    """
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    results: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        tool_result = block.get("toolResult")
        if not isinstance(tool_result, dict):
            continue
        results.append(
            {
                "toolUseId": tool_result.get("toolUseId"),
                "status": tool_result.get("status"),
            }
        )
    return results
