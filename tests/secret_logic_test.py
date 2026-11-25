from __future__ import annotations

import copy
import json
from itertools import product

import pytest

from app.secret_logic import CachedSecret, parse_slack_secret, reusable_secret

SECRETS = [
    {},
    {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_SIGNING_SECRET": "s"},
    {"SLACK_BOT_TOKEN": "", "extra": "日本語", "__proto__": "x"},
]
# Halves and whole numbers add exactly, so `fetched_at + age` loses nothing.
FETCHED_ATS = [0.0, 1000.0, 1e6]
AGES = [0.0, 0.5, 1.0, 299.5, 300.0, 300.5, 1e6]
MAX_AGES = [0.5, 1.0, 300.0]

# JSON values of every type, with objects nested and adversarial ones.
JSON_VALUES = [
    *SECRETS,
    {"a": {"b": ["c"]}},
    [],
    ["a"],
    [{"SLACK_BOT_TOKEN": "xoxb-1"}],
    "xoxb-1",
    "",
    "{}",
    0,
    1,
    -1.5,
    True,
    False,
    None,
]
NOT_JSON = ["", "xoxb-1", "{", "{'a': 'b'}", "[1,]", "SLACK_BOT_TOKEN=xoxb-1"]


@pytest.mark.parametrize(("now", "max_age"), list(product(FETCHED_ATS, MAX_AGES)))
def test_nothing_cached_must_be_read(now, max_age):
    assert reusable_secret(None, now=now, max_age=max_age) is None


@pytest.mark.parametrize(
    ("secret", "fetched_at", "age", "max_age"),
    list(product(SECRETS, FETCHED_ATS, AGES, MAX_AGES)),
)
def test_cached_secret_is_reused_only_while_younger_than_max_age(
    secret, fetched_at, age, max_age
):
    cached = CachedSecret(fetched_at=fetched_at, secret=secret)
    before = copy.deepcopy(cached)
    now = fetched_at + age

    result = reusable_secret(cached, now=now, max_age=max_age)

    if age < max_age:
        assert result is secret
    else:
        assert result is None
    assert cached == before


@pytest.mark.parametrize(
    ("value", "spacing"), list(product(JSON_VALUES, [None, 2, "\n\t "]))
)
def test_only_a_json_object_parses(value, spacing):
    text = json.dumps(value, indent=spacing, ensure_ascii=False)

    if isinstance(value, dict):
        assert parse_slack_secret(text) == value
    else:
        with pytest.raises(TypeError, match="JSON object"):
            parse_slack_secret(text)


@pytest.mark.parametrize("text", NOT_JSON)
def test_non_json_is_rejected(text):
    with pytest.raises(json.JSONDecodeError):
        parse_slack_secret(text)
