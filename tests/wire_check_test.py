from __future__ import annotations

import base64
import json

import pytest

from app.stream_logic import (
    FileOutput,
    Interrupt,
    StreamError,
    TextDelta,
    ToolResult,
    ToolUse,
)
from wire_check import (
    LoadedFile,
    Observation,
    build_resume_payload,
    build_session_id,
    build_turn_payload,
    describe_line,
    describe_rendering,
    describe_request,
    file_block,
    observe,
    parse_args,
    render,
    resume_hint,
    usage_error,
)

# --- parse_args / usage_error -------------------------------------------------


def test_prompt_alone_is_a_turn():
    args = parse_args(["hello"])
    assert args.prompt == "hello"
    assert args.file == []
    assert args.resume is None
    assert usage_error(args) is None


def test_files_repeat():
    args = parse_args(["--file", "a.pdf", "--file", "b.png", "hello"])
    assert args.file == ["a.pdf", "b.png"]
    assert usage_error(args) is None


def test_resume_with_answers_and_inputs():
    args = parse_args(["--resume", "s", "--answer", "i1=true", "--input", "i2=text"])
    assert args.resume == "s"
    assert args.answer == ["i1=true"]
    assert args.input == ["i2=text"]
    assert usage_error(args) is None


def test_no_prompt_and_no_resume_is_an_error():
    assert (
        usage_error(parse_args([])) == "a prompt is required unless --resume is given"
    )


def test_answers_without_resume_are_an_error():
    args = parse_args(["--answer", "i=1", "hello"])
    assert usage_error(args) == "--answer and --input go with --resume"


def test_resume_with_a_prompt_is_an_error():
    args = parse_args(["--resume", "s", "--answer", "i=1", "hello"])
    assert usage_error(args) == "--resume takes answers, not a prompt or files"


def test_resume_with_a_file_is_an_error():
    args = parse_args(["--resume", "s", "--answer", "i=1", "--file", "a.pdf"])
    assert usage_error(args) == "--resume takes answers, not a prompt or files"


def test_resume_without_answers_is_an_error():
    args = parse_args(["--resume", "s"])
    assert usage_error(args) == "--resume needs at least one --answer or --input"


# --- build_session_id ---------------------------------------------------------


def test_session_id_is_shaped_like_welts_and_long_enough():
    session_id = build_session_id(1757000000.123456)
    assert session_id == "slack_-_wire_check_1757000000-123456"
    assert len(session_id) >= 33


def test_session_id_is_padded_to_the_minimum():
    assert len(build_session_id(1.0)) == 33


# --- file_block ---------------------------------------------------------------


def test_file_block_by_modality():
    data = b"\x00\x01"
    encoded = base64.b64encode(data).decode("ascii")
    assert file_block(LoadedFile(name="a.png", data=data)) == {
        "image": {"format": "png", "source": {"bytes": encoded}}
    }
    assert file_block(LoadedFile(name="a.mp4", data=data)) == {
        "video": {"format": "mp4", "source": {"bytes": encoded}}
    }
    assert file_block(LoadedFile(name="a.pdf", data=data)) == {
        "document": {"format": "pdf", "name": "a-pdf", "source": {"bytes": encoded}}
    }


def test_file_block_maps_extension_spellings():
    encoded = base64.b64encode(b"x").decode("ascii")
    assert file_block(LoadedFile(name="A.JPG", data=b"x")) == {
        "image": {"format": "jpeg", "source": {"bytes": encoded}}
    }
    assert file_block(LoadedFile(name="a.3gp", data=b"x")) == {
        "video": {"format": "three_gp", "source": {"bytes": encoded}}
    }
    assert file_block(LoadedFile(name="a.htm", data=b"x")) == {
        "document": {"format": "html", "name": "a-htm", "source": {"bytes": encoded}}
    }


def test_file_block_refuses_an_unknown_extension():
    with pytest.raises(ValueError, match="a.zip: no Converse format"):
        file_block(LoadedFile(name="a.zip", data=b"x"))


# --- build_turn_payload -------------------------------------------------------


def test_turn_payload_is_one_user_message_named_after_the_speaker():
    assert build_turn_payload("hello", []) == {
        "messages": [{"role": "user", "content": [{"text": "@wire_check: hello"}]}]
    }


def test_turn_payload_carries_files_as_blocks_and_names_them():
    payload = build_turn_payload(
        "read these",
        [LoadedFile(name="a.pdf", data=b"p"), LoadedFile(name="b.png", data=b"i")],
    )
    content = payload["messages"][0]["content"]
    assert [next(iter(block)) for block in content] == ["document", "text", "image"]
    assert content[1] == {
        "text": "@wire_check: read these\n\n[file: a.pdf]\n[file: b.png]"
    }


def test_turn_payload_refuses_an_unknown_extension():
    with pytest.raises(ValueError, match="a.zip"):
        build_turn_payload("hello", [LoadedFile(name="a.zip", data=b"x")])


# --- build_resume_payload -----------------------------------------------------


def test_resume_payload_keeps_answers_apart_from_inputs():
    assert build_resume_payload(["i1=true", 'i2="Canary first"'], ["i3=ship it"]) == {
        "interrupt_responses": {
            "i1": {"value": True, "source": "option"},
            "i2": {"value": "Canary first", "source": "option"},
            "i3": {"value": "ship it", "source": "input"},
        }
    }


def test_resume_payload_splits_on_the_first_equals():
    assert build_resume_payload([], ["i=a=b"]) == {
        "interrupt_responses": {"i": {"value": "a=b", "source": "input"}}
    }


def test_resume_payload_refuses_a_pair_without_equals():
    with pytest.raises(ValueError, match="i1: expected ID=VALUE"):
        build_resume_payload(["i1"], [])
    with pytest.raises(ValueError, match="=x: expected ID=VALUE"):
        build_resume_payload([], ["=x"])


def test_resume_payload_refuses_a_non_json_answer():
    with pytest.raises(ValueError, match="i=Canary first: the value is not JSON"):
        build_resume_payload(["i=Canary first"], [])


# --- describe_request ---------------------------------------------------------


def test_request_description_elides_base64():
    payload = build_turn_payload("hi", [LoadedFile(name="a.png", data=b"\x00" * 30)])
    described = describe_request("sess", payload)
    lines = described.split("\n")
    assert lines[0] == "→ POST http://localhost:8080/invocations"
    assert lines[1] == "  session: sess"
    body = json.loads(lines[2])
    assert (
        body["messages"][0]["content"][1]["image"]["source"]["bytes"]
        == "<40 base64 chars>"
    )


def test_request_description_prints_a_resume_body_whole():
    payload = build_resume_payload(["i=1"], [])
    assert describe_request("sess", payload).endswith("\n  " + json.dumps(payload))


# --- observe / describe -------------------------------------------------------


def test_blank_lines_are_not_observed():
    assert observe("\n") is None
    assert observe("   ") is None


def test_a_data_line_is_decoded_through_welts_parser():
    observation = observe('data: {"data": "hi"}\n')
    assert observation == Observation(
        line='data: {"data": "hi"}\n', event={"data": "hi"}
    )
    assert render(observation) == TextDelta(text="hi")


def test_a_line_that_is_not_an_event_is_kept():
    observation = observe(": keep-alive\n")
    assert observation == Observation(line=": keep-alive\n", event=None)
    assert render(observation) is None


def test_describe_non_event():
    observation = Observation(line=": ping\n", event=None)
    assert describe_line(observation) == "← : ping"
    assert describe_rendering(observation, None) == "  not an event"


def test_describe_ignored_event():
    observation = Observation(line='data: {"other":1}\n', event={"other": 1})
    assert describe_line(observation) == '← data: {"other":1}'
    assert render(observation) is None
    assert describe_rendering(observation, None) == "  ignored"


@pytest.mark.parametrize(
    ("render_event", "rendering"),
    [
        (TextDelta(text="hi"), "TextDelta(text='hi')  appended to the reply"),
        (
            ToolUse(name="t", tool_use_id="1"),
            "ToolUse(name='t', tool_use_id='1')  opens the tool indicator",
        ),
        (
            ToolResult(tool_use_id="1", error=False),
            "ToolResult(tool_use_id='1', error=False)  closes the tool indicator as done",
        ),
        (
            ToolResult(tool_use_id="1", error=True),
            "ToolResult(tool_use_id='1', error=True)  closes the tool indicator as failed",
        ),
        (
            FileOutput(name="a.csv", data=b"1,2\n"),
            "FileOutput(name='a.csv', 4 bytes)  uploaded to the thread",
        ),
        (
            Interrupt(id="i", name="ask", reason="ok?"),
            "Interrupt(id='i', name='ask', reason='ok?')  asked in the thread",
        ),
        (
            StreamError(message="boom"),
            "StreamError(message='boom')  reported in the thread",
        ),
    ],
)
def test_describe_render_events(render_event, rendering):
    observation = Observation(line='data: {"k": "v"}\n', event={"k": "v"})
    assert describe_line(observation) == '← data: {"k": "v"}'
    assert describe_rendering(observation, render_event) == f"  {rendering}"


# --- resume_hint --------------------------------------------------------------


def test_no_hint_without_interrupts():
    assert resume_hint("sess", []) is None


def test_hint_names_each_interrupt_and_both_widgets():
    hint = resume_hint(
        "sess",
        [
            Interrupt(id="i1", name="a", reason=None),
            Interrupt(id="i2", name="b", reason=None),
        ],
    )
    assert hint is not None
    assert hint.split("\n") == [
        "The agent is waiting on 2 interrupt(s). Resume with:",
        "  uv run wire_check.py --resume sess --answer i1=<json>  (an option's value)",
        "  uv run wire_check.py --resume sess --input i1=<text>  (typed text)",
        "  uv run wire_check.py --resume sess --answer i2=<json>  (an option's value)",
        "  uv run wire_check.py --resume sess --input i2=<text>  (typed text)",
    ]
