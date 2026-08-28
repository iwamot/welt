"""Pure logic for building the Converse-shaped request payload.

Welt sends Bedrock Converse-shaped messages (JSON-safe) to the agent, which
feeds them to its framework. Image / document / video blocks carry base64 in
the `bytes` slot (JSON cannot carry raw bytes), so the agent decodes them
back to bytes before handing the messages on.

When the agent keeps the conversation history itself — always the case for
a managed harness (stored under the runtimeSessionId), opt-in for a Runtime
agent via AGENT_MANAGES_HISTORY — Welt sends only the messages the agent has
not seen yet (`keep_messages_after_last_assistant`) instead of the whole
thread.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, TypedDict, TypeGuard

from app.message_logic import build_slack_user_prefixed_text
from app.slack_markdown_logic import (
    attachments_to_markdown,
    blocks_to_markdown,
    mrkdwn_to_markdown,
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


# The longest document name Converse accepts.
DOCUMENT_NAME_MAX_LENGTH = 200

# A mention of a person or an app, as a message carries it. What Slack
# writes for a user group (`<!subteam^S0123>`), for a broadcast (`<!here>`)
# and for a channel (`<#C0123>`) is left as it came: each of those says on
# its own what it points at, where an ID says nothing without a lookup.
_MENTION = re.compile(r"<@([UW][A-Z0-9]{2,})>")


def build_messages(
    replies: list[dict],
    *,
    bot_user_id: str | None,
    file_blocks_by_id: dict[str, ContentBlock] | None = None,
    display_names: Mapping[str, str] | None = None,
) -> list[Message]:
    """
    Convert Slack replies (chronological order) into Converse-shaped messages.

    A reply is read from its blocks where it has any and from its text
    where it has none, whoever sent it. Blocks are the message as the
    thread shows it — what Welt streamed as Markdown, what another app
    assembled, what a person composed — while the `text` beside them is a
    summary Slack writes, which flattens headings into the paragraph
    below, drops tables outright, and appends a notice of its own where the
    message holds a widget. A message with no blocks is one posted as text,
    and then its text is all there is.

    Trailing bot replies (e.g. a stale loading message) are dropped, and so are
    leading ones (left behind when an overlong thread is truncated to its
    newest replies) so the conversation starts with something a person said
    rather than mid-answer. Nova enforces the same shape, refusing a
    conversation that opens on an assistant message, where Anthropic's
    models take one. Welt's replies that say nothing at all are skipped so no
    blank content block is sent; the rest become `assistant` messages.
    Everyone else becomes a `user` message prefixed with their name so
    the model can attribute turns — always, even where the message says
    nothing (e.g. a mention-only call): the prefix keeps the text block
    non-blank for Converse, and the model sees who pinged it without
    saying anything. That text block is also what lets a reply carry a
    document at all: Converse refuses a message holding a document and no
    text ("A text block must be included when using documents"). File blocks
    go on the user message of the reply that carried the file, documents
    ahead of the text and images and videos behind it — an order Welt keeps
    for readability, since Converse accepts any.
    Documents sharing a name are renamed apart, because Converse rejects a
    request whose messages carry two documents under one name — a thread
    where the same file name is uploaded twice would otherwise fail.

    Args:
        replies (list[dict]): Slack replies in chronological order.
        bot_user_id (str | None): The bot's own user ID.
        file_blocks_by_id (dict[str, ContentBlock] | None): Fetched file
            blocks keyed by Slack file ID.
        display_names (Mapping[str, str] | None): Names by Slack ID, for
            the people and apps the thread mentions.

    Returns:
        list[Message]: The conversation as Converse-shaped messages.
    """
    messages: list[Message] = []
    for reply in _drop_surrounding_bot_replies(replies, bot_user_id):
        message = _reply_to_message(
            reply,
            bot_user_id=bot_user_id,
            file_blocks_by_id=file_blocks_by_id or {},
            display_names=display_names or {},
        )
        if message is not None:
            messages.append(message)
    return _with_unique_document_names(messages)


def _with_unique_document_names(messages: list[Message]) -> list[Message]:
    """
    Rename repeated document names apart, across the whole conversation.

    Args:
        messages (list[Message]): The conversation as Converse-shaped messages.

    Returns:
        list[Message]: The messages, each document under a name of its own;
            blocks that keep their name are returned as they came.
    """
    taken: set[str] = set()
    return [
        {
            "role": message["role"],
            "content": [_uniquely_named(block, taken) for block in message["content"]],
        }
        for message in messages
    ]


def _uniquely_named(block: ContentBlock, taken: set[str]) -> ContentBlock:
    """
    Rename a document block if its name is already taken.

    Args:
        block (ContentBlock): The content block to name.
        taken (set[str]): The document names used so far, extended with the
            name this block ends up under.

    Returns:
        ContentBlock: The block, renamed only if its name was taken; blocks
            other than documents pass through (only documents carry a name).
    """
    if not _is_document_block(block):
        return block
    document = block["document"]
    name = _unique_document_name(document["name"], taken)
    taken.add(name)
    if name == document["name"]:
        return block
    return {
        "document": {
            "format": document["format"],
            "name": name,
            "source": document["source"],
        }
    }


def _is_document_block(block: ContentBlock) -> TypeGuard[DocumentBlock]:
    """
    Tell whether a content block is a document block.

    Args:
        block (ContentBlock): The content block to check.

    Returns:
        TypeGuard[DocumentBlock]: Whether the block carries a document.
    """
    return "document" in block


def _unique_document_name(name: str, taken: set[str]) -> str:
    """
    Find a document name that is free, counting up from the given one.

    Args:
        name (str): The document's own name.
        taken (set[str]): The document names used so far.

    Returns:
        str: The name itself when free, else it with the first free ` (n)`
            suffix — the stem trimmed if the suffix would overrun the length
            Converse accepts.
    """
    if name not in taken:
        return name
    counter = 2
    while True:
        suffix = f" ({counter})"
        stem = name[: DOCUMENT_NAME_MAX_LENGTH - len(suffix)].strip()
        candidate = f"{stem}{suffix}"
        if candidate not in taken:
            return candidate
        counter += 1


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
    display_names: Mapping[str, str],
) -> Message | None:
    text = build_reply_text(reply, bot_user_id=bot_user_id, display_names=display_names)
    if _is_welt_reply(reply, bot_user_id):
        return {"role": "assistant", "content": [{"text": text}]} if text else None
    document_blocks, media_blocks = _file_blocks_of(reply, file_blocks_by_id)
    text_block: TextBlock = {"text": text}
    return {
        "role": "user",
        "content": [*document_blocks, text_block, *media_blocks],
    }


def build_reply_text(
    reply: dict,
    *,
    bot_user_id: str | None,
    display_names: Mapping[str, str] | None = None,
) -> str:
    """
    Say what a reply contributes to the conversation, as Markdown.

    The single reading of a reply's words, so what `payload_logic` counts
    towards the budget is what `build_messages` goes on to send.

    Args:
        reply (dict): A Slack reply.
        bot_user_id (str | None): The bot's own user ID.
        display_names (Mapping[str, str] | None): Names by Slack ID.
            Whoever is not in it is named by their ID.

    Returns:
        str: Welt's own reply as its blocks read, everyone else's as they
            typed it behind their name. Empty only for a reply of Welt's
            that says nothing — a `user` message always carries its speaker.
    """
    return _named(_said(reply, bot_user_id), display_names or {})


def _said(reply: dict, bot_user_id: str | None) -> str:
    """
    Say what a reply contributes, with its mentions still in Slack's form.

    What `build_reply_text` reads before it names anyone, and what
    `collect_user_ids` reads to know who there is to name.

    Args:
        reply (dict): A Slack reply.
        bot_user_id (str | None): The bot's own user ID.

    Returns:
        str: The reply's words, behind its speaker's mention where it has
            one.
    """
    spoken = _with_file_names(reply, _with_attachments(reply, _spoken_markdown(reply)))
    if _is_welt_reply(reply, bot_user_id):
        return spoken
    return build_slack_user_prefixed_text(reply, spoken)


def _with_attachments(reply: dict, spoken: str) -> str:
    """
    Read what hangs off a message, under what the message says.

    An app puts what it has to say there — a workflow notification carries
    no blocks and no text at all — and under what a person wrote, Slack
    puts its own unfurling of any link they pasted. Both are read: both are
    what the thread shows under the message.

    Args:
        reply (dict): A Slack reply.
        spoken (str): What the reply's blocks or text say.

    Returns:
        str: The reply's words and its attachments'.
    """
    attached = attachments_to_markdown(reply.get("attachments"))
    return "\n\n".join(part for part in (spoken, attached) if part)


def _spoken_markdown(reply: dict) -> str:
    """
    Read what a reply says as Markdown.

    Blocks come first, and `text` stands in where they carry nothing —
    a message posted as plain text has no blocks at all, and one whose
    blocks are all of a shape this reads nothing from would otherwise lose
    what its sender wrote in `text` beside them.

    Args:
        reply (dict): A Slack reply.

    Returns:
        str: What the reply says, empty when it says nothing.
    """
    blocks = reply.get("blocks")
    if isinstance(blocks, list) and blocks:
        spoken = blocks_to_markdown(blocks).strip()
        if spoken:
            return spoken
    return _cleaned_text(reply).strip()


def _with_file_names(reply: dict, spoken: str) -> str:
    """
    Name the files a reply shows, under what it said.

    A thread shows a file by name whether or not its bytes are anywhere
    the model can read them, so the name travels either way: an image a
    person sent reaches the model as the picture and nothing else, and a
    file nobody downloaded — one an app uploaded, one of a kind Welt does
    not take, one too large for the payload — would otherwise not reach it
    at all.

    Args:
        reply (dict): A Slack reply.
        spoken (str): What the reply says.

    Returns:
        str: The reply's words and the files it shows.
    """
    shown = "\n".join(_file_lines(reply))
    return "\n\n".join(part for part in (spoken, shown) if part)


def _file_lines(reply: dict) -> list[str]:
    """
    Name the files a reply shows, in the order it shows them.

    A file is shown under its title, which is its file name unless someone
    gave it one of its own; where the two differ, the thread shows the
    title and the name is what the file downloads as, so both travel.

    Args:
        reply (dict): A Slack reply.

    Returns:
        list[str]: One line per file; a file Slack names in neither field
            is left out.
    """
    files = reply.get("files")
    if not isinstance(files, list):
        return []
    lines = []
    for file in files:
        if not isinstance(file, dict):
            continue
        name = file.get("name")
        title = file.get("title")
        name = name if isinstance(name, str) and name else ""
        title = title if isinstance(title, str) and title else ""
        if title and name and title != name:
            lines.append(f"[file: {title} ({name})]")
        elif title or name:
            lines.append(f"[file: {title or name}]")
    return lines


def _cleaned_text(reply: dict) -> str:
    return mrkdwn_to_markdown(_text_of(reply))


def _is_welt_reply(reply: dict, bot_user_id: str | None) -> bool:
    """Tell whether a reply is one of Welt's own."""
    return bot_user_id is not None and reply.get("user") == bot_user_id


def _is_assistant_reply(reply: dict, bot_user_id: str | None) -> bool:
    """Tell whether a reply is one of Welt's own, with something in it."""
    return _is_welt_reply(reply, bot_user_id) and bool(
        build_reply_text(reply, bot_user_id=bot_user_id)
    )


def keep_replies_after_last_bot_reply(
    replies: list[dict], *, bot_user_id: str | None
) -> list[dict]:
    """
    Keep the replies that follow Welt's own last reply.

    The reply-level twin of `keep_messages_after_last_assistant`, deciding
    the same cut before the messages exist — and before the files they
    carry have been downloaded. It reads a reply as one of Welt's through
    the same test `build_messages` applies, so the two agree by
    construction rather than by resemblance.

    Args:
        replies (list[dict]): Slack replies in chronological order.
        bot_user_id (str | None): The bot's own user ID.

    Returns:
        list[dict]: The replies after the last of Welt's own, or all of
            them when it has not replied in this thread yet.
    """
    kept = _drop_surrounding_bot_replies(replies, bot_user_id)
    for index in range(len(kept) - 1, -1, -1):
        if _is_assistant_reply(kept[index], bot_user_id):
            return kept[index + 1 :]
    return kept


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
    parentheses, and square brackets in a document name, up to 200 characters.

    Args:
        name (str | None): The original file name.

    Returns:
        str: A non-empty sanitized name.
    """
    sanitized = re.sub(r"[^0-9A-Za-z\-()\[\] ]", "-", name or "")
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized[:DOCUMENT_NAME_MAX_LENGTH].strip()
    return sanitized or "document"


def collect_user_ids(replies: list[dict], *, bot_user_id: str | None) -> set[str]:
    """
    Find the people and apps a thread mentions.

    Read from what each reply will say, so that whoever ends up in the
    payload is looked up and nobody else is.

    Args:
        replies (list[dict]): Slack replies in chronological order.
        bot_user_id (str | None): The bot's own user ID.

    Returns:
        set[str]: The Slack IDs mentioned anywhere in them — every reply's
            sender among them, since a reply is prefixed with its own.
    """
    found: set[str] = set()
    for reply in replies:
        found.update(_MENTION.findall(_said(reply, bot_user_id)))
    return found


def _named(said: str, display_names: Mapping[str, str]) -> str:
    """
    Say who a reply names, in words rather than in Slack's mentions.

    A Slack ID means nothing to a model, and a thread shows nobody by one:
    it shows the names, and so should the conversation the model is given.
    An ID that resolves to no name is still written as a name — the ID
    itself — because of the second thing this does.

    It takes the mention syntax off: `<@U0123>` copied out of the history
    into a reply posts as a real mention and notifies somebody, where
    `@iwamot` is only ever text.

    Args:
        said (str): What a reply says.
        display_names (Mapping[str, str]): Names by Slack ID.

    Returns:
        str: The same, everyone in it named.
    """
    return _MENTION.sub(lambda match: f"@{display_names.get(match[1], match[1])}", said)
