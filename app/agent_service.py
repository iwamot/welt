"""I/O for invoking the agent and streaming its reply.

Three invoke paths share one render-event surface, picked by the configured
ARN: an AgentCore Runtime agent goes through `invoke_agent_runtime` (JSON
payload in, SSE of Strands events out), a managed harness through
`invoke_harness` (typed Converse-shaped messages in, a typed event stream
out), and no ARN at all — local mode, for development — through plain HTTP
to the same `/invocations` + SSE surface the AgentCore SDK serves locally.
The Runtime and local paths carry two payload envelopes — `messages` for a
conversation turn, `interrupt_responses` to resume an interrupted run.
boto3 and http.client are synchronous, so the blocking invoke call and each
blocking read of the response are pushed to a worker thread to keep the
event loop free.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
from collections.abc import AsyncIterator, Iterator

import boto3
from botocore.config import Config

from app.agent_logic import is_harness_arn
from app.converse_logic import Message, keep_messages_after_last_assistant
from app.stream_logic import (
    RenderEvent,
    harness_final_stop_error,
    parse_harness_event,
    parse_sse_data_line,
    parse_stream_event,
)

logger = logging.getLogger(__name__)

_client = None

# Welt puts no ceiling on how long a reply may take — that is between the
# agent and AgentCore Runtime's own limits. Hang detection is the connection
# layer's job: the read timeout bounds the silence between stream chunks, so
# a stalled connection dies while a healthy long-running stream keeps going.
_CLIENT_CONFIG = Config(read_timeout=60)

# Where local mode finds the agent: the AgentCore SDK's local server, which
# serves the same /invocations + SSE surface as the Runtime, on its default
# port. http.client (rather than urllib) keeps a developer's HTTP_PROXY out
# of the way — these requests must reach localhost directly.
_LOCAL_AGENT_HOST = "localhost"
_LOCAL_AGENT_PORT = 8080
LOCAL_AGENT_URL = f"http://{_LOCAL_AGENT_HOST}:{_LOCAL_AGENT_PORT}"

# The socket timeout plays the same role as the boto3 read timeout above:
# it bounds each blocking read, not the whole reply.
_LOCAL_TIMEOUT = 60


def init_client(*, region_name: str) -> None:
    """
    Create the shared bedrock-agentcore client.

    Called once at startup, before any listener runs: a misconfigured
    credential chain then fails at boot instead of on the first message,
    and client creation (not thread-safe through boto3's default session)
    never races between worker threads.

    Args:
        region_name (str): The agent's region (`Env.agent_region`, taken
            from AGENT_ARN), so the client targets the agent regardless of
            any ambient AWS region configuration.

    Returns:
        None
    """
    global _client
    _client = boto3.client(
        "bedrock-agentcore", region_name=region_name, config=_CLIENT_CONFIG
    )


def _get_client():
    if _client is None:
        raise RuntimeError("agent_service.init_client() must be called at startup")
    return _client


def log_local_mode() -> None:
    """
    Announce local mode, once at startup.

    Local mode's counterpart to `init_client`: an unset AGENT_ARN falls
    into local mode whether or not that was meant, so this log line names
    the target URL — the trace to look for when AGENT_ARN was merely
    forgotten. No probe beyond that: the agent may start, stop, or be
    swapped while Welt runs, and an unreachable one surfaces as a failed
    reply.

    Returns:
        None
    """
    logger.info(
        "AGENT_ARN is not set — invoking the local agent at %s", LOCAL_AGENT_URL
    )


def stream_agent_events(
    *,
    agent_arn: str | None,
    messages: list[Message],
    session_id: str,
    user_id: str,
    agent_manages_history: bool,
) -> AsyncIterator[RenderEvent]:
    """
    Invoke the agent and stream render events parsed from its reply.

    Args:
        agent_arn (str | None): The ARN of the AgentCore Runtime agent or
            managed harness to invoke, or None for the local agent.
        messages (list[Message]): The conversation, Converse-shaped.
        session_id (str): The runtimeSessionId (Slack thread/DM key).
        user_id (str): The runtimeUserId (verified Slack identity).
        agent_manages_history (bool): Whether the agent keeps the
            conversation history itself (`Env.agent_manages_history`). If
            so, only the messages it has not seen yet are sent — re-sending
            the whole thread would duplicate its stored history.

    Returns:
        AsyncIterator[RenderEvent]: Text deltas, tool-use indicators, and
            stream errors.
    """
    if agent_manages_history:
        messages = keep_messages_after_last_assistant(messages)
    if agent_arn is None:
        return _stream_local_events(
            payload={"messages": messages}, session_id=session_id
        )
    if is_harness_arn(agent_arn):
        return _stream_harness_events(
            harness_arn=agent_arn,
            messages=messages,
            session_id=session_id,
            user_id=user_id,
        )
    return _stream_runtime_events(
        agent_arn=agent_arn,
        payload={"messages": messages},
        session_id=session_id,
        user_id=user_id,
    )


def stream_agent_resume_events(
    *,
    agent_arn: str | None,
    interrupt_responses: dict,
    session_id: str,
    user_id: str,
) -> AsyncIterator[RenderEvent]:
    """
    Resume an interrupted run with the collected answers.

    Never the harness path: interrupts only ever come from a runtime or
    local agent, so this is only reached for one (a press on a button left
    over from an earlier runtime target after AGENT_ARN moved to a harness
    simply fails, surfacing through the usual reply-failure route).

    Args:
        agent_arn (str | None): The ARN of the AgentCore Runtime agent to
            resume, or None for the local agent.
        interrupt_responses (dict): The collected answers, one value per
            interrupt id (`interrupt_logic.build_interrupt_responses`).
        session_id (str): The runtimeSessionId the interrupted run used.
        user_id (str): The runtimeUserId (the presser's verified identity).

    Returns:
        AsyncIterator[RenderEvent]: The resumed reply's render events.
    """
    payload = {"interrupt_responses": interrupt_responses}
    if agent_arn is None:
        return _stream_local_events(payload=payload, session_id=session_id)
    return _stream_runtime_events(
        agent_arn=agent_arn,
        payload=payload,
        session_id=session_id,
        user_id=user_id,
    )


async def _stream_runtime_events(
    *,
    agent_arn: str,
    payload: dict,
    session_id: str,
    user_id: str,
) -> AsyncIterator[RenderEvent]:
    payload_bytes = json.dumps(payload).encode("utf-8")

    def invoke() -> dict:
        return _get_client().invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=session_id,
            runtimeUserId=user_id,
            contentType="application/json",
            accept="text/event-stream",
            payload=payload_bytes,
        )

    response = await asyncio.to_thread(invoke)
    lines: Iterator[bytes] = response["response"].iter_lines()
    async for render_event in _render_events_from_sse_lines(lines):
        yield render_event


async def _stream_local_events(
    *, payload: dict, session_id: str
) -> AsyncIterator[RenderEvent]:
    connection = http.client.HTTPConnection(
        _LOCAL_AGENT_HOST, _LOCAL_AGENT_PORT, timeout=_LOCAL_TIMEOUT
    )

    def invoke() -> http.client.HTTPResponse:
        # The runtimeSessionId travels in the header the SDK's local server
        # reads; the runtimeUserId has no local counterpart — the SDK exposes
        # no header for it — so local agents see no caller identity.
        connection.request(
            "POST",
            "/invocations",
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
            },
        )
        return connection.getresponse()

    try:
        response = await asyncio.to_thread(invoke)
        # The response is a file object, so iterating it yields lines.
        async for render_event in _render_events_from_sse_lines(iter(response)):
            yield render_event
    finally:
        connection.close()


async def _render_events_from_sse_lines(
    lines: Iterator[bytes],
) -> AsyncIterator[RenderEvent]:
    async for line in _iterate_in_thread(lines):
        decoded_line = line.decode("utf-8")
        event = parse_sse_data_line(decoded_line)
        if event is None:
            # A non-blank line that parses to nothing usually means the agent
            # yielded something the Runtime SDK could not keep as JSON (it
            # degrades such events to a quoted string), so leave a trace.
            if decoded_line.strip():
                logger.debug("Ignoring unparseable SSE line: %.200s", decoded_line)
            continue
        render_event = parse_stream_event(event)
        if render_event is not None:
            yield render_event


async def _stream_harness_events(
    *,
    harness_arn: str,
    messages: list[Message],
    session_id: str,
    user_id: str,
) -> AsyncIterator[RenderEvent]:
    def invoke() -> dict:
        return _get_client().invoke_harness(
            harnessArn=harness_arn,
            runtimeSessionId=session_id,
            runtimeUserId=user_id,
            messages=messages,
        )

    response = await asyncio.to_thread(invoke)
    events: Iterator[dict] = iter(response["stream"])
    last_stop_reason: object = None
    async for event in _iterate_in_thread(events):
        message_stop = event.get("messageStop")
        if isinstance(message_stop, dict):
            last_stop_reason = message_stop.get("stopReason")
        render_event = parse_harness_event(event)
        if render_event is not None:
            yield render_event
    stop_error = harness_final_stop_error(last_stop_reason)
    if stop_error is not None:
        yield stop_error


async def _iterate_in_thread[T](items: Iterator[T]) -> AsyncIterator[T]:
    # Each blocking read waits in a worker thread; a None sentinel marks the
    # end because StopIteration cannot cross the thread boundary.
    def next_item() -> T | None:
        return next(items, None)

    while True:
        item = await asyncio.to_thread(next_item)
        if item is None:
            break
        yield item
