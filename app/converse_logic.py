"""Pure logic for building the Converse-shaped request payload.

Welt sends Bedrock Converse-shaped messages (JSON-safe) to the agent, which
feeds them to Strands. Image / document / video blocks carry base64 in the
`bytes` slot (JSON cannot carry raw bytes), so the agent decodes them back to
bytes before handing the messages to Strands.

When the agent keeps the conversation history itself — always the case for
a managed harness (stored under the runtimeSessionId), opt-in for a Runtime
agent via AGENT_MANAGES_HISTORY — Welt sends only the messages the agent has
not seen yet (`keep_messages_after_last_assistant`) instead of the whole
thread.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from app.message_logic import (
    build_slack_user_prefixed_text,
    remove_bot_mention,
    slack_to_markdown,
    unescape_slack_formatting,
)


class TextBlock(TypedDict):
    """A Converse text content block."""

    text: str


class FileSource(TypedDict):
    """A Converse bytes source, base64-encoded for the JSON wire."""

    bytes: str


class ImageContent(TypedDict):
    """The inner value of a Converse image content block."""

    format: str
    source: FileSource


class ImageBlock(TypedDict):
    """A Converse image content block."""

    image: ImageContent


class DocumentContent(TypedDict):
    """The inner value of a Converse document content block."""

    format: str
    name: str
    source: FileSource


class DocumentBlock(TypedDict):
    """A Converse document content block."""

    document: DocumentContent


class VideoContent(TypedDict):
    """The inner value of a Converse video content block."""

    format: str
    source: FileSource


class VideoBlock(TypedDict):
    """A Converse video content block."""

    video: VideoContent


ContentBlock = TextBlock | ImageBlock | DocumentBlock | VideoBlock


class Message(TypedDict):
    """A Bedrock Converse message."""

    role: Literal["user", "assistant"]
    content: list[ContentBlock]


def build_messages(
    replies: list[dict],
    *,
    bot_user_id: str | None,
    file_blocks_by_id: dict[str, ContentBlock] | None = None,
) -> list[Message]:
    """
    Convert Slack replies (chronological order) into Converse-shaped messages.

    Trailing bot replies (e.g. a stale loading message) are dropped, and so are
    leading ones (left behind when an overlong thread is truncated to its
    newest replies) because Converse requires a conversation to start with a
    user message. Bot replies whose text is empty after cleaning are skipped
    so no blank content block is sent; bot replies with text become
    `assistant` messages. Everyone else becomes a `user` message prefixed with
    their mention so the model can attribute turns — always, even when the
    text is empty after cleaning (e.g. a mention-only call): the prefix keeps
    the text block non-blank for Converse, and the model sees who pinged it
    without saying anything. File blocks are attached to
    the user message of the reply that carried the file: documents before the
    text, images and videos after it (Converse rejects some block orders).

    Args:
        replies (list[dict]): Slack replies in chronological order.
        bot_user_id (str | None): The bot's own user ID.
        file_blocks_by_id (dict[str, ContentBlock] | None): Fetched file
            blocks keyed by Slack file ID.

    Returns:
        list[Message]: The conversation as Converse-shaped messages.
    """
    messages: list[Message] = []
    for reply in _drop_surrounding_bot_replies(replies, bot_user_id):
        message = _reply_to_message(
            reply,
            bot_user_id=bot_user_id,
            file_blocks_by_id=file_blocks_by_id or {},
        )
        if message is not None:
            messages.append(message)
    return messages


def _drop_surrounding_bot_replies(
    replies: list[dict], bot_user_id: str | None
) -> list[dict]:
    result = list(replies)
    if bot_user_id is None:
        return result
    while result and result[-1].get("user") == bot_user_id:
        result.pop()
    while result and result[0].get("user") == bot_user_id:
        result.pop(0)
    return result


def _reply_to_message(
    reply: dict,
    *,
    bot_user_id: str | None,
    file_blocks_by_id: dict[str, ContentBlock],
) -> Message | None:
    text = remove_bot_mention(_text_of(reply), bot_user_id)
    text = unescape_slack_formatting(text)
    text = slack_to_markdown(text)
    if bot_user_id is not None and reply.get("user") == bot_user_id:
        if not text.strip():
            return None
        return {"role": "assistant", "content": [{"text": text}]}
    document_blocks, media_blocks = _file_blocks_of(reply, file_blocks_by_id)
    text_block: TextBlock = {"text": build_slack_user_prefixed_text(reply, text)}
    return {
        "role": "user",
        "content": [*document_blocks, text_block, *media_blocks],
    }


def _file_blocks_of(
    reply: dict, file_blocks_by_id: dict[str, ContentBlock]
) -> tuple[list[ContentBlock], list[ContentBlock]]:
    document_blocks: list[ContentBlock] = []
    media_blocks: list[ContentBlock] = []
    files = reply.get("files")
    if not isinstance(files, list):
        return document_blocks, media_blocks
    for file in files:
        if not isinstance(file, dict):
            continue
        file_id = file.get("id")
        if not isinstance(file_id, str):
            continue
        block = file_blocks_by_id.get(file_id)
        if block is None:
            continue
        if "document" in block:
            document_blocks.append(block)
        else:
            media_blocks.append(block)
    return document_blocks, media_blocks


def _text_of(reply: dict) -> str:
    value = reply.get("text", "")
    return value if isinstance(value, str) else ""


def keep_messages_after_last_assistant(messages: list[Message]) -> list[Message]:
    """
    Keep the messages that follow the conversation's last assistant reply.

    An agent that manages its own history (a harness, or a Runtime agent
    with AGENT_MANAGES_HISTORY) already holds the earlier turns, so
    re-sending the whole thread would duplicate them. The messages after
    Welt's own last reply are exactly the ones the agent has not seen yet
    (the whole thread on the first invocation).

    Args:
        messages (list[Message]): The conversation as Converse-shaped messages.

    Returns:
        list[Message]: The trailing messages after the last assistant one.
    """
    for index in range(len(messages) - 1, -1, -1):
        if messages[index]["role"] == "assistant":
            return messages[index + 1 :]
    return messages


def build_image_block(*, image_format: str, data_base64: str) -> ImageBlock:
    """
    Build a Converse image content block for the JSON wire.

    Args:
        image_format (str): The Converse image format (png / jpeg / gif / webp).
        data_base64 (str): The image bytes, base64-encoded.

    Returns:
        ImageBlock: The image content block.
    """
    return {"image": {"format": image_format, "source": {"bytes": data_base64}}}


def build_video_block(*, video_format: str, data_base64: str) -> VideoBlock:
    """
    Build a Converse video content block for the JSON wire.

    Args:
        video_format (str): The Converse video format (e.g. mp4).
        data_base64 (str): The video bytes, base64-encoded.

    Returns:
        VideoBlock: The video content block.
    """
    return {"video": {"format": video_format, "source": {"bytes": data_base64}}}


def build_document_block(
    *, document_format: str, name: str | None, data_base64: str
) -> DocumentBlock:
    """
    Build a Converse document content block for the JSON wire.

    Args:
        document_format (str): The Converse document format (e.g. pdf).
        name (str | None): The file name; sanitized to what Converse allows.
        data_base64 (str): The document bytes, base64-encoded.

    Returns:
        DocumentBlock: The document content block.
    """
    return {
        "document": {
            "format": document_format,
            "name": sanitize_document_name(name),
            "source": {"bytes": data_base64},
        }
    }


def sanitize_document_name(name: str | None) -> str:
    """
    Sanitize a file name to what the Converse document block accepts.

    Converse allows only alphanumeric characters, single whitespace, hyphens,
    parentheses, and square brackets in a document name.

    Args:
        name (str | None): The original file name.

    Returns:
        str: A non-empty sanitized name.
    """
    sanitized = re.sub(r"[^0-9A-Za-z\-()\[\] ]", "-", name or "")
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized or "document"
