"""Read Welt's Slack credentials from AWS Secrets Manager."""

from __future__ import annotations

import functools
import time
from collections.abc import Mapping

import boto3

from app.agent_logic import parse_arn_region
from app.secret_logic import CachedSecret, parse_slack_secret, reusable_secret

# How long a fetched secret is reused before it is read again. Five minutes is
# the default of the AWS Parameters and Secrets Lambda Extension, so a changed
# secret takes effect within five minutes.
SECRET_MAX_AGE_SECONDS = 300

# Kept at module level so the cache outlives an invocation and serves the warm
# ones that follow in the same execution environment.
_cache: dict[str, CachedSecret] = {}


def fetch_slack_secret(secret_arn: str) -> Mapping[str, str]:
    """
    Fetch a secret's JSON, reusing it for SECRET_MAX_AGE_SECONDS.

    Args:
        secret_arn (str): The ARN that SLACK_SECRET_ARN holds.

    Returns:
        Mapping[str, str]: The secret's JSON object.

    Raises:
        TypeError: If the secret holds JSON other than an object.
    """
    now = time.monotonic()
    secret = reusable_secret(
        _cache.get(secret_arn), now=now, max_age=SECRET_MAX_AGE_SECONDS
    )
    if secret is None:
        response = _client(secret_arn).get_secret_value(SecretId=secret_arn)
        secret = parse_slack_secret(response["SecretString"])
        _cache[secret_arn] = CachedSecret(fetched_at=now, secret=secret)
    return secret


@functools.cache
def _client(secret_arn: str):
    # The client calls the Region in the ARN, so the secret need not be in the
    # function's Region.
    return boto3.client("secretsmanager", region_name=parse_arn_region(secret_arn))
