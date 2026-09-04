from __future__ import annotations

import pytest

from app.message_logic import (
    build_slack_user_prefixed_text,
    build_tool_use_task_chunk,
    tool_chunks,
)
from app.stream_logic import ToolUse


@pytest.mark.parametrize(
    "reply, text, expected",
    [
        ({"user": "U0123456"}, "hello", "<@U0123456>: hello"),
        # Whoever is not an ID is a name already: an incoming webhook posts
        # under one, and a message from nobody at all has none.
        ({"username": "some-webhook"}, "hi", "@some-webhook: hi"),
        ({}, "yo", "@None: yo"),
    ],
)
def test_build_slack_user_prefixed_text(reply, text, expected):
    result = build_slack_user_prefixed_text(reply, text)

    assert result == expected


# --- build_tool_use_task_chunk -----------------------------------------------


def test_task_chunk_with_name_and_id():
    result = build_tool_use_task_chunk(
        tool_use_id="tooluse_abc", tool_name="get_weather", status="in_progress"
    )

    assert result == {
        "type": "task_update",
        "id": "tooluse_abc",
        "title": "Using get_weather",
        "status": "in_progress",
    }


def test_task_chunk_without_name_or_id_uses_fallbacks():
    result = build_tool_use_task_chunk(
        tool_use_id=None, tool_name=None, status="complete"
    )

    assert result == {
        "type": "task_update",
        "id": "tool",
        "title": "Using a tool",
        "status": "complete",
    }


def test_task_chunk_title_is_truncated_to_chunk_limit():
    result = build_tool_use_task_chunk(
        tool_use_id="t", tool_name="x" * 300, status="in_progress"
    )

    assert len(result["title"]) == 256


# --- tool_chunks --------------------------------------------------------------


def test_tool_chunks_are_empty_when_nothing_changed():
    assert tool_chunks() == []


def test_tool_chunks_open_a_started_tool():
    assert tool_chunks(started=ToolUse(name="search", tool_use_id="t1")) == [
        {
            "type": "task_update",
            "id": "t1",
            "title": "Using search",
            "status": "in_progress",
        }
    ]


def test_tool_chunks_close_the_completed_tool_before_opening_the_next():
    chunks = tool_chunks(
        completed=ToolUse(name="search", tool_use_id="t1"),
        started=ToolUse(name="fetch", tool_use_id="t2"),
    )

    assert [(c["id"], c["status"]) for c in chunks] == [
        ("t1", "complete"),
        ("t2", "in_progress"),
    ]


def test_tool_chunks_mark_a_failed_tool_as_an_error():
    chunks = tool_chunks(completed=ToolUse(name="search", tool_use_id="t1"), error=True)

    assert [(c["id"], c["status"]) for c in chunks] == [("t1", "error")]
