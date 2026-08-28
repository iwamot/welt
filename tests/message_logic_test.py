from __future__ import annotations

import pytest

from app.message_logic import build_slack_user_prefixed_text, build_tool_use_task_chunk


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
