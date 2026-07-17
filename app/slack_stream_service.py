"""Slack chat streaming with rollover past the message length limit.

Slack caps one streamed message at an undocumented total length (observed at
~10,000 characters as of 2026-07): once the accumulated text would cross it,
chat.appendStream and chat.stopStream fail with `msg_too_long`. Long agent
replies are normal, so the failure is absorbed instead of surfaced: the full
message is finalized as it stands, a fresh streamed message opens in the same
thread, and the text the SDK helper had not yet delivered is replayed into
it. The reader just sees the reply continue in a follow-up message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_chat_stream import AsyncChatStream
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PendingAppend:
    """One append handed to the current stream but possibly not delivered.

    The SDK helper buffers markdown until `buffer_size` and only then calls
    the API, so an append that raised — and the buffered appends before it —
    never reached Slack. Keeping them until a flush succeeds is what lets a
    rollover replay them into the next message with nothing lost.
    """

    markdown_text: str | None
    chunks: list[dict] | None


def _is_message_too_long(error: SlackApiError) -> bool:
    if not isinstance(error.response, AsyncSlackResponse):
        return False
    reason = error.response.get("error")
    return isinstance(reason, str) and reason == "msg_too_long"


class RotatingChatStream:
    """A streaming reply that rolls over to a new message when one fills up.

    Wraps the SDK's `AsyncChatStream` with the same append/stop surface. On
    `msg_too_long` the current message is finalized as-is (a normal stop, no
    error shown), a new stream opens in the same thread, and the undelivered
    tail continues there.

    The stream opens lazily, on the first append: a run that delivers
    nothing to render (a failure before the first token, a stop on
    interrupts alone) leaves no message behind. The waiting reaction, not
    an empty stream, is what shows the run is being worked on.
    """

    def __init__(
        self,
        client: AsyncWebClient,
        *,
        channel: str,
        thread_ts: str,
        recipient_team_id: str | None,
        recipient_user_id: str | None,
        buffer_size: int,
    ):
        self._client = client
        self._channel = channel
        self._thread_ts = thread_ts
        self._recipient_team_id = recipient_team_id
        self._recipient_user_id = recipient_user_id
        self._buffer_size = buffer_size
        self._streamer: AsyncChatStream | None = None
        self._pending: list[_PendingAppend] = []

    @property
    def ts(self) -> str | None:
        """The current streamed message's timestamp, if one has started."""
        return self._streamer.ts if self._streamer is not None else None

    async def append(
        self,
        *,
        markdown_text: str | None = None,
        chunks: list[dict] | None = None,
    ) -> None:
        """
        Append to the reply, rolling over to a new message if it is full.

        Args:
            markdown_text (str | None): Markdown to append to the reply.
            chunks (list[dict] | None): Streaming chunks (e.g. task updates).

        Returns:
            None
        """
        self._pending.append(_PendingAppend(markdown_text=markdown_text, chunks=chunks))
        if self._streamer is None:
            self._streamer = await self._new_streamer()
        try:
            response = await self._streamer.append(
                markdown_text=markdown_text, chunks=chunks
            )
        except SlackApiError as error:
            if not _is_message_too_long(error):
                raise
            await self._rotate()
            return
        if response is not None:
            self._pending.clear()

    async def stop(
        self,
        *,
        markdown_text: str | None = None,
        chunks: list[dict] | None = None,
    ) -> None:
        """
        Finalize the reply, rolling over first if the close would overflow.

        A reply that never opened and closes with no new content is a no-op:
        the SDK helper's stop would start a stream just to close it, leaving
        an empty message in the thread.

        Args:
            markdown_text (str | None): Markdown to append before closing.
            chunks (list[dict] | None): Streaming chunks to close with.

        Returns:
            None
        """
        if self._streamer is None and markdown_text is None and not chunks:
            return
        self._pending.append(_PendingAppend(markdown_text=markdown_text, chunks=chunks))
        if self._streamer is None:
            self._streamer = await self._new_streamer()
        try:
            await self._streamer.stop(markdown_text=markdown_text, chunks=chunks)
        except SlackApiError as error:
            if not _is_message_too_long(error):
                raise
            await self._rotate()
            await self._require_streamer().stop()
        else:
            self._pending.clear()

    async def _rotate(self) -> None:
        """Finalize the full message and continue in a fresh one.

        Replays the undelivered appends into the new stream directly on the
        SDK helper: if even the replay overflows (a single oversized delta),
        the error propagates to the caller's failure handling rather than
        rotating forever.
        """
        current = self._require_streamer()
        if current.ts is not None:
            await self._client.chat_stopStream(channel=self._channel, ts=current.ts)
        logger.debug(
            "Streamed message hit the length limit; continuing in a new message "
            "(channel: %s, thread: %s)",
            self._channel,
            self._thread_ts,
        )
        replay = self._pending
        self._pending = []
        self._streamer = await self._new_streamer()
        for item in replay:
            self._pending.append(item)
            response = await self._streamer.append(
                markdown_text=item.markdown_text, chunks=item.chunks
            )
            if response is not None:
                self._pending.clear()

    async def _new_streamer(self) -> AsyncChatStream:
        return await self._client.chat_stream(
            channel=self._channel,
            thread_ts=self._thread_ts,
            recipient_team_id=self._recipient_team_id,
            recipient_user_id=self._recipient_user_id,
            buffer_size=self._buffer_size,
        )

    def _require_streamer(self) -> AsyncChatStream:
        if self._streamer is None:
            raise RuntimeError("The stream has not been opened by an append")
        return self._streamer
