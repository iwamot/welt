"""Pure logic for the streaming reply's undelivered tail.

The SDK helper buffers markdown until its buffer size and only then calls
the API, so an append that raised — and the buffered appends before it —
never reached Slack. `PendingAppends` is that tail: appends accumulate
until a call reports having reached the API, and a rollover drains them to
replay into the fresh message with nothing lost. The stream I/O itself
lives in `slack_stream_service`.
"""

from __future__ import annotations

from dataclasses import dataclass


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
