"""Pure helpers for what Welt puts around a Slack message.

The speaker prefix on the way in, the streaming chunks on the way out.
Reading what a message shows is
`slack_markdown_logic`; outbound Markdown needs no conversion at all —
the chat streaming API renders `markdown_text` server-side.
"""

from __future__ import annotations

import re

from app.stream_logic import ToolUse

# What Slack calls a person or an app. Anything else standing where one
# does is a name already — an incoming webhook posts under one.
_SLACK_ID = re.compile(r"[UW][A-Z0-9]{2,}")


def build_slack_user_prefixed_text(reply: dict, text: str) -> str:
    """
    Build a Slack user-prefixed text message.

    Args:
        reply (dict): The reply dictionary containing user information.
        text (str): The text message to be prefixed.

    Returns:
        str: The text behind whoever sent it — an ID as the mention that
            `converse_logic` goes on to name, and anything else as the
            name it already is.
    """
    speaker = reply.get("user", reply.get("username"))
    if isinstance(speaker, str) and _SLACK_ID.fullmatch(speaker):
        return f"<@{speaker}>: {text}"
    return f"@{speaker}: {text}"


# Converse task_update chunks cap title/details at 256 characters.
TASK_CHUNK_TITLE_MAX_LENGTH = 256


def build_tool_use_task_chunk(
    *, tool_use_id: str | None, tool_name: str | None, status: str
) -> dict:
    """
    Build a task_update chunk showing a tool invocation in the reply timeline.

    Args:
        tool_use_id (str | None): The event's toolUseId, used as the task ID
            so a later status change updates the same task.
        tool_name (str | None): The tool name, if the event carried one.
        status (str): The task status (in_progress / complete / error).

    Returns:
        dict: A chat.appendStream / chat.stopStream `chunks` entry.
    """
    title = f"Using {tool_name}" if tool_name else "Using a tool"
    return {
        "type": "task_update",
        "id": tool_use_id or "tool",
        "title": title[:TASK_CHUNK_TITLE_MAX_LENGTH],
        "status": status,
    }


def tool_chunks(
    *,
    completed: ToolUse | None = None,
    started: ToolUse | None = None,
    error: bool = False,
) -> list[dict]:
    """
    Build the task_update chunks for a change in which tool is running.

    Args:
        completed (ToolUse | None): The tool that just finished, if one did.
        started (ToolUse | None): The tool that just started, if one did.
        error (bool): Whether the completed tool failed.

    Returns:
        list[dict]: The chunks, the completed tool's first: at most one to
            close its task and one to open the next.
    """
    chunks: list[dict] = []
    if completed is not None:
        chunks.append(
            build_tool_use_task_chunk(
                tool_use_id=completed.tool_use_id,
                tool_name=completed.name,
                status="error" if error else "complete",
            )
        )
    if started is not None:
        chunks.append(
            build_tool_use_task_chunk(
                tool_use_id=started.tool_use_id,
                tool_name=started.name,
                status="in_progress",
            )
        )
    return chunks
