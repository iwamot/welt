"""Checks that what Welt sends matches the published payload schema.

`schema/request-payload.schema.json` is the machine-readable half of the wire contract,
and the agent-side adapters reject a payload that fails it. A schema that
drifts from Welt's actual output would have every adapter reject real
traffic, so the payload builders are checked against it here.
"""

from __future__ import annotations

import json
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


def test_a_text_only_thread_matches_the_schema():
    messages = build_messages(
        [
            {"user": "U1", "text": "hello"},
            {"user": "U_BOT", "text": "hi"},
            {"user": "U1", "text": "thanks"},
        ],
        bot_user_id="U_BOT",
    )

    validate({"messages": messages})


def test_a_thread_carrying_every_file_kind_matches_the_schema():
    blocks: dict[str, ContentBlock] = {
        "F_IMG": build_image_block(image_format="png", data_base64="aW1n"),
        "F_DOC": build_document_block(
            document_format="pdf", name="quarterly report", data_base64="ZG9j"
        ),
        "F_VID": build_video_block(video_format="three_gp", data_base64="dmlk"),
    }
    messages = build_messages(
        [
            {
                "user": "U1",
                "text": "see attached",
                "files": [{"id": "F_IMG"}, {"id": "F_DOC"}, {"id": "F_VID"}],
            }
        ],
        bot_user_id="U_BOT",
        file_blocks_by_id=blocks,
    )

    validate({"messages": messages})


def test_a_file_reply_without_text_matches_the_schema():
    blocks: dict[str, ContentBlock] = {
        "F1": build_image_block(image_format="png", data_base64="aW1n")
    }
    messages = build_messages(
        [{"user": "U1", "text": "<@U_BOT>", "files": [{"id": "F1"}]}],
        bot_user_id="U_BOT",
        file_blocks_by_id=blocks,
    )

    validate({"messages": messages})


def test_a_sanitized_document_name_matches_the_schema():
    blocks: dict[str, ContentBlock] = {
        "F1": build_document_block(
            document_format="txt", name="../secret file.txt", data_base64="ZG9j"
        )
    }
    messages = build_messages(
        [{"user": "U1", "text": "here", "files": [{"id": "F1"}]}],
        bot_user_id="U_BOT",
        file_blocks_by_id=blocks,
    )

    validate({"messages": messages})


def test_two_documents_sharing_a_name_at_the_length_limit_match_the_schema():
    blocks: dict[str, ContentBlock] = {
        file_id: build_document_block(
            document_format="pdf", name="a" * 250, data_base64="ZG9j"
        )
        for file_id in ("F1", "F2")
    }
    messages = build_messages(
        [{"user": "U1", "text": "two", "files": [{"id": "F1"}, {"id": "F2"}]}],
        bot_user_id="U_BOT",
        file_blocks_by_id=blocks,
    )

    validate({"messages": messages})


def test_the_trimmed_thread_an_agent_managing_history_gets_matches_the_schema():
    messages = build_messages(
        [
            {"user": "U1", "text": "hello"},
            {"user": "U_BOT", "text": "hi"},
            {"user": "U1", "text": "one more thing"},
        ],
        bot_user_id="U_BOT",
    )

    validate({"messages": keep_messages_after_last_assistant(messages)})


def test_a_resume_payload_matches_the_schema():
    state = CollectionState(
        pending=("i-1", "i-2"),
        answers={
            "i-1": Answer(value="approve", user="U1"),
            "i-2": Answer(value="ship it", user="U2"),
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
        {"interrupt_responses": {"i-1": True}},
    ],
)
def test_the_schema_rejects_what_the_contract_does_not_describe(payload: dict):
    assert not is_valid(payload)
