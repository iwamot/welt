"""Show what Welt makes of a local agent's reply stream.

A check for the wire between an adapter and Welt, run against the agent's
local server without Slack or AWS in the way. The request is built the way
Welt builds one — the prompt as a Slack reply through `build_messages`, a
file as the block its modality calls for — and every line of the reply is
read through Welt's own parser, so each event is shown next to what Welt
would render for it, or marked ignored where Welt would show nothing.

    uv run wire_check.py "what time is it?"
    uv run wire_check.py --file report.pdf "summarize this"

An interrupt ends the run with the interrupt's id and the session id, since
resuming is a second request carrying the answers alone; answer it with a
second run, `--answer` for an option's value (as JSON) and `--input` for
typed text:

    uv run wire_check.py --resume <session id> --answer <interrupt id>='"Canary first"'
    uv run wire_check.py --resume <session id> --input <interrupt id>='ship it'

The agent is a model call, so this runs one turn per invocation and is not
part of validate.sh.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import logging
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.agent_logic import build_runtime_session_id
from app.agent_service import LOCAL_AGENT_HOST, LOCAL_AGENT_PORT, LOCAL_AGENT_TIMEOUT
from app.converse_logic import (
    ContentBlock,
    build_document_block,
    build_image_block,
    build_messages,
    build_video_block,
)
from app.slack_file_logic import CONVERSE_FORMATS
from app.stream_logic import (
    FileOutput,
    Interrupt,
    RenderEvent,
    StreamError,
    TextDelta,
    ToolResult,
    ToolUse,
    parse_sse_data_line,
    parse_stream_event,
)

# What the prompt is sent as: a reply from someone Welt has no ID for, so
# the message carries the name itself rather than a mention to look up.
SPEAKER = "wire_check"

# File extensions that spell a Converse format differently.
_FORMAT_BY_EXTENSION = {"jpg": "jpeg", "htm": "html", "3gp": "three_gp"}


@dataclass(frozen=True)
class LoadedFile:
    """A file to send with the prompt, read into memory."""

    name: str
    data: bytes


@dataclass(frozen=True)
class Observation:
    """One line of the reply stream, and the event Welt decodes from it."""

    line: str
    event: dict | None


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """
    Parse the command line.

    Args:
        argv (Sequence[str]): The arguments after the program name.

    Returns:
        argparse.Namespace: `prompt`, `file`, `resume`, `answer`, `input`.
    """
    parser = argparse.ArgumentParser(
        prog="wire_check.py",
        description="Show what Welt makes of a local agent's reply stream.",
    )
    parser.add_argument("prompt", nargs="?", help="what to say to the agent")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH",
        help="a file to send with the prompt (repeatable)",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION",
        help="resume the interrupted run of this session id with the answers",
    )
    parser.add_argument(
        "--answer",
        action="append",
        default=[],
        metavar="ID=JSON",
        help="answer an interrupt with an option's value, as JSON (repeatable)",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="ID=TEXT",
        help="answer an interrupt with typed text (repeatable)",
    )
    return parser.parse_args(argv)


def usage_error(args: argparse.Namespace) -> str | None:
    """
    Judge whether the arguments describe one request.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        str | None: What is wrong with the combination, or None.
    """
    if args.resume is None:
        if args.prompt is None:
            return "a prompt is required unless --resume is given"
        if args.answer or args.input:
            return "--answer and --input go with --resume"
        return None
    if args.prompt is not None or args.file:
        return "--resume takes answers, not a prompt or files"
    if not args.answer and not args.input:
        return "--resume needs at least one --answer or --input"
    return None


def build_session_id(now: float) -> str:
    """
    Build the session id for a new run.

    Args:
        now (float): The current time, in seconds since the epoch.

    Returns:
        str: A session id in the same shape Welt sends, and within the same
            constraints — a Slack thread's timestamp is what fills the
            thread slot, so the time plays the part here.
    """
    return build_runtime_session_id(
        team_id=None, channel_id=SPEAKER, thread_ts=f"{now:.6f}"
    )


def file_block(file: LoadedFile) -> ContentBlock:
    """
    Shape a file into the wire block its extension calls for.

    Args:
        file (LoadedFile): The file to send.

    Returns:
        ContentBlock: The image, video, or document block.

    Raises:
        ValueError: If the extension names no Converse format.
    """
    extension = Path(file.name).suffix[1:].lower()
    file_format = _FORMAT_BY_EXTENSION.get(extension, extension)
    if file_format not in CONVERSE_FORMATS:
        raise ValueError(
            f"{file.name}: no Converse format for the extension; "
            f"one of {', '.join(CONVERSE_FORMATS)}"
        )
    modality = CONVERSE_FORMATS[file_format][0]
    data_base64 = _base64(file.data)
    if modality == "image":
        return build_image_block(image_format=file_format, data_base64=data_base64)
    if modality == "video":
        return build_video_block(video_format=file_format, data_base64=data_base64)
    return build_document_block(
        document_format=file_format, name=file.name, data_base64=data_base64
    )


def _base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def build_turn_payload(prompt: str, files: Sequence[LoadedFile]) -> dict:
    """
    Build the request for a conversation turn, the way Welt builds one.

    The prompt travels as a Slack reply — the speaker's name in front of
    it, each file named under it and carried as a block — through the same
    `build_messages` that shapes a thread.

    Args:
        prompt (str): What to say to the agent.
        files (Sequence[LoadedFile]): The files to send with it.

    Returns:
        dict: The `messages` payload.

    Raises:
        ValueError: If a file's extension names no Converse format.
    """
    file_entries = [
        {"id": f"F{index}", "name": file.name} for index, file in enumerate(files)
    ]
    reply = {"username": SPEAKER, "text": prompt, "files": file_entries}
    file_blocks_by_id = {
        entry["id"]: file_block(file)
        for entry, file in zip(file_entries, files, strict=True)
    }
    messages = build_messages(
        [reply], bot_user_id=None, file_blocks_by_id=file_blocks_by_id
    )
    return {"messages": messages}


def build_resume_payload(answers: Sequence[str], inputs: Sequence[str]) -> dict:
    """
    Build the request that resumes an interrupted run.

    Args:
        answers (Sequence[str]): `ID=JSON` pairs, an option's value each.
        inputs (Sequence[str]): `ID=TEXT` pairs, typed text each.

    Returns:
        dict: The `interrupt_responses` payload, one answer per interrupt id
            with the widget it came from.

    Raises:
        ValueError: If a pair has no `=`, or an answer's value is not JSON.
    """
    responses = {}
    for pair in answers:
        interrupt_id, raw = _split_pair(pair)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"{pair}: the value is not JSON ({error})") from error
        responses[interrupt_id] = {"value": value, "source": "option"}
    for pair in inputs:
        interrupt_id, text = _split_pair(pair)
        responses[interrupt_id] = {"value": text, "source": "input"}
    return {"interrupt_responses": responses}


def _split_pair(pair: str) -> tuple[str, str]:
    interrupt_id, separator, value = pair.partition("=")
    if not separator or not interrupt_id:
        raise ValueError(f"{pair}: expected ID=VALUE")
    return interrupt_id, value


def describe_request(session_id: str, payload: dict) -> str:
    """
    Say what is about to be sent.

    Args:
        session_id (str): The session id the request goes out under.
        payload (dict): The request body.

    Returns:
        str: The target, the session, and the body with every base64
            payload replaced by its length, since the bytes say nothing a
            reader could check.
    """
    return "\n".join(
        [
            f"→ POST http://{LOCAL_AGENT_HOST}:{LOCAL_AGENT_PORT}/invocations",
            f"  session: {session_id}",
            f"  {json.dumps(_without_bytes(payload))}",
        ]
    )


def _without_bytes(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: f"<{len(item)} base64 chars>"
            if key == "bytes" and isinstance(item, str)
            else _without_bytes(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_without_bytes(item) for item in value]
    return value


def observe(line: str) -> Observation | None:
    """
    Decode one line of the reply stream the way Welt does.

    Args:
        line (str): One line of the SSE response.

    Returns:
        Observation | None: The line with the event it carries, or None for
            a blank line, which separates events and says nothing.
    """
    if not line.strip():
        return None
    return Observation(line=line, event=parse_sse_data_line(line))


def describe_line(observation: Observation) -> str:
    """
    Say what a line carried.

    Args:
        observation (Observation): One observed line.

    Returns:
        str: The line as it came off the wire — not re-encoded, so what the
            agent escaped and how it spaced the JSON is what shows.
    """
    return f"← {observation.line.rstrip()}"


def render(observation: Observation) -> RenderEvent | None:
    """
    Read an observed event the way Welt renders it.

    Welt's parser warns as it drops an event, so this is called only once
    the line is on screen, where the warning lands under it.

    Args:
        observation (Observation): One observed line.

    Returns:
        RenderEvent | None: What Welt would render, or None for a line that
            is not an event or an event Welt renders nothing for.
    """
    if observation.event is None:
        return None
    return parse_stream_event(observation.event)


def describe_rendering(
    observation: Observation, render_event: RenderEvent | None
) -> str:
    """
    Say what Welt would do with a line.

    Args:
        observation (Observation): One observed line.
        render_event (RenderEvent | None): What `render` read from it.

    Returns:
        str: The render event and what it renders as, `not an event` for a
            line that is not a `data:` line carrying a JSON object, or
            `ignored` for an event Welt renders nothing for.
    """
    if observation.event is None:
        return "  not an event"
    return f"  {_rendering(render_event)}"


def _rendering(render_event: RenderEvent | None) -> str:
    if isinstance(render_event, TextDelta):
        return f"{render_event!r}  appended to the reply"
    if isinstance(render_event, ToolUse):
        return f"{render_event!r}  opens the tool indicator"
    if isinstance(render_event, ToolResult):
        outcome = "as failed" if render_event.error else "as done"
        return f"{render_event!r}  closes the tool indicator {outcome}"
    if isinstance(render_event, FileOutput):
        size = len(render_event.data)
        return f"FileOutput(name={render_event.name!r}, {size} bytes)  uploaded to the thread"
    if isinstance(render_event, Interrupt):
        return f"{render_event!r}  asked in the thread"
    if isinstance(render_event, StreamError):
        return f"{render_event!r}  reported in the thread"
    return "ignored"


def resume_hint(session_id: str, interrupts: Sequence[Interrupt]) -> str | None:
    """
    Say how to answer the interrupts a run stopped on.

    Args:
        session_id (str): The session id the run went out under.
        interrupts (Sequence[Interrupt]): The interrupts the reply carried.

    Returns:
        str | None: The resume commands, one per interrupt, or None when
            the run stopped on nothing.
    """
    if not interrupts:
        return None
    lines = [f"The agent is waiting on {len(interrupts)} interrupt(s). Resume with:"]
    for interrupt in interrupts:
        lines.append(
            f"  uv run wire_check.py --resume {session_id} "
            f"--answer {interrupt.id}=<json>  (an option's value)"
        )
        lines.append(
            f"  uv run wire_check.py --resume {session_id} "
            f"--input {interrupt.id}=<text>  (typed text)"
        )
    return "\n".join(lines)


def post(payload: dict, session_id: str) -> Iterator[str]:
    """
    Send one request to the local agent and yield the reply's lines.

    The same request `agent_service` sends in local mode: the session id
    in the header the AgentCore SDK's local server reads, and a status
    outside 2xx raised, since the body would otherwise read as an event
    stream carrying nothing.

    Args:
        payload (dict): The request body.
        session_id (str): The session id.

    Yields:
        str: One line of the SSE response.
    """
    connection = http.client.HTTPConnection(
        LOCAL_AGENT_HOST, LOCAL_AGENT_PORT, timeout=LOCAL_AGENT_TIMEOUT
    )
    try:
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
        if not 200 <= response.status < 300:
            raise RuntimeError(f"The local agent answered {response.status}")
        for line in response:
            yield line.decode("utf-8")
    finally:
        connection.close()


def main(argv: Sequence[str]) -> int:
    """
    Run one request and print what Welt makes of the reply.

    Args:
        argv (Sequence[str]): The arguments after the program name.

    Returns:
        int: The exit status.
    """
    # Welt's parser warns where it drops an event; those warnings are the
    # reason a line reads as ignored, so they go to the same stream, in
    # order — a piped stdout would otherwise hold the lines back while the
    # warnings went ahead on stderr.
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(
        level=logging.WARNING,
        format="  %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    args = parse_args(argv)
    error = usage_error(args)
    if error is not None:
        print(f"wire_check.py: {error}", file=sys.stderr)
        return 2
    try:
        if args.resume is None:
            session_id = build_session_id(time.time())
            files = [
                LoadedFile(name=Path(p).name, data=Path(p).read_bytes())
                for p in args.file
            ]
            payload = build_turn_payload(args.prompt, files)
        else:
            session_id = args.resume
            payload = build_resume_payload(args.answer, args.input)
    except ValueError as error:
        print(f"wire_check.py: {error}", file=sys.stderr)
        return 2
    print(describe_request(session_id, payload))
    interrupts = []
    for line in post(payload, session_id):
        observation = observe(line)
        if observation is None:
            continue
        print(describe_line(observation))
        render_event = render(observation)
        print(describe_rendering(observation, render_event))
        if isinstance(render_event, Interrupt):
            interrupts.append(render_event)
    hint = resume_hint(session_id, interrupts)
    if hint is not None:
        print(hint)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
