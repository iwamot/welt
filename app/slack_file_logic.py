"""Pure logic for deciding which Slack files to fetch, and how to retry.

The actual download is I/O (`slack_file_service`); this module only inspects
Slack file metadata, applies the allowed-modality configuration
(`FILE_INPUT_MODALITIES`), judges a failed download's worth of another
attempt, and shapes a downloaded file into its wire block, so it can be
covered by fixture-driven tests.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal

from slack_bolt.context.base_context import BaseContext

from app.bolt_logic import has_read_files_scope
from app.converse_logic import (
    ContentBlock,
    build_document_block,
    build_image_block,
    build_video_block,
)

Modality = Literal["image", "document", "video"]

# Converse format -> (content-block modality, Slack `mimetype` values that
# identify it). `mpeg` and `mpg` share a MIME type; `mpeg`, listed first, wins.
CONVERSE_FORMATS: dict[str, tuple[Modality, tuple[str, ...]]] = {
    "png": ("image", ("image/png",)),
    "jpeg": ("image", ("image/jpeg",)),
    "gif": ("image", ("image/gif",)),
    "webp": ("image", ("image/webp",)),
    "pdf": ("document", ("application/pdf",)),
    "csv": ("document", ("text/csv",)),
    "doc": ("document", ("application/msword",)),
    "docx": (
        "document",
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
    ),
    "xls": ("document", ("application/vnd.ms-excel",)),
    "xlsx": (
        "document",
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    ),
    "html": ("document", ("text/html",)),
    "txt": ("document", ("text/plain",)),
    "md": ("document", ("text/markdown",)),
    "mkv": ("video", ("video/x-matroska",)),
    "mov": ("video", ("video/quicktime",)),
    "mp4": ("video", ("video/mp4",)),
    "webm": ("video", ("video/webm",)),
    "flv": ("video", ("video/x-flv",)),
    "mpeg": ("video", ("video/mpeg",)),
    "mpg": ("video", ("video/mpeg",)),
    "wmv": ("video", ("video/x-ms-wmv",)),
    "three_gp": ("video", ("video/3gpp",)),
}

# The modality names FILE_INPUT_MODALITIES accepts; looking a token up here
# narrows it from str to Modality.
_MODALITY_BY_NAME: dict[str, Modality] = {
    "image": "image",
    "document": "document",
    "video": "video",
}

# Per-request block counts; recent replies win the slots. Only the document
# count is Converse's own ("You can't include more than 5 documents in a
# request"). The video count is Nova's, which refuses a second video. The
# image count is the ceiling Bedrock documents rather than one that is
# enforced — a 21st image is accepted — so 20 is where Welt stops on its own.
MAX_SLOTS_BY_MODALITY: dict[Modality, int] = {"image": 20, "document": 5, "video": 1}

# Per-file size limits, checked against Slack `size` metadata before download.
# Each is the largest raw size its boundary still accepts, and the three come
# from three places that count in three different units (measured 2026-08-26,
# nova-lite and claude-haiku-4-5):
#   image     Anthropic models cap the base64 form at 5 MiB, so 3,932,160 raw
#             bytes encode to exactly the limit. Converse does not check an
#             image's size itself; Nova takes far larger ones.
#   document  Converse's own cap, on the raw bytes, and 4.5 MB means
#             4,500,000: one byte more is rejected.
#   video     Nova caps the base64 form at 25,000,000, so 18,750,000 raw bytes
#             encode to exactly the limit.
MAX_BYTES_BY_MODALITY: dict[Modality, int] = {
    "image": 3_932_160,
    "document": 4_500_000,
    "video": 18_750_000,
}

# Slack sometimes serves PDFs as a generic binary stream.
_EXTRA_CONTENT_TYPES: dict[str, tuple[str, ...]] = {"pdf": ("binary/octet-stream",)}

# How many times a single file download is attempted before the reply gives
# up, and how long the first retry waits. A blip on the way to Slack's file
# host would otherwise cost the whole reply, since one unreadable attachment
# fails the turn.
MAX_DOWNLOAD_ATTEMPTS = 3
_FIRST_RETRY_DELAY_SECONDS = 0.5


def parse_file_input_modalities(value: str) -> tuple[Modality, ...]:
    """
    Parse the FILE_INPUT_MODALITIES environment variable (CSV of modalities).

    Tokens are case-insensitive; blanks and duplicates are dropped. An empty
    value disables file input entirely.

    Args:
        value (str): The raw CSV value, e.g. ``"image,document"``.

    Returns:
        tuple[Modality, ...]: The allowed modalities, in input order.

    Raises:
        ValueError: If the value names anything other than a Converse
            content-block modality (image, document, video).
    """
    modalities: list[Modality] = []
    for token in value.split(","):
        name = token.strip().lower()
        if not name:
            continue
        modality = _MODALITY_BY_NAME.get(name)
        if modality is None:
            supported = ", ".join(_MODALITY_BY_NAME)
            raise ValueError(
                f"FILE_INPUT_MODALITIES contains an unsupported modality "
                f"{name!r} (supported: {supported})"
            )
        if modality not in modalities:
            modalities.append(modality)
    return tuple(modalities)


def expected_content_types(file_format: str) -> list[str]:
    """
    List the Content-Type values a download of this format may respond with.

    Args:
        file_format (str): A Converse format from `CONVERSE_FORMATS`.

    Returns:
        list[str]: The acceptable Content-Type values.
    """
    _, mime_types = CONVERSE_FORMATS[file_format]
    return [*mime_types, *_EXTRA_CONTENT_TYPES.get(file_format, ())]


def is_retryable_status(status: int) -> bool:
    """
    Judge whether a failed download's status is worth another attempt.

    Args:
        status (int): The response status of a failed download.

    Returns:
        bool: True for 429 and the 5xx range — Slack asking to slow down, or
            a bad moment on its side, both of which pass. False for anything
            else: a file the bot may not read answers 403 however often it
            is asked.
    """
    return status == 429 or 500 <= status < 600


def retry_delay_seconds(attempt: int) -> float:
    """
    Say how long to wait after a failed attempt before making the next one.

    The delay doubles per attempt, so a file host having a bad moment gets
    progressively more room instead of a burst of identical retries.

    Args:
        attempt (int): The number of the attempt that just failed, counting
            from 1.

    Returns:
        float: The seconds to wait before the next attempt.
    """
    return _FIRST_RETRY_DELAY_SECONDS * 2 ** (attempt - 1)


@dataclass(frozen=True)
class FileToFetch:
    """A Slack file selected for download and conversion to a wire block."""

    file_id: str
    url: str
    modality: Modality
    format: str
    name: str | None


def select_files_to_fetch(
    replies: list[dict],
    *,
    bot_user_id: str | None,
    allowed_modalities: Collection[Modality],
    max_slots_by_modality: Mapping[Modality, int],
    max_bytes_by_modality: Mapping[Modality, int],
) -> list[FileToFetch]:
    """
    Select which Slack files Welt should download for the agent payload.

    Only files posted by humans count (bot posts are excluded). Each modality
    (image / document / video) fills at most its slots, preferring the most
    recent replies so old attachments fall off first. Files whose MIME type
    maps to no allowed modality, whose size is missing, unreadable, zero, or
    past the modality's ceiling, are skipped without consuming a slot. The
    slots and ceilings are `MAX_SLOTS_BY_MODALITY` and
    `MAX_BYTES_BY_MODALITY`, which say where each of them comes from.

    What the selection adds up to is not bounded here; `payload_logic`
    trims the thread to one payload once the text is counted with it.

    Args:
        replies (list[dict]): Slack replies in chronological order.
        bot_user_id (str | None): The bot's own user ID.
        allowed_modalities (Collection[Modality]): The allowed modalities.
        max_slots_by_modality (Mapping[Modality, int]): Per-modality slot
            limits.
        max_bytes_by_modality (Mapping[Modality, int]): Per-modality file
            size limits in bytes.

    Returns:
        list[FileToFetch]: The files to download.
    """
    selected: list[FileToFetch] = []
    used_slots: dict[Modality, int] = {"image": 0, "document": 0, "video": 0}
    for reply in reversed(replies):
        if reply.get("bot_id") is not None:
            continue
        if bot_user_id is not None and reply.get("user") == bot_user_id:
            continue
        files = reply.get("files")
        if not isinstance(files, list):
            continue
        for file in files:
            selection = _select_file(
                file,
                allowed_modalities=allowed_modalities,
                max_bytes_by_modality=max_bytes_by_modality,
            )
            if selection is None:
                continue
            used = used_slots[selection.modality]
            if used >= max_slots_by_modality.get(selection.modality, 0):
                continue
            used_slots[selection.modality] = used + 1
            selected.append(selection)
    return selected


def _select_file(
    file: object,
    *,
    allowed_modalities: Collection[Modality],
    max_bytes_by_modality: Mapping[Modality, int],
) -> FileToFetch | None:
    if not isinstance(file, dict):
        return None
    file_id = file.get("id")
    url = file.get("url_private")
    if not isinstance(file_id, str) or not isinstance(url, str):
        return None
    resolved = _resolve_format(file.get("mimetype"), allowed_modalities)
    if resolved is None:
        return None
    file_format, modality = resolved
    size = file.get("size")
    max_bytes = max_bytes_by_modality.get(modality, 0)
    # `size` is Slack's metadata rather than the file, and it is not always
    # a number to compare: an entry that names no size, or names something
    # other than an integer, is skipped here instead of downloaded to find
    # out. Zero is skipped with it, though neither upload path produces one —
    # the Slack client refuses to attach an empty file, and
    # files.getUploadURLExternal answers a length of 0 with missing_argument.
    if not isinstance(size, int) or not 0 < size <= max_bytes:
        return None
    name = file.get("name")
    return FileToFetch(
        file_id=file_id,
        url=url,
        modality=modality,
        format=file_format,
        name=name if isinstance(name, str) else None,
    )


def _resolve_format(
    mime_type: object, allowed_modalities: Collection[Modality]
) -> tuple[str, Modality] | None:
    for file_format, (modality, mime_types) in CONVERSE_FORMATS.items():
        if modality in allowed_modalities and mime_type in mime_types:
            return file_format, modality
    return None


def select_files_for_replies(
    context: BaseContext,
    replies: list[dict],
    *,
    allowed_modalities: tuple[Modality, ...],
) -> list[FileToFetch]:
    """
    Choose the files the replies carry, if file input is enabled.

    Nothing is downloaded here: the thread is still to be trimmed to one
    payload, and a file that does not survive that is never fetched.

    Args:
        context (BaseContext): The Bolt context object.
        replies (list[dict]): Slack replies in chronological order.
        allowed_modalities (tuple[Modality, ...]): The modalities to accept
            (`Env.file_input_modalities`); empty disables file input.

    Returns:
        list[FileToFetch]: The eligible files.
    """
    if not allowed_modalities:
        return []
    if not has_read_files_scope(context.authorize_result):
        return []
    return select_files_to_fetch(
        replies,
        bot_user_id=context.bot_user_id,
        allowed_modalities=allowed_modalities,
        max_slots_by_modality=MAX_SLOTS_BY_MODALITY,
        max_bytes_by_modality=MAX_BYTES_BY_MODALITY,
    )


def build_file_block(selection: FileToFetch, *, data_base64: str) -> ContentBlock:
    """
    Shape a downloaded file into the wire block its modality calls for.

    Args:
        selection (FileToFetch): The file, as selected for download.
        data_base64 (str): The downloaded content, base64-encoded.

    Returns:
        ContentBlock: The image, video, or document block for the JSON wire.
    """
    if selection.modality == "image":
        return build_image_block(image_format=selection.format, data_base64=data_base64)
    if selection.modality == "video":
        return build_video_block(video_format=selection.format, data_base64=data_base64)
    return build_document_block(
        document_format=selection.format,
        name=selection.name,
        data_base64=data_base64,
    )
