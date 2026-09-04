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

from slack_sdk.errors import SlackApiError
from slack_sdk.models.messages.chunk import MarkdownTextChunk
from slack_sdk.web.async_chat_stream import AsyncChatStream
from slack_sdk.web.async_client import AsyncWebClient

from app.slack_stream_logic import (
    PendingAppends,
    is_message_too_long,
    note_after_reply,
)

logger = logging.getLogger(__name__)


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
        self._pending = PendingAppends()
        self._abandoned = False

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
        if self._abandoned:
            return
        if self._streamer is None:
            self._streamer = await self._new_streamer()
            _open_streams.add(self)
        try:
            await self._append_to_streamer(markdown_text=markdown_text, chunks=chunks)
        except SlackApiError as error:
            if not is_message_too_long(error):
                raise
            await self._rotate()

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
        if self._abandoned or (
            self._streamer is None and markdown_text is None and not chunks
        ):
            return
        # Left here rather than after the close so a shutdown sweep does not
        # close a stream its own caller is already closing.
        _open_streams.discard(self)
        self._pending.record(markdown_text=markdown_text, chunks=chunks)
        if self._streamer is None:
            self._streamer = await self._new_streamer()
        try:
            await self._streamer.stop(markdown_text=markdown_text, chunks=chunks)
        except SlackApiError as error:
            if not is_message_too_long(error):
                raise
            await self._rotate()
            await self._require_streamer().stop()
        else:
            self._pending.clear()

    async def close_unfinished(self, *, markdown_text: str) -> None:
        """
        Close the open message where it stands, saying why.

        For a reply being abandoned rather than finished — a shutdown —
        while the coroutine writing it may still be inside an append. The
        SDK helper clears its buffer only after its own call returns, so a
        `stop` racing that call would read the same buffer and deliver it
        a second time. This goes to the API directly instead: the note is
        the only thing sent, and whatever the helper still holds is
        dropped with the reply it belonged to.

        The stream is left abandoned: a later append or stop on it does
        nothing, so the coroutine that was writing the reply cannot open a
        second message to report the failure it is about to see.

        Args:
            markdown_text (str): Markdown to close the message with.

        Returns:
            None
        """
        # Set before the call: past here the reply is over, and an append
        # or a stop from the coroutine still writing it would land on a
        # message that is closing, or open a second one to report that.
        self._abandoned = True
        ts = self.ts
        if ts is None:
            return
        await self._client.chat_stopStream(
            channel=self._channel,
            ts=ts,
            chunks=[MarkdownTextChunk(text=markdown_text)],
        )

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
        replay = self._pending.drain()
        self._streamer = await self._new_streamer()
        for item in replay:
            await self._append_to_streamer(
                markdown_text=item.markdown_text, chunks=item.chunks
            )

    async def _append_to_streamer(
        self, *, markdown_text: str | None, chunks: list[dict] | None
    ) -> None:
        """Append to the open stream, keeping the append until it lands.

        The SDK helper returns a response only for a call that reached the
        API; a buffered call returns None, leaving the append in the tail
        for a rollover to replay.
        """
        self._pending.record(markdown_text=markdown_text, chunks=chunks)
        response = await self._require_streamer().append(
            markdown_text=markdown_text, chunks=chunks
        )
        if response is not None:
            self._pending.clear()

    async def _new_streamer(self) -> AsyncChatStream:
        return await self._client.chat_stream(
            channel=self._channel,
            thread_ts=self._thread_ts,
            recipient_team_id=self._recipient_team_id,
            recipient_user_id=self._recipient_user_id,
            task_display_mode="plan",
            buffer_size=self._buffer_size,
        )

    def _require_streamer(self) -> AsyncChatStream:
        if self._streamer is None:
            raise RuntimeError("The stream has not been opened by an append")
        return self._streamer


# The streams with a message open in a thread. A shutdown closes what is
# here: a reply cut off mid-flight would otherwise leave a message streaming
# with nothing to say why. A stream joins on the append that opens it — one
# that never opened has no message to close — and leaves when a caller
# stops it.
_open_streams: set[RotatingChatStream] = set()


async def close_open_streams(*, markdown_text: str) -> None:
    """
    Close every stream still open, appending markdown_text to each.

    For shutdown, once new work has stopped arriving. The replies
    themselves cannot be finished — an agent's reply runs for minutes and
    a stopping container has seconds — so each message is closed where it
    stands, saying so. One that fails to close does not stop the rest.

    Args:
        markdown_text (str): Markdown to append before closing each.

    Returns:
        None
    """
    note = note_after_reply(markdown_text)
    for stream in list(_open_streams):
        _open_streams.discard(stream)
        try:
            await stream.close_unfinished(markdown_text=note)
        except Exception:
            logger.exception("Failed to close a stream while shutting down")
