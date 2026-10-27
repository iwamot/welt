"""I/O for invoking the agent and streaming its reply.

The invoke paths share one render-event surface. A Runtime ARN uses
`invoke_agent_runtime`, optionally with a Gateway endpoint override; a
harness ARN uses `invoke_harness`. A Gateway URL without an ARN uses signed
HTTP to its target's `/invocations` path. With neither configured, plain
HTTP reaches the AgentCore SDK's local `/invocations` endpoint.
Runtime, Gateway, and local agents receive `messages` for a conversation
turn or `interrupt_responses` to resume an interrupted run.
boto3 and http.client are synchronous, so the blocking invoke call and each
blocking read of the response are pushed to a worker thread to keep the
event loop free. A reply is not always read to its end — an error event, or
a failed Slack call while the reply is being rendered, ends the run there —
so every path closes its response on the way out instead of leaving the
connection to be reclaimed whenever the stream is finalized. A stream that
falls silent past the read timeout ends as `AgentSilenceTimeout`, which
tells a slow agent apart from a broken one.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
from collections.abc import AsyncIterator, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError

from app.agent_logic import build_qualifier_kwargs, is_harness_arn
from app.converse_logic import Message, keep_messages_after_last_assistant
from app.gateway_service import GatewayClient
from app.stream_logic import (
    RenderEvent,
    harness_final_stop_error,
    parse_harness_event,
    parse_sse_data_line,
    parse_stream_event,
)

logger = logging.getLogger(__name__)


class AgentSilenceTimeout(Exception):
    """The agent sent nothing for long enough that the read timeout fired."""


_client = None
_gateway_client: GatewayClient | None = None

# Welt puts no ceiling on how long a reply may take — that is between the
# agent and AgentCore Runtime's own limits. Hang detection is the connection
# layer's job: the read timeout bounds the silence between stream chunks, so
# a stalled connection dies while a healthy long-running stream keeps going.
# The standard retry mode retries what the service model marks retryable —
# notably the 409 RetryableConflictException a session mid-provision or
# mid-teardown answers with — where the default legacy mode only retries
# throttling codes and 5xx.
_CLIENT_CONFIG = Config(read_timeout=60, retries={"mode": "standard"})

# Where local mode finds the agent: the AgentCore SDK's local server, which
# serves the same /invocations + SSE surface as the Runtime, on its default
# port. http.client (rather than urllib) keeps a developer's HTTP_PROXY out
# of the way — these requests must reach localhost directly.
LOCAL_AGENT_HOST = "localhost"
LOCAL_AGENT_PORT = 8080
LOCAL_AGENT_URL = f"http://{LOCAL_AGENT_HOST}:{LOCAL_AGENT_PORT}"

# The socket timeout plays the same role as the boto3 read timeout above:
# it bounds each blocking read, not the whole reply.
LOCAL_AGENT_TIMEOUT = 60


def init_client(
    *, region_name: str, gateway_url: str | None = None, gateway_only: bool = False
) -> None:
    """
    Create the shared bedrock-agentcore client.

    Called once at startup, before any listener runs: a misconfigured
    credential chain then fails at boot instead of on the first message,
    and client creation (not thread-safe through boto3's default session)
    never races between worker threads.

    Args:
        region_name (str): The signing region (`Env.agent_region`), derived
            from the ARN or Gateway URL instead of ambient configuration.
        gateway_url (str | None): IAM-authenticated Gateway target base URL,
            or None to invoke Runtime directly.
        gateway_only (bool): Whether no ARN is configured, selecting signed
            HTTP instead of the Runtime SDK's endpoint-override mode.

    Returns:
        None
    """
    global _client, _gateway_client
    _client = None
    _gateway_client = None
    if gateway_only and gateway_url is not None:
        _gateway_client = GatewayClient(url=gateway_url, region_name=region_name)
    else:
        _client = boto3.client(
            "bedrock-agentcore",
            region_name=region_name,
            endpoint_url=gateway_url,
            config=_CLIENT_CONFIG,
        )


def _get_client():
    if _client is None:
        raise RuntimeError("agent_service.init_client() must be called at startup")
    return _client


def log_local_mode() -> None:
    """
    Announce local mode, once at startup.

    Local mode's counterpart to `init_client`: with neither an ARN nor a
    Gateway URL configured, this log line names the local target. The agent
    may start, stop, or be swapped while Welt runs, and an unreachable one
    surfaces as a failed reply.

    Returns:
        None
    """
    logger.info(
        "AGENT_ARN and AGENT_GATEWAY_URL are not set — invoking the local agent at %s",
        LOCAL_AGENT_URL,
    )


def stream_agent_events(
    *,
    agent_arn: str | None,
    agent_qualifier: str | None,
    messages: list[Message],
    session_id: str,
    user_id: str,
    agent_manages_history: bool,
    agent_gateway_url: str | None = None,
) -> AsyncIterator[RenderEvent]:
    """
    Invoke the agent and stream render events parsed from its reply.

    Args:
        agent_arn (str | None): The ARN of the AgentCore Runtime agent or
            managed harness to invoke, or None for Gateway URL-only or local mode.
        agent_qualifier (str | None): The endpoint to invoke on that target
            (`Env.agent_qualifier`), or None for the DEFAULT endpoint.
        messages (list[Message]): The conversation, Converse-shaped.
        session_id (str): The runtimeSessionId (Slack thread/DM key).
        user_id (str): The runtimeUserId (verified Slack identity).
        agent_gateway_url (str | None): Configured Gateway URL; without an
            ARN, selects the signed HTTP path.
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
    if agent_arn is None and agent_gateway_url is not None:
        events = _stream_gateway_events(
            payload={"messages": messages}, session_id=session_id, user_id=user_id
        )
    elif agent_arn is None:
        events = _stream_local_events(
            payload={"messages": messages}, session_id=session_id
        )
    elif is_harness_arn(agent_arn):
        events = _stream_harness_events(
            harness_arn=agent_arn,
            qualifier=agent_qualifier,
            messages=messages,
            session_id=session_id,
            user_id=user_id,
        )
    else:
        events = _stream_runtime_events(
            agent_arn=agent_arn,
            qualifier=agent_qualifier,
            payload={"messages": messages},
            session_id=session_id,
            user_id=user_id,
        )
    return _translate_silence(events)


def stream_agent_resume_events(
    *,
    agent_arn: str | None,
    agent_qualifier: str | None,
    interrupt_responses: dict,
    session_id: str,
    user_id: str,
    agent_gateway_url: str | None = None,
) -> AsyncIterator[RenderEvent]:
    """
    Resume an interrupted run with the collected answers.

    Never the harness path: interrupts only ever come from a runtime or
    local agent, so this is only reached for one (a press on a button left
    over from an earlier runtime target after AGENT_ARN moved to a harness
    simply fails, surfacing through the usual reply-failure route).

    Args:
        agent_arn (str | None): The ARN of the AgentCore Runtime agent to
            resume, or None for Gateway URL-only or local mode.
        agent_qualifier (str | None): The endpoint the interrupted run was
            invoked on, so the resume reaches the same agent version.
        agent_gateway_url (str | None): Configured Gateway URL; without an
            ARN, selects the same signed HTTP path as the original turn.
        interrupt_responses (dict): The collected answers, one value per
            interrupt id (`interrupt_logic.build_interrupt_responses`).
        session_id (str): The runtimeSessionId the interrupted run used.
        user_id (str): The runtimeUserId (the presser's verified identity).

    Returns:
        AsyncIterator[RenderEvent]: The resumed reply's render events.
    """
    payload = {"interrupt_responses": interrupt_responses}
    if agent_arn is None and agent_gateway_url is not None:
        events = _stream_gateway_events(
            payload=payload, session_id=session_id, user_id=user_id
        )
    elif agent_arn is None:
        events = _stream_local_events(payload=payload, session_id=session_id)
    else:
        events = _stream_runtime_events(
            agent_arn=agent_arn,
            qualifier=agent_qualifier,
            payload=payload,
            session_id=session_id,
            user_id=user_id,
        )
    return _translate_silence(events)


async def _translate_silence(
    events: AsyncIterator[RenderEvent],
) -> AsyncIterator[RenderEvent]:
    # A read timeout means the stream went quiet, which is not always a
    # stalled connection: a model that thinks before it speaks sends nothing
    # while it does. Naming that case as its own exception is what lets a
    # caller say so instead of reporting a broken reply. Wrapping all invoke
    # paths here keeps the two timeout flavours — boto3's and the socket's —
    # in one place, and only the agent's stream runs inside this scope, so a
    # bare TimeoutError here (which asyncio raises too) can only be a read.
    try:
        async for render_event in events:
            yield render_event
    except (ReadTimeoutError, TimeoutError) as error:
        raise AgentSilenceTimeout from error


async def _stream_runtime_events(
    *,
    agent_arn: str,
    qualifier: str | None,
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
            **build_qualifier_kwargs(qualifier),
        )

    response = await asyncio.to_thread(invoke)
    stream = response["response"]
    try:
        lines: Iterator[bytes] = stream.iter_lines()
        async for render_event in _render_events_from_sse_lines(lines):
            yield render_event
    finally:
        stream.close()


async def _stream_gateway_events(
    *, payload: dict, session_id: str, user_id: str
) -> AsyncIterator[RenderEvent]:
    if _gateway_client is None:
        raise RuntimeError("agent_service.init_client() must be called at startup")
    stream = await asyncio.to_thread(
        _gateway_client.invoke, payload=payload, session_id=session_id, user_id=user_id
    )
    try:
        async for event in _render_events_from_sse_lines(stream.iter_lines()):
            yield event
    finally:
        stream.close()


async def _stream_local_events(
    *, payload: dict, session_id: str
) -> AsyncIterator[RenderEvent]:
    connection = http.client.HTTPConnection(
        LOCAL_AGENT_HOST, LOCAL_AGENT_PORT, timeout=LOCAL_AGENT_TIMEOUT
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
        response = connection.getresponse()
        # A Runtime invoke fails as an exception boto3 raises; a local one
        # answers with a status and raises nothing. Left at that, the error
        # body parses as an event stream carrying nothing, and a reply that
        # renders nothing opens no message — the failure would reach no one.
        # What went wrong is the local agent's own to report, on the console
        # it runs in, so the status is all this carries.
        if not 200 <= response.status < 300:
            raise RuntimeError(f"The local agent answered {response.status}")
        return response

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
    qualifier: str | None,
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
            **build_qualifier_kwargs(qualifier),
        )

    response = await asyncio.to_thread(invoke)
    stream = response["stream"]
    last_stop_reason: object = None
    try:
        events: Iterator[dict] = iter(stream)
        async for event in _iterate_in_thread(events):
            message_stop = event.get("messageStop")
            if isinstance(message_stop, dict):
                last_stop_reason = message_stop.get("stopReason")
            render_event = parse_harness_event(event)
            if render_event is not None:
                yield render_event
    finally:
        stream.close()
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
