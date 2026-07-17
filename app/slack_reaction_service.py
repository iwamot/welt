"""Marks a Slack post with a waiting reaction while Welt works on it.

The marker sits on the post being replied to — or on the interrupt
button message whose answer is being processed — for as long as Welt is
working on it; it comes off when the run ends, whether that is a finished
reply, a posted interrupt prompt, or a reported failure. Both sides are
best-effort — an install that predates the reactions:write scope simply
goes without the marker.
"""

from __future__ import annotations

import logging

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

# The waiting marker — deliberately not configurable, like the fixed texts
# in the listeners, so the frame around the conversation reads the same on
# every deployment.
WAITING_REACTION = "eyes"


class WaitingReaction:
    """The waiting marker on one Slack post, added once and cleared once."""

    def __init__(
        self,
        client: AsyncWebClient,
        *,
        channel_id: str,
        message_ts: str,
    ) -> None:
        """
        Bind the marker to the post it will sit on.

        Args:
            client (AsyncWebClient): The Slack Web API client.
            channel_id (str): The ID of the channel holding the post.
            message_ts (str): The timestamp of the post to mark.
        """
        self._client = client
        self._channel_id = channel_id
        self._message_ts = message_ts
        self._active = False

    async def add(self) -> None:
        """
        Put the marker on the post; a failure just leaves it off.

        Returns:
            None
        """
        try:
            await self._client.reactions_add(
                channel=self._channel_id,
                timestamp=self._message_ts,
                name=WAITING_REACTION,
            )
        except SlackApiError:
            logger.debug("Could not add the waiting reaction", exc_info=True)
            return
        self._active = True

    async def clear(self) -> None:
        """
        Take the marker off the post. Idempotent — later calls no-op.

        Returns:
            None
        """
        if not self._active:
            return
        self._active = False
        try:
            await self._client.reactions_remove(
                channel=self._channel_id,
                timestamp=self._message_ts,
                name=WAITING_REACTION,
            )
        except SlackApiError:
            logger.debug("Could not remove the waiting reaction", exc_info=True)
