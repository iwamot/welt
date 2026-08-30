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
from collections.abc import AsyncIterator, Collection

from slack_bolt.context.base_context import BaseContext
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.agent_logic import build_runtime_session_id, build_runtime_user_id
from app.agent_service import (
    AgentSilenceTimeout,
    stream_agent_events,
    stream_agent_resume_events,
)
from app.bolt_logic import (
    MAX_THREAD_REPLIES,
    determine_thread_ts_to_reply,
    extract_user_id_from_context,
    has_read_files_scope,
    is_post_from_bot,
    is_post_in_dm,
    is_post_mentioned,
)
from app.converse_logic import (
    ContentBlock,
    build_messages,
    collect_user_ids,
    keep_replies_after_last_bot_reply,
)
from app.env import Env
from app.interrupt_logic import (
    append_context_notice,
    build_collection_metadata,
    build_fallback_text,
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
from app.payload_logic import MAX_PAYLOAD_BYTES, compact_to_payload_budget
from app.slack_file_logic import (
    MAX_BYTES_BY_MODALITY,
    MAX_SLOTS_BY_MODALITY,
    FileToFetch,
    Modality,
    select_files_to_fetch,
)
from app.slack_file_service import fetch_file_blocks
from app.slack_reaction_service import WaitingReaction
from app.slack_stream_logic import note_after_reply
from app.slack_stream_service import RotatingChatStream
from app.stream_logic import (
    FileOutput,
    Interrupt,
    RenderEvent,
    StreamError,
    ToolResult,
    ToolUse,
    fills_in_tool_name,
)

logger = logging.getLogger(__name__)


class AgentReplyError(Exception):
    """The agent's stream reported an error instead of completing the reply."""


# Fixed texts of Welt's own messages — deliberately not configurable, so
# the frame around the conversation reads the same on every deployment.
REPLY_FAILURE_TEXT = ":warning: Failed to reply. Please check the app logs."
# Appended to a reply that a shutdown cut off. Unlike the failure above,
# this one lands under the half-written answer it interrupted, so it says
# the answer is unfinished as well as why.
SHUTDOWN_TEXT = (
    ":warning: The app is shutting down. This reply is incomplete — please try again."
)
RESUME_FAILURE_TEXT = (
    ":warning: Could not resume the agent. The approval may have "
    "expired or already been answered — ask again if needed."
)
# For the one failure that is not a failure of Welt's or the agent's: a
# reply that sent nothing for long enough to be cut off as a stalled one.
# It reads under a half-written reply, on its own, and under the buttons of
# a resume, so it says nothing about where the reply got to.
AGENT_TIMEOUT_TEXT = (
    ":warning: The agent went quiet for too long, so this reply was ended. "
    "It may just be slow — please ask again."
)
# What stands in for a button message whose blocks read as nothing at all.
# Every message Welt builds says more than this; it is here so that `text`
# is never empty.
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
    # Built here rather than at the invoke below so the log lines in this
    # handler — the failure path included — carry the same value AgentCore
    # Observability keys its traces by.
    session_id = build_runtime_session_id(
        team_id=context.team_id,
        channel_id=context.channel_id,
        thread_ts=reply_thread_ts,
    )
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
        # An agent holding its own history is sent only the replies after
        # Welt's last one. Cutting to them here rather than on the messages
        # further down keeps the files on the earlier replies from being
        # downloaded to be dropped, and leaves the budget below counting
        # what the payload will actually carry.
        if env.agent_manages_history:
            replies = keep_replies_after_last_bot_reply(
                replies, bot_user_id=context.bot_user_id
            )
        selections = select_files_for_replies(
            context, replies, allowed_modalities=env.file_input_modalities
        )
        # Names are read before the thread is trimmed, so what the budget
        # counts is the text the payload will carry.
        display_names = await fetch_display_names(
            client, collect_user_ids(replies, bot_user_id=context.bot_user_id)
        )
        replies, selections = compact_to_payload_budget(
            replies,
            selections,
            bot_user_id=context.bot_user_id,
            display_names=display_names,
            max_payload_bytes=MAX_PAYLOAD_BYTES,
        )
        file_blocks = await fetch_file_blocks_for_selections(context, selections)
        messages = build_messages(
            replies,
            bot_user_id=context.bot_user_id,
            file_blocks_by_id=file_blocks,
            display_names=display_names,
        )
        events = stream_agent_events(
            agent_arn=env.agent_arn,
            agent_qualifier=env.agent_qualifier,
            messages=messages,
            agent_manages_history=env.agent_manages_history,
            session_id=session_id,
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
        await stream_reply_with_interrupt_prompt(
            client=client,
            channel_id=context.channel_id,
            thread_ts=reply_thread_ts,
            streamer=streamer,
            events=events,
            session_id=session_id,
        )
    except AgentSilenceTimeout:
        logger.warning("The agent went quiet (session: %s)", session_id)
        await report_reply_failure(
            client=client,
            channel_id=context.channel_id,
            thread_ts=reply_thread_ts,
            streamer=streamer,
            text=AGENT_TIMEOUT_TEXT,
        )
    except Exception:
        logger.exception("Failed to reply (session: %s)", session_id)
        await report_reply_failure(
            client=client,
            channel_id=context.channel_id,
            thread_ts=reply_thread_ts,
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
    DM message) starts a new conversation from that post alone — carried
    with the blocks and attachments the event brought, so that the post
    reads the same whether it is the conversation or the first line of one.

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
            "blocks": payload.get("blocks"),
            "attachments": payload.get("attachments"),
            "user": user_id,
            "bot_id": payload.get("bot_id"),
            "files": payload.get("files"),
        }
    ]


async def get_thread_replies(
    client: AsyncWebClient, channel_id: str, thread_ts: str
) -> list[dict]:
    """
    Retrieve one page of a Slack thread: its newest replies.

    A page is what `limit` asks for, taken from the newest end — a thread
    longer than that comes back as its parent plus its most recent
    replies, which is what an answer needs. Slack caps the page at 1000,
    and Welt asks for the cap rather than paginating past it: the posts a
    reply is answering are always in the newest page, and a thread that
    long has already lost its early context to the payload budget.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel containing the thread.
        thread_ts (str): The timestamp of the parent post.

    Returns:
        list[dict]: The newest replies in the thread, in chronological order.
    """
    response = await client.conversations_replies(
        channel=channel_id,
        ts=thread_ts,
        limit=MAX_THREAD_REPLIES,
    )
    return response.get("messages", [])


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


# Slack IDs to the names their profiles show, kept for as long as the process
# lives. A name is read once and used for every thread after: a workspace's
# names change rarely, and the reply path is no place to keep asking. A
# thousand of them come to about 130 KiB (measured 2026-08-29).
_display_names: dict[str, str] = {}

# Whether names can be read at all. An installation without users:read says
# so on the first attempt, and is not asked again.
_names_readable = True


async def fetch_display_names(
    client: AsyncWebClient, user_ids: Collection[str]
) -> dict[str, str]:
    """
    Look up what a thread's people and apps are called.

    A thread shows names, not IDs, so the conversation the agent is given
    says the same. Whoever cannot be looked up is left out, and their ID
    travels as it was: a name is worth a call, never a failed reply.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        user_ids (Collection[str]): The Slack IDs the conversation mentions.

    Returns:
        dict[str, str]: Names by Slack ID, for those that have one.
    """
    global _names_readable
    for user_id in [uid for uid in user_ids if uid not in _display_names]:
        if not _names_readable:
            break
        try:
            user = (await client.users_info(user=user_id))["user"]
        except SlackApiError as error:
            # Without users:read there is nothing to come back for; asking
            # again every turn would only spend a round trip per speaker.
            _names_readable = error.response.get("error") != "missing_scope"
            logger.debug("Failed to look up %s", user_id, exc_info=True)
            continue
        except Exception:
            logger.debug("Failed to look up %s", user_id, exc_info=True)
            continue
        name = pick_display_name(user)
        if name is not None:
            _display_names[user_id] = name
    return {uid: _display_names[uid] for uid in user_ids if uid in _display_names}


async def fetch_file_blocks_for_selections(
    context: BaseContext, selections: list[FileToFetch]
) -> dict[str, ContentBlock]:
    """
    Download the selected files.

    Args:
        context (BaseContext): The Bolt context object.
        selections (list[FileToFetch]): The files that survived the trim.

    Returns:
        dict[str, ContentBlock]: Content blocks keyed by Slack file ID.
    """
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
    session_id: str,
) -> None:
    """
    Render the reply stream, then prompt for its interrupts, if any.

    A run that stopped for human input ends its stream with interrupt
    events; after the streamed reply is finalized, they become one
    button-carrying message in the thread, its metadata holding the
    collection state the button presses fill in. The interrupt ids and names
    go to the log only — the rendering is derived from each reason.

    Takes the session ID already built rather than the parts to build it
    from, so the correlation ID has one place it is assembled.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel being replied in.
        thread_ts (str): The thread timestamp being replied to.
        streamer (RotatingChatStream): The stream helper for this reply.
        events (AsyncIterator[RenderEvent]): Parsed agent stream events.
        session_id (str): The AgentCore session ID for this conversation.

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
        "Prompting for %d interrupt(s) (interrupts: %s, session: %s)",
        len(interrupts),
        {interrupt.id: interrupt.name for interrupt in interrupts},
        session_id,
    )
    blocks = build_interrupt_blocks(interrupts)
    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=build_fallback_text(blocks, default=INTERRUPT_PROMPT_TEXT),
        blocks=blocks,
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
    is finalized with chat.stopStream; a run that rendered nothing (one that
    stopped on interrupts alone) leaves no streamed message at all. A reply
    that outgrows Slack's
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
            raise AgentReplyError(f"The agent reported an error: {event.message}")
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
                if fills_in_tool_name(active=active_tool, event=event):
                    active_tool = event
                    await streamer.append(chunks=_tool_chunks(started=event))
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
    # No thread to key a session on, so these two carry the channel and the
    # presser instead: the press was dropped, and the one person who knows
    # what was clicked is the one who clicked it.
    if not isinstance(message, dict):
        logger.warning(
            "Ignoring a button press that carried no message (channel: %s, user: %s)",
            context.channel_id,
            user_id,
        )
        return
    message_ts = message.get("ts")
    thread_ts = message.get("thread_ts")
    if not isinstance(message_ts, str) or not isinstance(thread_ts, str):
        logger.warning(
            "Ignoring a button press without message timestamps "
            "(channel: %s, user: %s)",
            context.channel_id,
            user_id,
        )
        return

    # Built here rather than at the resume invoke below so the log lines in
    # this handler — the ignored presses and the failure path included —
    # carry the same value AgentCore Observability keys its traces by.
    session_id = build_runtime_session_id(
        team_id=context.team_id,
        channel_id=context.channel_id,
        thread_ts=thread_ts,
    )
    streamer = None
    waiting = None
    try:
        action_id = payload.get("action_id")
        if not isinstance(action_id, str):
            logger.warning(
                "Ignoring a button press without an action id (session: %s)",
                session_id,
            )
            return
        pressed = parse_action_answer(payload)
        if pressed is None:
            logger.warning(
                "Ignoring a button press with an unreadable answer (session: %s)",
                session_id,
            )
            return
        interrupt_id, choice, source = pressed
        original_blocks = message.get("blocks")
        if not isinstance(original_blocks, list):
            logger.warning(
                "Ignoring a button press whose message has no blocks (session: %s)",
                session_id,
            )
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
            logger.warning(
                "Ignoring a button press without collection metadata (session: %s)",
                session_id,
            )
            return
        updated = record_answer(
            state,
            interrupt_id=interrupt_id,
            value=choice,
            source=source,
            user_id=user_id,
        )
        if updated is None:
            logger.warning(
                "Ignoring a button press for an unknown interrupt "
                "(interrupt: %s, session: %s)",
                interrupt_id,
                session_id,
            )
            return
        # One line per answer, not one per resume: a single stop can carry
        # several questions, and a line at resume time would keep only the
        # last presser. The answer itself stays out — a free-text `input`
        # carries whatever was typed.
        logger.info(
            "Interrupt answered (interrupt: %s, user: %s, session: %s)",
            interrupt_id,
            user_id,
            session_id,
        )

        presser_name = await fetch_display_name(client=client, user_id=user_id)
        replaced_blocks = replace_answered_blocks(
            original_blocks,
            action_id=action_id,
            presser_name=presser_name,
            answer=choice,
        )
        shown_blocks = (
            replaced_blocks if replaced_blocks is not None else original_blocks
        )
        await client.chat_update(
            channel=context.channel_id,
            ts=message_ts,
            text=build_fallback_text(shown_blocks, default=INTERRUPT_PROMPT_TEXT),
            blocks=shown_blocks,
            metadata=build_collection_metadata(updated),
        )

        if not is_fully_answered(updated):
            return
        events = stream_agent_resume_events(
            agent_arn=env.agent_arn,
            agent_qualifier=env.agent_qualifier,
            interrupt_responses=build_interrupt_responses(updated),
            session_id=session_id,
            user_id=build_runtime_user_id(team_id=context.team_id, user_id=user_id),
        )
        # Peek at the first event before opening a streaming reply: a resume
        # that cannot happen at all (the agent's session is gone, AGENT_ARN
        # moved elsewhere) fails right here, and gets a notice under the
        # buttons instead of an empty reply bubble. A failure after the
        # reply started streaming takes the usual reply-failure route below.
        first: RenderEvent | None = None
        notice_text = RESUME_FAILURE_TEXT
        try:
            first = await anext(aiter(events), None)
        except AgentSilenceTimeout:
            logger.warning("The agent went quiet on resume (session: %s)", session_id)
            notice_text = AGENT_TIMEOUT_TEXT
        except Exception:
            logger.exception("Failed to resume the agent (session: %s)", session_id)
        if first is None or isinstance(first, StreamError):
            if isinstance(first, StreamError):
                logger.error(
                    "The agent reported an error on resume (error: %s, session: %s)",
                    first.message,
                    session_id,
                )
            noticed_blocks = append_context_notice(shown_blocks, notice_text)
            await client.chat_update(
                channel=context.channel_id,
                ts=message_ts,
                text=build_fallback_text(noticed_blocks, default=INTERRUPT_PROMPT_TEXT),
                blocks=noticed_blocks,
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
        await stream_reply_with_interrupt_prompt(
            client=client,
            channel_id=context.channel_id,
            thread_ts=thread_ts,
            streamer=streamer,
            events=_with_first(first, events),
            session_id=session_id,
        )
    except AgentSilenceTimeout:
        logger.warning("The agent went quiet (session: %s)", session_id)
        await report_reply_failure(
            client=client,
            channel_id=context.channel_id,
            thread_ts=thread_ts,
            streamer=streamer,
            text=AGENT_TIMEOUT_TEXT,
        )
    except Exception:
        logger.exception("Failed to reply (session: %s)", session_id)
        await report_reply_failure(
            client=client,
            channel_id=context.channel_id,
            thread_ts=thread_ts,
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
    streamer: RotatingChatStream | None,
    text: str = REPLY_FAILURE_TEXT,
) -> None:
    """
    Report a failed reply with a fixed note to Slack.

    The caller logs the failure itself; the error text can carry internals
    (ARNs, AWS error details), so the channel only gets one of Welt's own
    texts. If the streaming reply is already visible, the note finalizes
    that message so no empty half-open reply is left behind; otherwise it
    is posted as a new reply.

    Args:
        client (AsyncWebClient): The Slack Web API client.
        channel_id (str): The ID of the channel where the post was made.
        thread_ts (str): The thread timestamp to reply to.
        streamer (RotatingChatStream | None): The stream helper, if created.
        text (str): The note to leave, for a failure whose cause is worth
            naming. Defaults to the generic pointer to the app logs.

    Returns:
        None
    """
    if streamer is not None and streamer.ts is not None:
        try:
            await streamer.stop(markdown_text=note_after_reply(text))
            return
        except Exception:
            logger.debug("Failed to stop the stream", exc_info=True)
    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=text,
    )
