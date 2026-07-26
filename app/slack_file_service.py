"""I/O for downloading Slack files and converting them to wire blocks.

Selection and the retry policy are pure (`slack_file_logic`); this module
performs the authorized downloads with the bot token and base64-encodes the
content for the JSON wire.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import aiohttp
from slack_sdk.errors import SlackApiError

from app.converse_logic import (
    ContentBlock,
    build_document_block,
    build_image_block,
    build_video_block,
)
from app.slack_file_logic import (
    MAX_DOWNLOAD_ATTEMPTS,
    FileToFetch,
    expected_content_types,
    is_retryable_status,
    retry_delay_seconds,
)

logger = logging.getLogger(__name__)

PDF_MAGIC_PREFIX = b"%PDF-"


class _TransientDownloadError(SlackApiError):
    """A download failure a further attempt could still get past."""


async def fetch_file_blocks(
    selections: list[FileToFetch], *, bot_token: str
) -> dict[str, ContentBlock]:
    """
    Download the selected Slack files and build their wire content blocks.

    Args:
        selections (list[FileToFetch]): The files to download.
        bot_token (str): The bot token authorizing the downloads.

    Returns:
        dict[str, ContentBlock]: Content blocks keyed by Slack file ID.
    """
    blocks: dict[str, ContentBlock] = {}
    if not selections:
        return blocks
    async with aiohttp.ClientSession() as session:
        for selection in selections:
            content = await _download_slack_file(
                session=session,
                url=selection.url,
                bot_token=bot_token,
                expected_content_types=expected_content_types(selection.format),
            )
            if selection.format == "pdf" and not content.startswith(PDF_MAGIC_PREFIX):
                logger.warning(f"Skipped invalid PDF (url: {selection.url})")
                continue
            blocks[selection.file_id] = _build_block(
                selection, data_base64=base64.b64encode(content).decode("utf-8")
            )
    return blocks


def _build_block(selection: FileToFetch, *, data_base64: str) -> ContentBlock:
    if selection.modality == "image":
        return build_image_block(image_format=selection.format, data_base64=data_base64)
    if selection.modality == "video":
        return build_video_block(video_format=selection.format, data_base64=data_base64)
    return build_document_block(
        document_format=selection.format,
        name=selection.name,
        data_base64=data_base64,
    )


async def _download_slack_file(
    *,
    session: aiohttp.ClientSession,
    url: str,
    bot_token: str,
    expected_content_types: list[str],
) -> bytes:
    """
    Download one Slack file, retrying the failures a retry could clear.

    A reachability problem — a dropped connection, a timeout, Slack asking
    to slow down or having a bad moment — costs the whole reply otherwise,
    since one unreadable attachment fails the turn. The last attempt's
    error propagates: the file the agent was meant to see is missing either
    way, and failing loudly beats answering about a file it never got.

    Args:
        session (aiohttp.ClientSession): The session to download through.
        url (str): The file's private download URL.
        bot_token (str): The bot token authorizing the download.
        expected_content_types (list[str]): The Content-Type values the
            file's format may legitimately arrive as.

    Returns:
        bytes: The file's raw content.

    Raises:
        SlackApiError: If the download fails for good, or keeps failing.
    """

    async def read() -> bytes:
        return await _read_slack_file(
            session=session,
            url=url,
            bot_token=bot_token,
            expected_content_types=expected_content_types,
        )

    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS):
        try:
            return await read()
        except _TransientDownloadError:
            delay = retry_delay_seconds(attempt)
            logger.warning(
                "Retrying a Slack file download in %ss (attempt %s of %s, url: %s)",
                delay,
                attempt + 1,
                MAX_DOWNLOAD_ATTEMPTS,
                url,
                exc_info=True,
            )
            await asyncio.sleep(delay)
    return await read()


async def _read_slack_file(
    *,
    session: aiohttp.ClientSession,
    url: str,
    bot_token: str,
    expected_content_types: list[str],
) -> bytes:
    try:
        async with session.get(
            url,
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                message = f"Request to {url} failed with status code {response.status}"
                if is_retryable_status(response.status):
                    raise _TransientDownloadError(message, response)
                raise SlackApiError(message, response)
            content_type = response.headers.get("Content-Type", "")
            if content_type.startswith("text/html"):
                raise SlackApiError(
                    f"You don't have the permission to download this file: {url}",
                    response,
                )
            # Slack may append parameters (e.g. "; charset=utf-8") to text types.
            mime_type = content_type.split(";")[0].strip()
            if mime_type not in expected_content_types:
                raise SlackApiError(
                    f"The responded content-type is not expected: {content_type}",
                    response,
                )
            return await response.read()
    except (aiohttp.ClientError, TimeoutError) as error:
        # The connection never carried a response to judge, which is the
        # kind of failure that most often clears on its own.
        message = f"Request to {url} failed: {error}"
        raise _TransientDownloadError(message, None) from error
