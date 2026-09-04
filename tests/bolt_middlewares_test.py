from __future__ import annotations

import json

from slack_bolt import BoltResponse
from slack_bolt.request import BoltRequest
from slack_bolt.request.async_request import AsyncBoltRequest

from app.bolt_middlewares import before_authorize, before_authorize_http

NEW_EVENT = {"type": "message", "text": "hi"}
NEW_POST = {"type": "event_callback", "event": NEW_EVENT}
EDITED_EVENT = {"type": "message", "subtype": "message_changed"}
EDITED_POST = {"type": "event_callback", "event": EDITED_EVENT}


def _http_request(body: dict, headers: dict | None = None) -> BoltRequest:
    return BoltRequest(body=json.dumps(body), headers=headers)


def _socket_request(body: dict, headers: dict | None = None) -> AsyncBoltRequest:
    return AsyncBoltRequest(body=body, headers=headers, mode="socket_mode")


def _is_empty_ok(response: BoltResponse | None) -> bool:
    return response is not None and response.status == 200 and response.body == ""


# --- before_authorize_http ----------------------------------------------------


def test_http_passes_a_first_delivery_on():
    calls: list[str] = []

    result = before_authorize_http(
        _http_request(NEW_POST),
        NEW_POST,
        NEW_EVENT,
        lambda: calls.append("next"),
    )

    assert result is None
    assert calls == ["next"]


def test_http_answers_a_retried_delivery_without_the_listeners():
    calls: list[str] = []

    result = before_authorize_http(
        _http_request(NEW_POST, {"x-slack-retry-num": "1"}),
        NEW_POST,
        NEW_EVENT,
        lambda: calls.append("next"),
    )

    assert _is_empty_ok(result)
    assert calls == []


def test_http_answers_an_edited_message_without_the_listeners():
    calls: list[str] = []

    result = before_authorize_http(
        _http_request(EDITED_POST),
        EDITED_POST,
        EDITED_EVENT,
        lambda: calls.append("next"),
    )

    assert _is_empty_ok(result)
    assert calls == []


# --- before_authorize ---------------------------------------------------------


async def test_socket_mode_passes_a_first_delivery_on():
    calls: list[str] = []

    async def next_() -> None:
        calls.append("next")

    result = await before_authorize(
        _socket_request(NEW_POST, {"x-slack-retry-num": "0"}),
        NEW_POST,
        NEW_EVENT,
        next_,
    )

    assert result is None
    assert calls == ["next"]


async def test_socket_mode_answers_a_retried_delivery_without_the_listeners():
    calls: list[str] = []

    async def next_() -> None:
        calls.append("next")

    result = await before_authorize(
        _socket_request(NEW_POST, {"x-slack-retry-num": "1"}),
        NEW_POST,
        NEW_EVENT,
        next_,
    )

    assert _is_empty_ok(result)
    assert calls == []
