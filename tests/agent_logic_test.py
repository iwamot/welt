from __future__ import annotations

import re

from app.agent_logic import (
    build_runtime_session_id,
    build_runtime_user_id,
    is_harness_arn,
    parse_arn_region,
)


def test_thread_session_id_uses_team_channel_and_thread():
    result = build_runtime_session_id(
        team_id="T0123456789", channel_id="C0123456789", thread_ts="1712345678.123456"
    )

    assert result == "slack_T0123456789_C0123456789_1712345678-123456"


def test_dm_session_id_uses_thread_and_placeholder_team():
    result = build_runtime_session_id(
        team_id=None, channel_id="D0123456789", thread_ts="1712345678.123456"
    )

    assert result == "slack_-_D0123456789_1712345678-123456"


def test_session_id_shorter_than_api_minimum_is_padded():
    result = build_runtime_session_id(team_id=None, channel_id="D1", thread_ts="1.2")

    assert result == "slack_-_D1_1-2".ljust(33, "_")
    assert len(result) >= 33


def test_session_id_stays_inside_the_harness_character_set():
    result = build_runtime_session_id(
        team_id="T0123456789", channel_id="C0123456789", thread_ts="1712345678.123456"
    )

    assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-_]*", result)
    assert len(result) <= 100


def test_user_id_combines_team_and_user():
    assert (
        build_runtime_user_id(team_id="T0123456789", user_id="U0123456789")
        == "slack:T0123456789:U0123456789"
    )


def test_user_id_without_team_uses_placeholder():
    assert build_runtime_user_id(team_id=None, user_id="U1") == "slack:-:U1"


def test_harness_arn_is_detected():
    assert is_harness_arn(
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:harness/MyHarness-XyZ1234567"
    )


def test_runtime_arn_is_not_a_harness():
    assert not is_harness_arn(
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/my_agent-abcdefghij"
    )


def test_malformed_arn_is_not_a_harness():
    assert not is_harness_arn("harness/short")


def test_arn_region_is_extracted():
    result = parse_arn_region(
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/my_agent-abcdefghij"
    )

    assert result == "us-west-2"


def test_arn_with_empty_region_has_no_region():
    assert (
        parse_arn_region("arn:aws:bedrock-agentcore::123456789012:runtime/my_agent")
        is None
    )


def test_malformed_arn_has_no_region():
    assert parse_arn_region("runtime/short") is None
