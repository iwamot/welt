"""Pure logic for the streaming reply: its undelivered tail, and its notes.

The SDK helper buffers markdown until its buffer size and only then calls
the API, so an append that raised — and the buffered appends before it —
never reached Slack. `PendingAppends` is that tail: appends accumulate
until a call reports having reached the API, and a rollover drains them to
replay into the fresh message with nothing lost. The stream I/O itself
lives in `slack_stream_service`.
"""

from __future__ import annotations

from dataclasses import dataclass

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_slack_response import AsyncSlackResponse


def is_message_too_long(error: SlackApiError) -> bool:
    """
    Tell whether a stream call failed because the message is full.

    Args:
        error (SlackApiError): The error the chat stream call raised.

    Returns:
        bool: True if Slack answered `msg_too_long`; False for any other
            failure, including one that never carried a response.
    """
    if not isinstance(error.response, AsyncSlackResponse):
        return False
    reason = error.response.get("error")
    return isinstance(reason, str) and reason == "msg_too_long"


def note_after_reply(text: str) -> str:
    """
    Set a note off from the reply it is appended to.

    Welt's own notes — a failure, a shutdown — are appended to whatever
    the reply had streamed so far, which is a sentence stopped wherever it
    was. Without a break the note reads as the next few words of it.

    Args:
        text (str): The note.

    Returns:
        str: The note, opening on a line of its own.
    """
    return f"\n\n{text}"


@dataclass(frozen=True)
class PendingAppend:
    """One append handed to the current stream but possibly not delivered."""

    markdown_text: str | None
    chunks: list[dict] | None


class PendingAppends:
    """The appends a streaming reply may still owe its reader.

    Holds them in the order they were made, which is the order a replay has
    to repeat them in for the continuation message to read as the reply
    continuing.
    """

    def __init__(self) -> None:
        self._items: list[PendingAppend] = []

    def record(
        self, *, markdown_text: str | None = None, chunks: list[dict] | None = None
    ) -> None:
        """
        Remember an append until a call confirms it reached Slack.

        Args:
            markdown_text (str | None): Markdown handed to the stream.
            chunks (list[dict] | None): Streaming chunks handed to the stream.

        Returns:
            None
        """
        self._items.append(PendingAppend(markdown_text=markdown_text, chunks=chunks))

    def clear(self) -> None:
        """
        Drop the tail, the appends in it having reached Slack.

        Returns:
            None
        """
        self._items.clear()

    def drain(self) -> list[PendingAppend]:
        """
        Take the tail for replay, leaving nothing owed to the old message.

        Returns:
            list[PendingAppend]: The undelivered appends, oldest first. The
                list is the caller's own, so recording into a replay's own
                tail as it goes does not disturb it.
        """
        items = self._items
        self._items = []
        return items


# How long a streamed message may grow before the reply moves on to the next
# one at its next paragraph break. Slack refuses a streamed message past about
# 12,000 characters (measured 2026-11-27, English and Japanese alike, so the
# limit counts characters rather than bytes). The margin is what a paragraph
# already under way when the mark is passed has left to finish in.
SOFT_MESSAGE_LENGTH = 10_000

_FENCE = "```"


def open_fence(text: str) -> str | None:
    """
    Tell whether text ends inside a fenced code block.

    Only whole lines count: a last line without its line break may still be
    on its way. A fence is a line of three or more backticks, indented by up
    to three spaces. A block closes on a line of only backticks, at least as
    many as it opened with.

    Args:
        text (str): Markdown streamed so far.

    Returns:
        str | None: The line that opened the block, as written after its
            indentation, or None outside one.
    """
    opener: str | None = None
    width = 0
    for line in text.split("\n")[:-1]:
        stripped = line.lstrip(" ")
        if len(line) - len(stripped) > 3 or not stripped.startswith(_FENCE):
            continue
        ticks = len(stripped) - len(stripped.lstrip("`"))
        if opener is None:
            opener, width = stripped, ticks
        elif stripped.rstrip() == "`" * ticks and ticks >= width:
            opener, width = None, 0
    return opener


def split_point(
    sent: str, incoming: str, soft_limit: int
) -> tuple[int, str | None] | None:
    """
    Find where the current message should end within incoming.

    Once the message holds at least soft_limit characters it ends at the
    first break that keeps the reply's blocks whole: the end of a blank line
    outside a code block, or the end of any line inside one. A table ends at
    a blank line, so no table is cut in two. A code block can run past any
    paragraph, so it is cut between two of its lines: Slack closes the block
    the finished message ends inside, and the next message opens it again.

    Args:
        sent (str): Markdown already handed to the current message.
        incoming (str): Markdown about to be appended.
        soft_limit (int): The length past which the message may end.

    Returns:
        tuple[int, str | None] | None: How much of incoming belongs to the
            current message, the rest opening the next one, and the fence
            line the next message opens with when the break falls inside a
            code block; None when the message does not end within incoming.
    """
    text = sent + incoming
    start = len(sent)
    for end in range(max(start, soft_limit, 1), len(text) + 1):
        if text[end - 1] != "\n":
            continue
        fence = open_fence(text[:end])
        if fence is not None or text[end - 2 : end] == "\n\n":
            return end - start, fence
    return None
