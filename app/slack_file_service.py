"""I/O for downloading Slack files and converting them to wire blocks.

Selection is pure (`slack_file_logic`); this module performs the authorized
downloads with the bot token and base64-encodes the content for the JSON wire.
"""

from __future__ import annotations

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
from app.slack_file_logic import FileToFetch, expected_content_types

logger = logging.getLogger(__name__)

PDF_MAGIC_PREFIX = b"%PDF-"


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
    async with session.get(
        url,
        headers={"Authorization": f"Bearer {bot_token}"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as response:
        if response.status != 200:
            raise SlackApiError(
                f"Request to {url} failed with status code {response.status}",
                response,
            )
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
