"""Checks that what Welt sends matches the published payload schema.

`schema/request-payload.schema.json` is the machine-readable half of the wire
contract, and Welt is the side that produces this direction: it can state
what it sends, and hold itself to it. Nothing reads the schema at runtime, so
these tests are what keeps the published contract and the payload builders
from drifting apart.
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.converse_logic import (
    ContentBlock,
    build_document_block,
    build_image_block,
    build_messages,
    build_video_block,
    keep_messages_after_last_assistant,
)
from app.interrupt_logic import Answer, CollectionState, build_interrupt_responses
from app.slack_file_logic import CONVERSE_FORMATS

SCHEMA = json.loads(
    (
        Path(__file__).parent.parent / "schema" / "request-payload.schema.json"
    ).read_text()
)


def validate(payload: dict) -> None:
    Draft202012Validator(SCHEMA).validate(payload)


def is_valid(payload: dict) -> bool:
    return Draft202012Validator(SCHEMA).is_valid(payload)


def test_the_schema_itself_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_the_format_tokens_are_the_ones_welt_can_send():
    tokens = SCHEMA["$defs"]
    by_modality: dict[str, list[str]] = {
        "image": tokens["imageBlock"]["properties"]["image"]["properties"]["format"][
            "enum"
        ],
        "document": tokens["documentBlock"]["properties"]["document"]["properties"][
            "format"
        ]["enum"],
        "video": tokens["videoBlock"]["properties"]["video"]["properties"]["format"][
            "enum"
        ],
    }

    for modality, schema_tokens in by_modality.items():
        welt_tokens = sorted(
            token
            for token, (token_modality, _) in CONVERSE_FORMATS.items()
            if token_modality == modality
        )
        assert sorted(schema_tokens) == welt_tokens


_FILE_BLOCKS: dict[str, ContentBlock] = {
    "F_IMG": build_image_block(image_format="png", data_base64="aW1n"),
    "F_DOC": build_document_block(
        document_format="pdf", name="quarterly report", data_base64="ZG9j"
    ),
    "F_VID": build_video_block(video_format="three_gp", data_base64="dmlk"),
    "F_TXT": build_document_block(
        document_format="txt", name="../secret file.txt", data_base64="ZG9j"
    ),
    # A name with nothing Converse accepts in it.
    "F_NONAME": build_document_block(
        document_format="txt", name="   ", data_base64="ZG9j"
    ),
    # Two documents sharing a name at the length limit, so the second is
    # renamed against it.
    "F_LONG1": build_document_block(
        document_format="pdf", name="a" * 250, data_base64="ZG9j"
    ),
    "F_LONG2": build_document_block(
        document_format="pdf", name="a" * 250, data_base64="ZG9j"
    ),
}

_THREAD_REPLIES = (
    {"user": "U1", "text": "hello"},
    {"user": "U_BOT", "text": "hi"},
    {"user": "U_BOT", "text": ""},
    {
        "user": "U1",
        "text": "see attached",
        "files": [{"id": "F_IMG"}, {"id": "F_DOC"}, {"id": "F_VID"}],
    },
    # Nothing but a mention beside the file.
    {"user": "U1", "text": "<@U_BOT>", "files": [{"id": "F_IMG"}]},
    {"user": "U1", "text": "here", "files": [{"id": "F_TXT"}, {"id": "F_NONAME"}]},
    {"user": "U1", "text": "two", "files": [{"id": "F_LONG1"}, {"id": "F_LONG2"}]},
)


def test_every_thread_welt_builds_matches_the_schema():
    # A thread Welt answers holds somebody else's post: the one it answers.
    validator = Draft202012Validator(SCHEMA)
    for size in range(1, 4):
        for thread in product(_THREAD_REPLIES, repeat=size):
            if all(reply["user"] == "U_BOT" for reply in thread):
                continue
            messages = build_messages(
                list(thread), bot_user_id="U_BOT", file_blocks_by_id=_FILE_BLOCKS
            )
            # An agent holding its own history gets the messages after
            # Welt's last reply.
            for sent in (messages, keep_messages_after_last_assistant(messages)):
                validator.validate({"messages": sent})


def test_a_resume_payload_matches_the_schema():
    state = CollectionState(
        pending=("i-1", "i-2", "i-3"),
        answers={
            "i-1": Answer(value="approve", source="option", user="U1"),
            "i-2": Answer(value="ship it", source="input", user="U2"),
            # A default button answers with a boolean, and the schema
            # describes an answer as whatever JSON its option declared.
            "i-3": Answer(value=False, source="option", user="U3"),
        },
    )

    validate({"interrupt_responses": build_interrupt_responses(state)})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"messages": [], "interrupt_responses": {"i-1": "y"}},
        {"messages": []},
        {"messages": [{"role": "system", "content": [{"text": "hi"}]}]},
        {"messages": [{"role": "assistant", "content": [{"text": "hi"}]}]},
        {"messages": [{"role": "user", "content": [{"toolUse": {}}]}]},
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"image": {"format": "png", "source": {"bytes": "aW1n"}}}
                    ],
                }
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": {"format": "avif", "source": {"bytes": "aW1n"}}}
                    ],
                }
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"document": {"format": "pdf", "source": {}}}],
                }
            ]
        },
        {"interrupt_responses": {}},
        # An answer states both what it was and where it came from, and
        # names a widget the reason contract has.
        {"interrupt_responses": {"i-1": "y"}},
        {"interrupt_responses": {"i-1": {"value": True}}},
        {"interrupt_responses": {"i-1": {"source": "option"}}},
        {"interrupt_responses": {"i-1": {"value": True, "source": "button"}}},
        {
            "interrupt_responses": {
                "i-1": {"value": True, "source": "option", "user": "U1"}
            }
        },
    ],
)
def test_the_schema_rejects_what_the_contract_does_not_describe(payload: dict):
    assert not is_valid(payload)
