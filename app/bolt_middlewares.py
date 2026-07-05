"""Middleware functions for the Slack Bolt app."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from slack_bolt import BoltResponse
from slack_bolt.request import BoltRequest

from app.bolt_logic import is_retried_request, should_skip_event

logger = logging.getLogger(__name__)


async def before_authorize(
    body: dict,
    payload: dict,
    next_: Callable[[], Awaitable[None]],
) -> BoltResponse | None:
    """
    Skip message changed/deleted events to reduce unnecessary workload.

    Especially, "message_changed" events can be triggered many times when the
    app rapidly updates its streaming reply.

    Args:
        body (dict): The request body.
        payload (dict): The request payload.
        next_ (Callable[[], Awaitable[None]]): The next middleware to call.

    Returns:
        BoltResponse | None: A response if the event is skipped, else None.
    """
    if should_skip_event(body, payload):
        logger.debug(
            "Skipped the following middleware and listeners "
            f"for this message event (subtype: {payload.get('subtype')})"
        )
        return BoltResponse(status=200, body="")
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

    The sync twin of `before_authorize` for the HTTP (Lambda) entry, with one
    HTTP-only addition: Slack retries a delivery whose ack misses the
    3-second window, which a Lambda cold start can. The first delivery has
    already handed the real work to the lazy invocation by then, so a retry
    would just produce a duplicate reply.

    Args:
        request (BoltRequest): The incoming request.
        body (dict): The request body.
        payload (dict): The request payload.
        next_ (Callable[[], None]): The next middleware to call.

    Returns:
        BoltResponse | None: A response if the request is skipped, else None.
    """
    if is_retried_request(request.headers):
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
    next_()
    return None
