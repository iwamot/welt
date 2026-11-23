from __future__ import annotations

import re
from itertools import product

import pytest

from app.agent_logic import (
    build_qualifier_kwargs,
    build_runtime_session_id,
    build_runtime_user_id,
    is_harness_arn,
    parse_arn_region,
    parse_gateway_region,
    split_endpoint_arn,
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


def test_user_id_combines_team_and_user():
    assert (
        build_runtime_user_id(team_id="T0123456789", user_id="U0123456789")
        == "slack:T0123456789:U0123456789"
    )


def test_user_id_without_team_uses_placeholder():
    assert build_runtime_user_id(team_id=None, user_id="U1") == "slack:-:U1"


def test_qualifier_becomes_an_invoke_argument():
    assert build_qualifier_kwargs("prod") == {"qualifier": "prod"}


def test_no_qualifier_leaves_the_invoke_argument_out():
    assert build_qualifier_kwargs(None) == {}


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


_HARNESS_ARN = (
    "arn:aws:bedrock-agentcore:us-west-2:123456789012:harness/MyHarness-XyZ1234567"
)
_RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/my_agent-abcdefghij"
)


def test_an_endpoint_arn_splits_into_the_resource_and_the_endpoint():
    resources = (_HARNESS_ARN, _RUNTIME_ARN, "harness/short", "")
    separators = ("/harness-endpoint/", "/runtime-endpoint/")
    for resource, separator, endpoint in product(
        resources, separators, ("DEFAULT", "prod_1", "")
    ):
        arn = f"{resource}{separator}{endpoint}"
        if endpoint:
            assert split_endpoint_arn(arn) == (resource, endpoint)
        else:
            # Naming no endpoint is not naming an empty one.
            assert split_endpoint_arn(arn) == (arn, None)
        # The endpoint does not change which invoke API the resource takes.
        assert is_harness_arn(arn) == is_harness_arn(resource)
    for resource in resources:
        assert split_endpoint_arn(resource) == (resource, None)


def test_session_ids_fit_both_invoke_apis_and_keep_threads_apart():
    keys = list(
        product(
            (None, "T0123456789", "T1", "E0ABCDEFGHIJ"),
            ("C0123456789", "D0123456789", "D1", "G1"),
            ("1712345678.123456", "1.2", "1712345678.000100", "0.0"),
        )
    )
    results = set()
    for team_id, channel_id, thread_ts in keys:
        result = build_runtime_session_id(
            team_id=team_id, channel_id=channel_id, thread_ts=thread_ts
        )
        assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-_]*", result), result
        assert 33 <= len(result) <= 100, result
        results.add(result)
    # One thread is one conversation, and no two threads share one.
    assert len(results) == len(keys)


@pytest.mark.parametrize(
    ("url", "region"),
    [
        (
            "https://my-gateway.gateway.bedrock-agentcore.ap-northeast-1.amazonaws.com/agent",
            "ap-northeast-1",
        ),
        (
            "https://my-gateway.gateway.bedrock-agentcore.cn-north-1.amazonaws.com.cn/agent/",
            "cn-north-1",
        ),
        (
            "http://my-gateway.gateway.bedrock-agentcore.us-west-2.amazonaws.com/agent",
            None,
        ),
        ("https://example.com/agent", None),
        ("https:///agent", None),
        (
            "https://my-gateway.gateway.bedrock-agentcore.us-west-2.amazonaws.com.example.com/agent",
            None,
        ),
    ],
)
def test_gateway_signing_region_comes_from_its_aws_endpoint(url, region):
    assert parse_gateway_region(url) == region
