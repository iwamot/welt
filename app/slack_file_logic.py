"""Pure logic for deciding which Slack files to fetch for the agent.

The actual download is I/O (`slack_file_service`); this module only inspects
Slack file metadata and applies the allowed-modality configuration
(`FILE_INPUT_MODALITIES`), so it can be covered by fixture-driven tests.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal

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

# Converse per-request block limits; recent replies win the slots.
MAX_SLOTS_BY_MODALITY: dict[Modality, int] = {"image": 20, "document": 5, "video": 1}

# Converse per-file size limits, checked against Slack `size` metadata before
# download. Image/document are the documented per-file caps (3.75 MB / 4.5 MB);
# the video cap is the largest raw size whose base64 form (4/3 growth) still
# fits Nova's 25 MB total-payload limit.
MAX_BYTES_BY_MODALITY: dict[Modality, int] = {
    "image": 3_932_160,  # 3.75 MB
    "document": 4_718_592,  # 4.5 MB
    "video": 19_660_800,  # 25 MB * 3/4
}

# Slack sometimes serves PDFs as a generic binary stream.
_EXTRA_CONTENT_TYPES: dict[str, tuple[str, ...]] = {"pdf": ("binary/octet-stream",)}


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
    (image / document / video) fills at most its Converse per-request slots,
    preferring the most recent replies so old attachments fall off first.
    Files whose MIME type maps to no allowed modality, whose size exceeds the
    modality's Converse limit, or with missing metadata, are skipped without
    consuming a slot.

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
    if not isinstance(size, int) or size > max_bytes_by_modality.get(modality, 0):
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
