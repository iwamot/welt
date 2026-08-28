"""Pure logic for cutting a thread down to what one request payload holds.

`slack_file_logic` decides which files are eligible at all; this module
decides which of them, and which replies, survive once the whole payload has
to fit. It works from Slack's size metadata rather than the files themselves,
so what it discards is never downloaded.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.converse_logic import build_reply_text
from app.slack_file_logic import FileToFetch

# What Welt lets one payload reach. Converse refuses a request whose whole
# body passes 32,000,000 bytes (measured 2026-08-26, the same boundary on
# nova-lite and claude-haiku-4-5). The 2,000,000 between that and this is not
# what the estimating below needs — the allowances are generous enough to
# leave about 23,000 bytes spare on a thread of a thousand replies with every
# file slot filled — and closing it would win nothing: a thread carrying this
# much already takes long enough to transfer that the last two megabytes make
# no odds, and a round number is easier to keep in mind than a tight one.
MAX_PAYLOAD_BYTES = 30_000_000

# Added to each file's encoded length for the JSON around it. The largest is
# a document block, at 268 bytes once its name runs to the 200 characters
# Converse allows; an image block is 53.
_FILE_BLOCK_OVERHEAD = 320

# Added to each reply's text for the role, the content list, and the commas,
# which come to 41 bytes.
_MESSAGE_OVERHEAD = 64


def file_payload_bytes(size: int) -> int:
    """
    Say what a file of this size costs the payload.

    The wire carries base64, which grows 4/3 and pads to a multiple of four.

    Args:
        size (int): The file's size in bytes, from Slack's metadata.

    Returns:
        int: The bytes the file's content block adds to the payload.
    """
    return 4 * ((size + 2) // 3) + _FILE_BLOCK_OVERHEAD


def reply_payload_bytes(
    reply: dict, *, bot_user_id: str | None, display_names: Mapping[str, str]
) -> int:
    """
    Say what a reply's text costs the payload.

    `json.dumps` is what `agent_service` encodes the payload with, so quoting
    and escaping count as they will travel — a Japanese character leaves as
    six ASCII bytes, not three. The text is the one `converse_logic` will
    send, because what the payload carries is not what Slack stores: a reply
    of Welt's own is read back from its blocks, where a table comes back a
    table and not the summary line Slack left in its `text`, and a person's
    `*bold*` leaves as `**bold**`, growing a reply written mostly in
    emphasis by half again.

    Args:
        reply (dict): A Slack reply.
        bot_user_id (str | None): The bot's own user ID.
        display_names (Mapping[str, str]): Names by Slack ID, so that what
            is counted is the text as it will be sent — a name is rarely
            the length of the ID it stands in for.

    Returns:
        int: The bytes the reply's message adds to the payload.
    """
    text = build_reply_text(reply, bot_user_id=bot_user_id, display_names=display_names)
    return len(json.dumps(text)) + _MESSAGE_OVERHEAD


def compact_to_payload_budget(
    replies: list[dict],
    selections: list[FileToFetch],
    *,
    bot_user_id: str | None,
    display_names: Mapping[str, str],
    max_payload_bytes: int,
) -> tuple[list[dict], list[FileToFetch]]:
    """
    Drop from the oldest end until the thread fits one payload.

    An attachment goes before the message carrying it, and the oldest message
    is emptied before a newer one is touched: the oldest message's last
    attachment first, then the rest of them backwards, then the message
    itself, then on to the next-oldest. A thread loses its old pictures
    before it loses anything anyone said, and loses the oldest of what was
    said before the newest.

    Taking a message's attachments from the end reads its `files` array as
    the order they were attached in, on the view that what someone attached
    first is what they meant most. Slack does not document that order; a
    message posted with three attachments carried them in the order they
    went up, oldest `created` first (measured 2026-08-26, attached one at a
    time). Several picked at once was not part of that, and where the array
    turns out not to follow attachment order, which of a message's
    attachments goes is arbitrary rather than wrong.

    The last remaining reply is never dropped — it carries the post being
    answered — though its own attachments still are. Its text alone always
    fits: Slack truncates a message at 40,000 characters, which is far short
    of the budget even at six bytes each.

    Args:
        replies (list[dict]): Slack replies in chronological order.
        selections (list[FileToFetch]): The files eligible for download.
        bot_user_id (str | None): The bot's own user ID.
        display_names (Mapping[str, str]): Names by Slack ID.
        max_payload_bytes (int): The ceiling for the whole payload
            (`MAX_PAYLOAD_BYTES`).

    Returns:
        tuple[list[dict], list[FileToFetch]]: The replies that survive, in
            chronological order, and the files still worth downloading.
    """
    sizes = _file_sizes_by_id(replies)
    kept_replies = list(replies)
    kept_files = list(selections)
    total = sum(
        reply_payload_bytes(reply, bot_user_id=bot_user_id, display_names=display_names)
        for reply in kept_replies
    ) + sum(
        file_payload_bytes(sizes.get(selection.file_id, 0)) for selection in kept_files
    )
    while total > max_payload_bytes and kept_replies:
        oldest = kept_replies[0]
        dropped = _take_last_file_of(oldest, kept_files)
        if dropped is not None:
            total -= file_payload_bytes(sizes.get(dropped.file_id, 0))
            continue
        if len(kept_replies) == 1:
            break
        total -= reply_payload_bytes(
            oldest, bot_user_id=bot_user_id, display_names=display_names
        )
        kept_replies.pop(0)
    return kept_replies, kept_files


def _take_last_file_of(
    reply: dict, kept_files: list[FileToFetch]
) -> FileToFetch | None:
    """Remove and return the reply's last still-kept attachment, if it has one."""
    for file_id in reversed(_file_ids_of(reply)):
        for index, selection in enumerate(kept_files):
            if selection.file_id == file_id:
                return kept_files.pop(index)
    return None


def _file_ids_of(reply: dict) -> list[str]:
    files = reply.get("files")
    if not isinstance(files, list):
        return []
    return [
        file["id"]
        for file in files
        if isinstance(file, dict) and isinstance(file.get("id"), str)
    ]


def _file_sizes_by_id(replies: list[dict]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for reply in replies:
        for file in reply.get("files") or []:
            if not isinstance(file, dict):
                continue
            file_id = file.get("id")
            size = file.get("size")
            if isinstance(file_id, str) and isinstance(size, int):
                sizes[file_id] = size
    return sizes
