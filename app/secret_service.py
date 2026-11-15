"""Read Welt's Slack credentials from AWS Secrets Manager."""

from __future__ import annotations

import boto3

from app.agent_logic import parse_arn_region


def fetch_secret_string(secret_arn: str) -> str:
    """
    Fetch the SecretString of a Secrets Manager secret.

    The client calls the Region in the ARN, so the secret need not be in the
    function's Region.

    Args:
        secret_arn (str): The ARN that SLACK_SECRET_ARN holds.

    Returns:
        str: The secret's SecretString.
    """
    client = boto3.client("secretsmanager", region_name=parse_arn_region(secret_arn))
    return client.get_secret_value(SecretId=secret_arn)["SecretString"]
