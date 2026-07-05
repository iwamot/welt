"""Pure logic for filtering and routing incoming Slack posts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from slack_bolt.authorization.authorize_result import AuthorizeResult
from slack_bolt.context.base_context import BaseContext
from slack_bolt.request.payload_utils import is_event


def is_retried_request(headers: Mapping[str, Sequence[str]]) -> bool:
    """
    Check if the request is a Slack retry of an earlier event delivery.

    Slack retries an Events API delivery when the first response is late or
    fails; the retry carries an `x-slack-retry-num` header. Bolt normalizes
    header names to lowercase, so the check is exact.

    Args:
        headers (Mapping[str, Sequence[str]]): The normalized request headers
            (`BoltRequest.headers`).

    Returns:
        bool: True if the request is a retried delivery, False otherwise.
    """
    return "x-slack-retry-num" in headers


def should_skip_event(body: dict, payload: dict) -> bool:
    """
    Determine if the event should be skipped based on its type and subtype.

    Args:
        body (dict): The request body.
        payload (dict): The request payload.

    Returns:
        bool: True if the event should be skipped, False otherwise.
    """
    return (
        is_event(body)
        and payload.get("type") == "message"
        and payload.get("subtype") in ["message_changed", "message_deleted"]
    )


def extract_user_id_from_context(context: BaseContext) -> str | None:
    """
    Extract the user ID from a Bolt context object.

    Args:
        context (BaseContext): The Bolt context object.

    Returns:
        str | None: The user ID if available, None otherwise.
    """
    return context.actor_user_id or context.user_id


def is_post_from_bot(payload: dict) -> bool:
    """
    Check if the post is from a bot.

    Args:
        payload (dict): The Slack post payload.

    Returns:
        bool: True if the post is from a bot, False otherwise.
    """
    return payload.get("bot_id") is not None


def is_post_in_dm(payload: dict) -> bool:
    """
    Check if the post is in a direct message (DM) channel.

    Args:
        payload (dict): The Slack post payload.

    Returns:
        bool: True if the post is in a DM channel, False otherwise.
    """
    return payload.get("channel_type") == "im"


def is_post_mentioned(bot_user_id: str | None, post: dict | None) -> bool:
    """
    Checks whether the bot is mentioned in a Slack post.

    Args:
        bot_user_id (str | None): The bot's user ID.
        post (dict | None): The Slack post.

    Returns:
        bool: True if the bot is mentioned, False otherwise.
    """
    return post is not None and f"<@{bot_user_id}>" in post.get("text", "")


def determine_thread_ts_to_reply(payload: dict) -> str:
    """
    Determine the thread timestamp (thread_ts) to reply to.

    Welt always replies in a thread — one thread is one conversation (and one
    agent session), in channels and DMs alike. A post outside a thread starts
    a new conversation under its own timestamp.

    Args:
        payload (dict): The Slack post payload.

    Returns:
        str: The thread timestamp to reply to.
    """
    thread_ts = payload.get("thread_ts")
    return thread_ts if isinstance(thread_ts, str) else payload["ts"]


# A thread longer than this keeps only its newest replies as history.
MAX_THREAD_REPLIES = 1000


def keep_newest_replies(replies: list[dict], *, max_count: int) -> list[dict]:
    """
    Keep the newest replies of an overlong thread, dropping the oldest.

    The newest replies carry the post being answered, so an overlong thread
    loses its oldest context rather than its latest question.

    Args:
        replies (list[dict]): Slack replies in chronological order.
        max_count (int): The maximum number of replies to keep.

    Returns:
        list[dict]: The newest replies, at most max_count of them.
    """
    if max_count <= 0:
        return []
    if len(replies) <= max_count:
        return list(replies)
    return replies[-max_count:]


def has_read_files_scope(authorize_result: AuthorizeResult | None) -> bool:
    """
    Check if the bot has the "files:read" scope.

    Args:
        authorize_result (AuthorizeResult | None): The authorization result.

    Returns:
        bool: True if the bot has the "files:read" scope, False otherwise.
    """
    return (
        authorize_result is not None
        and authorize_result.bot_scopes is not None
        and "files:read" in authorize_result.bot_scopes
    )
