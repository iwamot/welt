"""Read Welt's Slack credentials from AWS Secrets Manager."""

from __future__ import annotations

import functools
from collections.abc import Mapping

import boto3
from aws_lambda_powertools.utilities.parameters import SecretsProvider

from app.agent_logic import parse_arn_region

# How long a fetched secret is reused before it is read again. Five minutes is
# the default of the AWS Parameters and Secrets Lambda Extension, so a changed
# secret takes effect within five minutes.
SECRET_MAX_AGE_SECONDS = 300


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
    secret = _provider(secret_arn).get(
        secret_arn, max_age=SECRET_MAX_AGE_SECONDS, transform="json"
    )
    if not isinstance(secret, dict):
        raise TypeError("SLACK_SECRET_ARN must name a secret holding a JSON object")
    return secret


@functools.cache
def _provider(secret_arn: str) -> SecretsProvider:
    # The client calls the Region in the ARN, so the secret need not be in the
    # function's Region. One provider per ARN keeps its cache across invocations.
    return SecretsProvider(
        boto3_client=boto3.client(
            "secretsmanager", region_name=parse_arn_region(secret_arn)
        )
    )
