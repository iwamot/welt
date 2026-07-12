from __future__ import annotations

import pytest
from slack_bolt.authorization import AuthorizeResult
from slack_bolt.context.base_context import BaseContext

from app.bolt_logic import (
    INTERRUPT_ACTION_PATTERN,
    INTERRUPT_ACTION_PREFIX,
    determine_thread_ts_to_reply,
    extract_user_id_from_context,
    has_read_files_scope,
    is_post_from_bot,
    is_post_in_dm,
    is_post_mentioned,
    is_retried_request,
    keep_newest_replies,
    should_skip_event,
)


@pytest.mark.parametrize(
    "action_id, expected",
    [
        (INTERRUPT_ACTION_PREFIX + "0", True),
        (INTERRUPT_ACTION_PREFIX, True),
        ("other_action", False),
        ("x_" + INTERRUPT_ACTION_PREFIX + "0", False),
        ("", False),
    ],
)
def test_interrupt_action_pattern(action_id: str, expected: bool):
    assert (INTERRUPT_ACTION_PATTERN.search(action_id) is not None) is expected


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"x-slack-retry-num": ["1"]}, True),
        ({"x-slack-retry-num": ["2"], "x-slack-retry-reason": ["http_timeout"]}, True),
        ({}, False),
        (
            {"x-slack-signature": ["v0=abc"], "content-type": ["application/json"]},
            False,
        ),
    ],
)
def test_is_retried_request(headers: dict[str, list[str]], expected: bool):
    assert is_retried_request(headers) is expected


@pytest.mark.parametrize(
    "body, payload, expected",
    [
        (
            {"type": "event_callback", "event": {"type": "message"}},
            {"type": "message", "subtype": "message_changed"},
            True,
        ),
        (
            {"type": "event_callback", "event": {"type": "message"}},
            {"type": "message", "subtype": "message_deleted"},
            True,
        ),
        (
            {"type": "event_callback", "event": {"type": "message"}},
            {"type": "message", "subtype": "message_replied"},
            False,
        ),
        (
            {"type": "event_callback", "event": {"type": "reaction_added"}},
            {"type": "reaction_added", "subtype": "message_changed"},
            False,
        ),
        (
            {"type": "event_callback", "event": {}},
            {"type": "message", "subtype": "message_changed"},
            False,
        ),
        (
            {"type": "event_callback"},
            {"type": "message", "subtype": "message_changed"},
            False,
        ),
        (
            {"type": "block_actions", "event": {"type": "message"}},
            {"type": "message", "subtype": "message_changed"},
            False,
        ),
        (
            None,
            {"type": "message", "subtype": "message_changed"},
            False,
        ),
    ],
)
def test_should_skip_event(body, payload, expected):
    result = should_skip_event(body, payload)

    assert result == expected


@pytest.mark.parametrize(
    "actor_user_id, user_id, expected",
    [
        ("U_ACTOR", "U_USER", "U_ACTOR"),
        (None, "U_USER", "U_USER"),
        (None, None, None),
    ],
)
def test_extract_user_id_from_context(actor_user_id, user_id, expected):
    context = BaseContext(actor_user_id=actor_user_id, user_id=user_id)

    result = extract_user_id_from_context(context)

    assert result == expected


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"bot_id": "B123456"}, True),
        ({"bot_id": ""}, True),
        ({"bot_id": None}, False),
        ({}, False),
    ],
)
def test_is_post_from_bot(payload, expected):
    result = is_post_from_bot(payload)

    assert result == expected


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"channel_type": "im"}, True),
        ({"channel_type": "channel"}, False),
        ({"channel_type": None}, False),
        ({}, False),
    ],
)
def test_is_post_in_dm(payload, expected):
    result = is_post_in_dm(payload)

    assert result == expected


@pytest.mark.parametrize(
    "bot_user_id, post, expected",
    [
        ("U12345", {"text": "Hello <@U12345>"}, True),
        ("U12345", {"text": "No mention here"}, False),
        ("U12345", {"text": ""}, False),
        ("U12345", {}, False),
        ("U12345", None, False),
        (None, {"text": "Hello <@U12345>"}, False),
    ],
)
def test_is_post_mentioned(bot_user_id, post, expected):
    result = is_post_mentioned(bot_user_id, post)

    assert result == expected


@pytest.mark.parametrize(
    "payload, expected",
    [
        (
            {"thread_ts": "123.456", "ts": "999.999", "channel_type": "channel"},
            "123.456",
        ),
        ({"ts": "789.789", "channel_type": "channel"}, "789.789"),
        (
            {"thread_ts": "123.456", "ts": "999.999", "channel_type": "im"},
            "123.456",
        ),
        # A DM post outside a thread starts a new conversation under its own
        # timestamp (one thread = one conversation, in channels and DMs alike).
        ({"ts": "789.789", "channel_type": "im"}, "789.789"),
    ],
)
def test_determine_thread_ts_to_reply(payload, expected):
    result = determine_thread_ts_to_reply(payload)

    assert result == expected


@pytest.mark.parametrize(
    "replies, max_count, expected",
    [
        # Shorter than the limit: everything is kept.
        ([{"ts": "1"}, {"ts": "2"}], 3, [{"ts": "1"}, {"ts": "2"}]),
        # Exactly at the limit: everything is kept.
        ([{"ts": "1"}, {"ts": "2"}], 2, [{"ts": "1"}, {"ts": "2"}]),
        # Over the limit: the oldest replies are dropped.
        ([{"ts": "1"}, {"ts": "2"}, {"ts": "3"}], 2, [{"ts": "2"}, {"ts": "3"}]),
        ([], 2, []),
        ([{"ts": "1"}], 0, []),
    ],
)
def test_keep_newest_replies(replies, max_count, expected):
    result = keep_newest_replies(replies, max_count=max_count)

    assert result == expected


def test_keep_newest_replies_returns_a_copy():
    replies = [{"ts": "1"}]

    result = keep_newest_replies(replies, max_count=2)

    assert result == replies
    assert result is not replies


@pytest.mark.parametrize(
    "bot_scopes, expected",
    [
        (None, False),
        (["chat:write", "users:read"], False),
        (["files:read", "chat:write"], True),
    ],
)
def test_has_read_files_scope(bot_scopes, expected):
    authorize_result = (
        None
        if bot_scopes is None
        else AuthorizeResult(
            bot_scopes=bot_scopes,
            enterprise_id="dummy_eid",
            team_id="dummy_tid",
        )
    )

    result = has_read_files_scope(authorize_result)

    assert result == expected
