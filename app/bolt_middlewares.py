"""Middleware functions for the Slack Bolt app."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence

from slack_bolt import BoltResponse
from slack_bolt.request import BoltRequest
from slack_bolt.request.async_request import AsyncBoltRequest

from app.bolt_logic import is_retried_request, should_skip_event

logger = logging.getLogger(__name__)


def _skip_response(
    headers: Mapping[str, Sequence[str]], body: dict, payload: dict
) -> BoltResponse | None:
    """
    Judge whether a delivery is one to answer without running the listeners.

    Shared by the two middlewares below, which differ in what makes Slack
    redeliver an event, not in what Welt does about it. Keeping the judgment
    in one place is what keeps a condition added later from reaching only
    the entry point its author happened to be working on.

    Args:
        headers (Mapping[str, Sequence[str]]): The request headers.
        body (dict): The request body.
        payload (dict): The request payload.

    Returns:
        BoltResponse | None: The empty 200 to answer the delivery with, or
            None to let it through to the listeners.
    """
    if is_retried_request(headers):
        logger.debug(
            "Skipped the following middleware and listeners for this retried delivery"
        )
        return BoltResponse(status=200, body="")
    if should_skip_event(body, payload):
        logger.debug(
            "Skipped the following middleware and listeners "
            f"for this message event (subtype: {payload.get('subtype')})"
        )
        return BoltResponse(status=200, body="")
    return None


async def before_authorize(
    request: AsyncBoltRequest,
    body: dict,
    payload: dict,
    next_: Callable[[], Awaitable[None]],
) -> BoltResponse | None:
    """
    Skip retried deliveries and message changed/deleted events.

    Slack redelivers an event whose ack it never saw — over Socket Mode a
    connection swap can eat the ack after the first delivery already handed
    the work off, so processing the retry would just produce a duplicate
    reply. Message changed/deleted events are skipped to reduce unnecessary
    workload; especially, "message_changed" events can be triggered many
    times when the app rapidly updates its streaming reply.

    Args:
        request (AsyncBoltRequest): The incoming request.
        body (dict): The request body.
        payload (dict): The request payload.
        next_ (Callable[[], Awaitable[None]]): The next middleware to call.

    Returns:
        BoltResponse | None: A response if the event is skipped, else None.
    """
    skipped = _skip_response(request.headers, body, payload)
    if skipped is not None:
        return skipped
    await next_()
    return None


def before_authorize_http(
    request: BoltRequest,
    body: dict,
    payload: dict,
    next_: Callable[[], None],
) -> BoltResponse | None:
    """
    Skip retried deliveries and message changed/deleted events, over HTTP.

    The sync twin of `before_authorize` for the HTTP (Lambda) entry. Over
    HTTP, Slack retries a delivery whose ack misses the 3-second window,
    which a Lambda cold start can. The first delivery has already handed the
    real work to the lazy invocation by then, so a retry would just produce
    a duplicate reply.

    Args:
        request (BoltRequest): The incoming request.
        body (dict): The request body.
        payload (dict): The request payload.
        next_ (Callable[[], None]): The next middleware to call.

    Returns:
        BoltResponse | None: A response if the request is skipped, else None.
    """
    skipped = _skip_response(request.headers, body, payload)
    if skipped is not None:
        return skipped
    next_()
    return None
