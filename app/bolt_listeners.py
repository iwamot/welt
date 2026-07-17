"""Responds to new Slack posts by streaming the agent's reply.

This is the I/O shell: it filters incoming posts, fetches conversation
history, invokes the AgentCore agent, and renders the streamed reply through
the chat streaming API (chat.startStream / appendStream / stopStream via
`RotatingChatStream`, which rolls the reply over to a follow-up message when
Slack's per-message length limit is hit). One Slack thread is one
conversation and one agent session, in channels and DMs alike. A run that
stops on interrupts gets a button message in the thread, and the presses
come back here as block_actions, resuming the agent once every question is
answered. All classification and formatting is delegated to the pure
`*_logic` modules.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from slack_bolt.context.base_context import BaseContext
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.agent_logic import build_runtime_session_id, build_runtime_user_id
from app.agent_service import stream_agent_events, stream_agent_resume_events
from app.bolt_logic import (
    MAX_THREAD_REPLIES,
    determine_thread_ts_to_reply,
    extract_user_id_from_context,
    has_read_files_scope,
    is_post_from_bot,
    is_post_in_dm,
    is_post_mentioned,
    keep_newest_replies,
)
from app.converse_logic import ContentBlock, build_messages
from app.env import Env
from app.interrupt_logic import (
    append_context_notice,
    build_collection_metadata,
    build_interrupt_blocks,
    build_interrupt_responses,
    initial_collection_state,
    is_fully_answered,
    parse_action_answer,
    parse_collection_state,
    pick_display_name,
    record_answer,
    replace_answered_blocks,
)
from app.message_logic import build_tool_use_task_chunk
from app.slack_file_logic import (
    MAX_BYTES_BY_MODALITY,
    MAX_SLOTS_BY_MODALITY,
    Modality,
    select_files_to_fetch,
)
from app.slack_file_service import fetch_file_blocks
from app.slack_reaction_service import WaitingReaction
from app.slack_stream_service import RotatingChatStream
from app.stream_logic import (
    FileOutput,
    Interrupt,
    RenderEvent,
    StreamError,
    ToolResult,
    ToolUse,
)

logger = logging.getLogger(__name__)

# Fixed texts of Welt's own messages — deliberately not configurable, so
# the frame around the conversation reads the same on every deployment.
REPLY_FAILURE_TEXT = ":warning: Failed to reply. Please check the app logs."
RESUME_FAILURE_TEXT = (
    ":warning: Could not resume the agent. The approval may have "
    "expired or already been answered — ask again if needed."
)
# The plain-text summary of the button message (notifications, accessibility).
INTERRUPT_PROMPT_TEXT = "The agent needs your decision to continue."


async def respond_to_new_post(
    *,
    env: Env,
    context: BaseContext,
    payload: dict,
    client: AsyncWebClient,
) -> None:
    """
    Respond to a new Slack post.

    Filters irrelevant posts, builds the conversation history, and streams
    the agent's reply into the thread. Takes `BaseContext` — the data shared
    by the sync and async Bolt contexts — so both entry points (Socket Mode
    and Lambda) can call it.

    Args:
        env (Env): The validated configuration.
        context (BaseContext): The Bolt context object.
        payload (dict): The payload of the incoming Slack post.
        client (AsyncWebClient): The Slack Web API client.

    Returns:
        None
    """
    if context.channel_id is None:
        raise ValueError("context.channel_id cannot be None")
    user_id = extract_user_id_from_context(context)
    if user_id is None:
        raise ValueError("User ID could not be determined from context")

    if is_post_from_bot(payload):
        return

    reply_thread_ts = determine_thread_ts_to_reply(payload)
    streamer = None
    waiting = None
    try:
        if not (
            is_post_mentioned(context.bot_user_id, payload)
            or is_post_in_dm(payload)
            or await has_parent_post_mentioned(context, payload, client)
        ):
            return
        waiting = WaitingReaction(
            client, channel_id=context.channel_id, message_ts=payload["ts"]
        )
        await waiting.add()
        replies = await get_replies(
            client=client,
            payload=payload,
            channel_id=context.channel_id,
            user_id=user_id,
        )
        file_blocks = await fetch_file_blocks_for_replies(
            context, replies, allowed_modalities=env.file_input_modalities
        )
        messages = build_messages(
            replies,
            bot_user_id=context.bot_user_id,
            file_blocks_by_id=file_blocks,
        )
        events = stream_agent_events(
            agent_arn=env.agent_arn,
            messages=messages,
            agent_manages_history=env.agent_manages_history,
            session_id=build_runtime_session_id(
                team_id=context.team_id,
                channel_id=context.channel_id,
                thread_ts=reply_thread_ts,
            ),
            user_id=build_runtime_user_id(team_id=context.team_id, user_id=user_id),
        )
        streamer = RotatingChatStream(
            client,
            channel=context.channel_id,
            thread_ts=reply_thread_ts,
            recipient_team_id=context.team_id,
            recipient_user_id=user_id,
            buffer_size=env.slack_stream_buffer_size,
        )
        await streamer.start()
        await stream_reply_with_interrupt_prompt(
            client=client,
            channel_id=context.channel_id,
            thread_ts=reply_thread_ts,
            streamer=streamer,
            events=events,
        )
    except Exception as e:
        await report_reply_failure(
            client=client,
            channel_id=context.channel_id,
            thread_ts=reply_thread_ts,
            error=e,
            streamer=streamer,
        )
    finally:
        if waiting is not None:
            await waiting.clear()


# Parent-mention decisions, keyed by (channel, thread_ts): without this,
# every reply in every thread the bot can see would cost a
# conversations_history call. A parent edited after the first check can leave
# a stale entry; accepted, since edits to thread parents are rare and the
# cache only spans this process. Oldest entries fall out first.
_PARENT_MENTION_CACHE_MAX_SIZE = 1000
_parent_mention_cache: dict[tuple[str, str], bool] = {}


async def has_parent_post_mentioned(
    context: BaseContext,
    payload: dict,
    client: AsyncWebClient,
) -> bool:
    """
    Check whether the parent post of the thread mentions the bot.

    Args:
        context (BaseContext): The Bolt context object.
        payload (dict): The payload of the incoming Slack post.
        client (AsyncWebClient): The Slack Web API client.

    Returns:
        bool: True if the parent post mentions the bot, False otherwise.
    """
    thread_ts = payload.get("thread_ts")
    if context.channel_id is None or not isinstance(thread_ts, str):
        return False
    key = (context.channel_id, thread_ts)
    cached = _parent_mention_cache.get(key)
    if cached is not None:
        return cached
    parent_post = await find_parent_post(
        client=client,
        channel_id=context.channel_id,
        thread_ts=thread_ts,
    )
    mentioned = is_post_mentioned(context.bot_user_id, parent_post)
    if len(_parent_mention_cache) >= _PARENT_MENTION_CACHE_MAX_SIZE:
        del _parent_mention_cache[next(iter(_parent_mention_cache))]
    _parent_mention_cache[key] = mentioned
    return mentioned


async def find_parent_post(
    *,
    client: AsyncWebClient,
    channel_id: str,
    thread_ts: str,
) -> dict | None:
    """
    Find the parent post of a thread in Slack.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel with the thread.
        thread_ts (str): The timestamp of the thread.

    Returns:
        dict | None: The parent post if found, None otherwise.
    """
    response = await client.conversations_history(
        channel=channel_id,
        latest=thread_ts,
        limit=1,
        inclusive=True,
    )
    posts: list[dict] = response.get("messages", [])
    return posts[0] if posts else None


async def get_replies(
    *,
    client: AsyncWebClient,
    payload: dict,
    channel_id: str,
    user_id: str,
) -> list[dict]:
    """
    Retrieve the replies to use as conversation history for the incoming post.

    One thread is one conversation: a post inside a thread brings the whole
    thread as history, and a post outside a thread (channel mention or a new
    DM message) starts a new conversation from that post alone.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        payload (dict): The payload of the incoming Slack post.
        channel_id (str): The ID of the channel where the post was made.
        user_id (str): The ID of the user who made the post.

    Returns:
        list[dict]: A list of replies based on the post context.
    """
    thread_ts = payload.get("thread_ts")
    if thread_ts is not None:
        return await get_thread_replies(client, channel_id, thread_ts)
    return [
        {
            "text": payload["text"],
            "user": user_id,
            "bot_id": payload.get("bot_id"),
            "files": payload.get("files"),
        }
    ]


async def get_thread_replies(
    client: AsyncWebClient, channel_id: str, thread_ts: str
) -> list[dict]:
    """
    Retrieve the replies in a Slack thread, keeping the newest of long ones.

    Follows cursor pagination so a thread longer than one page is read to its
    end, then keeps the newest MAX_THREAD_REPLIES replies — the latest posts
    must reach the agent, at the cost of the oldest context.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel containing the thread.
        thread_ts (str): The timestamp of the parent post.

    Returns:
        list[dict]: The newest replies in the thread, in chronological order.
    """
    replies: list[dict] = []
    cursor: str | None = None
    while True:
        response = await client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=1000,
            cursor=cursor,
        )
        replies.extend(response.get("messages", []))
        metadata = response.get("response_metadata")
        cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
        if not cursor:
            break
    return keep_newest_replies(replies, max_count=MAX_THREAD_REPLIES)


async def fetch_file_blocks_for_replies(
    context: BaseContext,
    replies: list[dict],
    *,
    allowed_modalities: tuple[Modality, ...],
) -> dict[str, ContentBlock]:
    """
    Download the files the replies carry, if file input is enabled.

    Args:
        context (BaseContext): The Bolt context object.
        replies (list[dict]): Slack replies in chronological order.
        allowed_modalities (tuple[Modality, ...]): The modalities to accept
            (`Env.file_input_modalities`); empty disables file input.

    Returns:
        dict[str, ContentBlock]: Content blocks keyed by Slack file ID.
    """
    if not allowed_modalities:
        return {}
    if not has_read_files_scope(context.authorize_result):
        return {}
    selections = select_files_to_fetch(
        replies,
        bot_user_id=context.bot_user_id,
        allowed_modalities=allowed_modalities,
        max_slots_by_modality=MAX_SLOTS_BY_MODALITY,
        max_bytes_by_modality=MAX_BYTES_BY_MODALITY,
    )
    if not selections:
        return {}
    if context.bot_token is None:
        raise ValueError("context.bot_token cannot be None")
    return await fetch_file_blocks(selections, bot_token=context.bot_token)


async def stream_reply_with_interrupt_prompt(
    *,
    client: AsyncWebClient,
    channel_id: str,
    thread_ts: str,
    streamer: RotatingChatStream,
    events: AsyncIterator[RenderEvent],
) -> None:
    """
    Render the reply stream, then prompt for its interrupts, if any.

    A run that stopped for human input ends its stream with interrupt
    events; after the streamed reply is finalized, they become one
    button-carrying message in the thread, its metadata holding the
    collection state the button presses fill in. The interrupt names go to
    the log only — the rendering is derived from each reason.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel being replied in.
        thread_ts (str): The thread timestamp being replied to.
        streamer (RotatingChatStream): The stream helper for this reply.
        events (AsyncIterator[RenderEvent]): Parsed agent stream events.

    Returns:
        None
    """
    interrupts = await stream_agent_reply_to_slack(
        client=client,
        channel_id=channel_id,
        thread_ts=thread_ts,
        streamer=streamer,
        events=events,
    )
    if not interrupts:
        return
    logger.info(
        "Prompting for %d interrupt(s): %s",
        len(interrupts),
        [interrupt.name for interrupt in interrupts],
    )
    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=INTERRUPT_PROMPT_TEXT,
        blocks=build_interrupt_blocks(interrupts),
        metadata=build_collection_metadata(initial_collection_state(interrupts)),
    )


async def stream_agent_reply_to_slack(
    *,
    client: AsyncWebClient,
    channel_id: str,
    thread_ts: str,
    streamer: RotatingChatStream,
    events: AsyncIterator[RenderEvent],
) -> list[Interrupt]:
    """
    Render the agent's event stream into the streaming reply.

    Text deltas append markdown (buffered by the SDK helper); tool use is
    shown as task_update chunks, marked complete (or error) when its tool
    result arrives — or when the next text or tool does, for agents that do
    not send tool results. A generated file is uploaded to the thread, where
    it appears as its own message alongside the streamed reply. The message
    is finalized with chat.stopStream. A reply that outgrows Slack's
    per-message limit continues in a follow-up message (see
    `RotatingChatStream`). Interrupt events are collected and handed back —
    posting the button prompt is the caller's move, after the reply is
    finalized.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel being replied in.
        thread_ts (str): The thread timestamp being replied to.
        streamer (RotatingChatStream): The stream helper for this reply.
        events (AsyncIterator[RenderEvent]): Parsed agent stream events.

    Returns:
        list[Interrupt]: The interrupts the run stopped on, stream order.
    """
    active_tool: ToolUse | None = None
    interrupts: list[Interrupt] = []
    async for event in events:
        if isinstance(event, StreamError):
            raise RuntimeError(f"The agent reported an error: {event.message}")
        if isinstance(event, Interrupt):
            interrupts.append(event)
            continue
        if isinstance(event, FileOutput):
            await client.files_upload_v2(
                channel=channel_id,
                thread_ts=thread_ts,
                filename=event.name,
                file=event.data,
            )
            continue
        if isinstance(event, ToolUse):
            if active_tool is not None and event.tool_use_id == active_tool.tool_use_id:
                continue
            chunks = _tool_chunks(completed=active_tool, started=event)
            active_tool = event
            await streamer.append(chunks=chunks)
            continue
        if isinstance(event, ToolResult):
            if active_tool is not None and event.tool_use_id == active_tool.tool_use_id:
                await streamer.append(
                    chunks=_tool_chunks(completed=active_tool, error=event.error)
                )
                active_tool = None
            continue
        if active_tool is not None:
            await streamer.append(chunks=_tool_chunks(completed=active_tool))
            active_tool = None
        await streamer.append(markdown_text=event.text)
    await streamer.stop(
        chunks=_tool_chunks(completed=active_tool) if active_tool else None
    )
    return interrupts


def _tool_chunks(
    *,
    completed: ToolUse | None = None,
    started: ToolUse | None = None,
    error: bool = False,
) -> list[dict]:
    chunks: list[dict] = []
    if completed is not None:
        chunks.append(
            build_tool_use_task_chunk(
                tool_use_id=completed.tool_use_id,
                tool_name=completed.name,
                status="error" if error else "complete",
            )
        )
    if started is not None:
        chunks.append(
            build_tool_use_task_chunk(
                tool_use_id=started.tool_use_id,
                tool_name=started.name,
                status="in_progress",
            )
        )
    return chunks


async def respond_to_interrupt_action(
    *,
    env: Env,
    context: BaseContext,
    body: dict,
    payload: dict,
    client: AsyncWebClient,
) -> None:
    """
    Respond to a press of an interrupt button.

    Records the answer into the button message's metadata and replaces the
    pressed button row with a visible receipt (which doubles as the guard
    against double presses). Once every interrupt of the stop is answered,
    the collected answers resume the agent on the same session and the
    continued reply streams into the thread as usual. Anyone who can see
    the thread may press — the trust boundary is channel membership; the
    presser is recorded in the metadata and shown next to the receipt.
    Expiry is optimistic: the resume is always attempted, and one that
    fails outright (the agent's session is gone, or AGENT_ARN moved
    elsewhere) puts a notice under the buttons; a failure after the reply
    started streaming takes the usual reply-failure route.

    Near-simultaneous presses on different rows can lose one metadata
    update; accepted for now — pressing the lost row again recovers. So
    is a duplicate answer to one question (a double press, or a double
    Enter in the text field): both handlers see the pre-answer message,
    so the duplicate's resume loses against the consumed interrupt and
    puts the resume-failure notice under the questions — misleading only
    until the first resume's reply streams in. Deduplicating would take
    state outside the message (Welt keeps none), so the notice's wording
    covers it instead.

    Args:
        env (Env): The validated configuration.
        context (BaseContext): The Bolt context object.
        body (dict): The full block_actions payload (for the message).
        payload (dict): The pressed action from the block_actions payload.
        client (AsyncWebClient): The Slack Web API client.

    Returns:
        None
    """
    if context.channel_id is None:
        raise ValueError("context.channel_id cannot be None")
    user_id = extract_user_id_from_context(context)
    if user_id is None:
        raise ValueError("User ID could not be determined from context")

    message = body.get("message")
    if not isinstance(message, dict):
        logger.warning("Ignoring a button press that carried no message")
        return
    message_ts = message.get("ts")
    thread_ts = message.get("thread_ts")
    if not isinstance(message_ts, str) or not isinstance(thread_ts, str):
        logger.warning("Ignoring a button press without message timestamps")
        return

    streamer = None
    waiting = None
    try:
        action_id = payload.get("action_id")
        pressed = parse_action_answer(payload)
        if not isinstance(action_id, str) or pressed is None:
            logger.warning("Ignoring a button press with an unreadable action")
            return
        interrupt_id, choice = pressed
        original_blocks = message.get("blocks")
        if not isinstance(original_blocks, list):
            logger.warning("Ignoring a button press whose message has no blocks")
            return
        # Marking the button message right away is the fastest visible
        # acknowledgment of the press, which softens the double-press
        # window the docstring describes.
        waiting = WaitingReaction(
            client, channel_id=context.channel_id, message_ts=message_ts
        )
        await waiting.add()

        state = parse_collection_state(message)
        if state is None:
            # Some surfaces omit metadata from the block_actions payload;
            # re-fetch the message with metadata included.
            state = parse_collection_state(
                await fetch_button_message(
                    client=client,
                    channel_id=context.channel_id,
                    message_ts=message_ts,
                )
            )
        if state is None:
            logger.warning("Ignoring a button press without collection metadata")
            return
        updated = record_answer(
            state, interrupt_id=interrupt_id, value=choice, user_id=user_id
        )
        if updated is None:
            logger.warning("Ignoring a button press for an unknown interrupt")
            return

        presser_name = await fetch_display_name(client=client, user_id=user_id)
        replaced_blocks = replace_answered_blocks(
            original_blocks,
            action_id=action_id,
            presser_name=presser_name,
            answer=choice,
        )
        await client.chat_update(
            channel=context.channel_id,
            ts=message_ts,
            text=INTERRUPT_PROMPT_TEXT,
            blocks=replaced_blocks if replaced_blocks is not None else original_blocks,
            metadata=build_collection_metadata(updated),
        )

        if not is_fully_answered(updated):
            return
        events = stream_agent_resume_events(
            agent_arn=env.agent_arn,
            interrupt_responses=build_interrupt_responses(updated),
            session_id=build_runtime_session_id(
                team_id=context.team_id,
                channel_id=context.channel_id,
                thread_ts=thread_ts,
            ),
            user_id=build_runtime_user_id(team_id=context.team_id, user_id=user_id),
        )
        # Peek at the first event before opening a streaming reply: a resume
        # that cannot happen at all (the agent's session is gone, AGENT_ARN
        # moved elsewhere) fails right here, and gets a notice under the
        # buttons instead of an empty reply bubble. A failure after the
        # reply started streaming takes the usual reply-failure route below.
        first: RenderEvent | None = None
        try:
            first = await anext(aiter(events), None)
        except Exception:
            logger.error("Failed to resume the agent", exc_info=True)
        if first is None or isinstance(first, StreamError):
            if isinstance(first, StreamError):
                logger.error("The agent reported an error on resume: %s", first.message)
            await client.chat_update(
                channel=context.channel_id,
                ts=message_ts,
                text=INTERRUPT_PROMPT_TEXT,
                blocks=append_context_notice(
                    replaced_blocks if replaced_blocks is not None else original_blocks,
                    RESUME_FAILURE_TEXT,
                ),
                metadata=build_collection_metadata(updated),
            )
            return
        streamer = RotatingChatStream(
            client,
            channel=context.channel_id,
            thread_ts=thread_ts,
            recipient_team_id=context.team_id,
            recipient_user_id=user_id,
            buffer_size=env.slack_stream_buffer_size,
        )
        await streamer.start()
        await stream_reply_with_interrupt_prompt(
            client=client,
            channel_id=context.channel_id,
            thread_ts=thread_ts,
            streamer=streamer,
            events=_with_first(first, events),
        )
    except Exception as e:
        await report_reply_failure(
            client=client,
            channel_id=context.channel_id,
            thread_ts=thread_ts,
            error=e,
            streamer=streamer,
        )
    finally:
        if waiting is not None:
            await waiting.clear()


async def _with_first(
    first: RenderEvent, rest: AsyncIterator[RenderEvent]
) -> AsyncIterator[RenderEvent]:
    # Reattach the peeked-at first event in front of the remaining stream.
    yield first
    async for event in rest:
        yield event


async def fetch_button_message(
    *,
    client: AsyncWebClient,
    channel_id: str,
    message_ts: str,
) -> dict | None:
    """
    Fetch a button message with its metadata included.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel holding the message.
        message_ts (str): The timestamp of the button message.

    Returns:
        dict | None: The message, or None if it could not be found.
    """
    response = await client.conversations_replies(
        channel=channel_id,
        ts=message_ts,
        latest=message_ts,
        inclusive=True,
        limit=1,
        include_all_metadata=True,
    )
    messages: list = response.get("messages", [])
    for message in messages:
        if isinstance(message, dict) and message.get("ts") == message_ts:
            return message
    return None


async def fetch_display_name(*, client: AsyncWebClient, user_id: str) -> str:
    """
    Fetch a user's display name for the pressed-button receipt.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        user_id (str): The presser's Slack user ID.

    Returns:
        str: The display name, falling back to the raw user ID when the
            profile is unreadable (for example, an install that predates
            the users:read scope).
    """
    try:
        response = await client.users_info(user=user_id)
    except SlackApiError:
        logger.warning("Could not fetch the presser's profile", exc_info=True)
        return user_id
    return pick_display_name(response.get("user")) or user_id


async def report_reply_failure(
    *,
    client: AsyncWebClient,
    channel_id: str,
    thread_ts: str,
    error: Exception,
    streamer: RotatingChatStream | None,
) -> None:
    """
    Report a failed reply: full details to the log, a generic note to Slack.

    The error text can carry internals (ARNs, AWS error details), so it
    stays in the log; the channel gets a generic pointer. If the streaming
    reply is already visible, the note finalizes that message so no empty
    half-open reply is left behind; otherwise it is posted as a new reply.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel where the post was made.
        thread_ts (str): The thread timestamp to reply to.
        error (Exception): The failure to log.
        streamer (RotatingChatStream | None): The stream helper, if created.

    Returns:
        None
    """
    logger.error(
        "Failed to reply (channel: %s, thread: %s)",
        channel_id,
        thread_ts,
        exc_info=error,
    )
    if streamer is not None and streamer.ts is not None:
        try:
            await streamer.stop(markdown_text=REPLY_FAILURE_TEXT)
            return
        except Exception:
            logger.debug("Failed to stop the stream", exc_info=True)
    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=REPLY_FAILURE_TEXT,
    )
