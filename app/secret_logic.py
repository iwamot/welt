"""Pure logic for the Slack secret Welt reads from AWS Secrets Manager.

`secret_service` does the reading; this module decides whether a cached copy
may still be used and what shape the secret must have.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import NamedTuple


class CachedSecret(NamedTuple):
    """A parsed secret and the monotonic time it was fetched at."""

    fetched_at: float
    secret: Mapping[str, str]


def reusable_secret(
    cached: CachedSecret | None, *, now: float, max_age: float
) -> Mapping[str, str] | None:
    """
    Return the cached secret while it is younger than max_age.

    Args:
        cached (CachedSecret | None): The cached copy, if any.
        now (float): The current monotonic time.
        max_age (float): How many seconds a fetched secret is reused.

    Returns:
        Mapping[str, str] | None: The secret, or None if it must be read again.
    """
    if cached is None or now - cached.fetched_at >= max_age:
        return None
    return cached.secret


def parse_slack_secret(secret_string: str) -> Mapping[str, str]:
    """
    Parse the secret's JSON.

    Args:
        secret_string (str): The secret's SecretString.

    Returns:
        Mapping[str, str]: The secret's JSON object.

    Raises:
        json.JSONDecodeError: If the secret is not JSON.
        TypeError: If the secret holds JSON other than an object.
    """
    secret = json.loads(secret_string)
    if not isinstance(secret, dict):
        raise TypeError("SLACK_SECRET_ARN must name a secret holding a JSON object")
    return secret
