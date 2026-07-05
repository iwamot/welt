from __future__ import annotations

from app.converse_logic import (
    Message,
    build_document_block,
    build_image_block,
    build_messages,
    build_video_block,
    keep_messages_after_last_assistant,
    sanitize_document_name,
)


def _build(replies: list[dict], **overrides: object) -> list[Message]:
    kwargs: dict = {"bot_user_id": "U_BOT"}
    kwargs.update(overrides)
    return build_messages(replies, **kwargs)


def test_user_message_is_prefixed_and_role_user():
    result = _build([{"user": "U1", "text": "hello"}])

    assert result == [{"role": "user", "content": [{"text": "<@U1>: hello"}]}]


def test_bot_message_is_assistant_and_not_prefixed():
    replies = [
        {"user": "U1", "text": "hi"},
        {"user": "U_BOT", "text": "hello there"},
        {"user": "U1", "text": "thanks"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "<@U1>: hi"}]},
        {"role": "assistant", "content": [{"text": "hello there"}]},
        {"role": "user", "content": [{"text": "<@U1>: thanks"}]},
    ]


def test_trailing_bot_replies_are_dropped():
    replies = [
        {"user": "U1", "text": "question"},
        {"user": "U_BOT", "text": "partial answer"},
        {"user": "U_BOT", "text": "loading ..."},
    ]

    result = _build(replies)

    assert result == [{"role": "user", "content": [{"text": "<@U1>: question"}]}]


def test_leading_bot_replies_are_dropped():
    # An overlong thread truncated to its newest replies can open with the
    # bot's own replies; Converse requires the conversation to start with a
    # user message.
    replies = [
        {"user": "U_BOT", "text": "old answer"},
        {"user": "U1", "text": "follow-up"},
        {"user": "U_BOT", "text": "answer"},
        {"user": "U1", "text": "question"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "<@U1>: follow-up"}]},
        {"role": "assistant", "content": [{"text": "answer"}]},
        {"role": "user", "content": [{"text": "<@U1>: question"}]},
    ]


def test_mention_only_reply_becomes_prefix_only_user_message():
    replies = [
        {"user": "U1", "text": "<@U_BOT>"},
        {"user": "U1", "text": "real message"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "<@U1>: "}]},
        {"role": "user", "content": [{"text": "<@U1>: real message"}]},
    ]


def test_mention_only_opener_keeps_conversation_starting_with_user():
    # A thread that opens with a mention-only call and the bot's answer must
    # not start with an assistant message (Converse rejects that), so the
    # opener has to survive as a user message.
    replies = [
        {"user": "U1", "text": "<@U_BOT>"},
        {"user": "U_BOT", "text": "how can I help?"},
        {"user": "U1", "text": "hello"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "<@U1>: "}]},
        {"role": "assistant", "content": [{"text": "how can I help?"}]},
        {"role": "user", "content": [{"text": "<@U1>: hello"}]},
    ]


def test_non_string_text_becomes_prefix_only_user_message():
    replies = [
        {"user": "U1", "text": 123},
        {"user": "U1", "text": "ok"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "<@U1>: "}]},
        {"role": "user", "content": [{"text": "<@U1>: ok"}]},
    ]


def test_mention_removed_and_mrkdwn_converted():
    result = _build([{"user": "U1", "text": "<@U_BOT> *bold*"}])

    assert result == [{"role": "user", "content": [{"text": "<@U1>: **bold**"}]}]


def test_slack_formatting_unescaped():
    result = _build([{"user": "U1", "text": "a &lt;b&gt; c"}])

    assert result == [{"role": "user", "content": [{"text": "<@U1>: a <b> c"}]}]


def test_bot_user_id_none_treats_all_as_user_and_drops_nothing():
    replies = [
        {"user": "U1", "text": "a"},
        {"user": "U_BOT", "text": "b"},
    ]

    result = _build(replies, bot_user_id=None)

    assert result == [
        {"role": "user", "content": [{"text": "<@U1>: a"}]},
        {"role": "user", "content": [{"text": "<@U_BOT>: b"}]},
    ]


def test_empty_replies():
    assert _build([]) == []


def test_bot_reply_with_empty_text_in_middle_is_skipped():
    replies = [
        {"user": "U1", "text": "question"},
        {"user": "U_BOT", "text": ""},
        {"user": "U1", "text": "still there?"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "<@U1>: question"}]},
        {"role": "user", "content": [{"text": "<@U1>: still there?"}]},
    ]


# --- file blocks -------------------------------------------------------------


def test_build_image_block():
    assert build_image_block(image_format="png", data_base64="AAAA") == {
        "image": {"format": "png", "source": {"bytes": "AAAA"}}
    }


def test_build_document_block_sanitizes_name():
    result = build_document_block(
        document_format="pdf", name="Q3_report (final).pdf", data_base64="BBBB"
    )

    assert result == {
        "document": {
            "format": "pdf",
            "name": "Q3-report (final)-pdf",
            "source": {"bytes": "BBBB"},
        }
    }


def test_build_video_block():
    assert build_video_block(video_format="mp4", data_base64="CCCC") == {
        "video": {"format": "mp4", "source": {"bytes": "CCCC"}}
    }


def test_sanitize_document_name_collapses_whitespace_and_falls_back():
    assert sanitize_document_name("a   b") == "a b"
    assert sanitize_document_name(None) == "document"
    assert sanitize_document_name("日本語.pdf") == "----pdf"


def test_file_blocks_attached_documents_before_text_media_after():
    image_block = build_image_block(image_format="png", data_base64="IMG")
    video_block = build_video_block(video_format="mp4", data_base64="VID")
    document_block = build_document_block(
        document_format="pdf", name="doc", data_base64="DOC"
    )
    replies = [
        {
            "user": "U1",
            "text": "see attached",
            "files": [{"id": "F_IMG"}, {"id": "F_DOC"}, {"id": "F_VID"}],
        }
    ]

    result = _build(
        replies,
        file_blocks_by_id={
            "F_IMG": image_block,
            "F_DOC": document_block,
            "F_VID": video_block,
        },
    )

    assert result == [
        {
            "role": "user",
            "content": [
                document_block,
                {"text": "<@U1>: see attached"},
                image_block,
                video_block,
            ],
        }
    ]


def test_reply_with_file_but_empty_text_is_kept():
    image_block = build_image_block(image_format="png", data_base64="IMG")
    replies = [{"user": "U1", "text": "<@U_BOT>", "files": [{"id": "F1"}]}]

    result = _build(replies, file_blocks_by_id={"F1": image_block})

    assert result == [{"role": "user", "content": [{"text": "<@U1>: "}, image_block]}]


def test_malformed_file_entries_are_ignored():
    image_block = build_image_block(image_format="png", data_base64="IMG")
    replies = [
        {
            "user": "U1",
            "text": "hi",
            "files": ["not-a-dict", {"id": 123}, {"id": "F1"}],
        }
    ]

    result = _build(replies, file_blocks_by_id={"F1": image_block})

    assert result == [{"role": "user", "content": [{"text": "<@U1>: hi"}, image_block]}]


def test_files_not_fetched_are_ignored():
    replies = [{"user": "U1", "text": "hi", "files": [{"id": "F_UNKNOWN"}]}]

    result = _build(replies, file_blocks_by_id={})

    assert result == [{"role": "user", "content": [{"text": "<@U1>: hi"}]}]


# --- harness delta -----------------------------------------------------------


def test_messages_after_last_assistant_are_kept():
    messages: list[Message] = [
        {"role": "user", "content": [{"text": "<@U1>: hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
        {"role": "user", "content": [{"text": "<@U1>: follow-up"}]},
        {"role": "user", "content": [{"text": "<@U2>: me too"}]},
    ]

    assert keep_messages_after_last_assistant(messages) == messages[2:]


def test_conversation_without_assistant_is_kept_whole():
    messages: list[Message] = [
        {"role": "user", "content": [{"text": "<@U1>: hi"}]},
        {"role": "user", "content": [{"text": "<@U2>: hello"}]},
    ]

    assert keep_messages_after_last_assistant(messages) == messages


def test_conversation_ending_with_assistant_keeps_nothing():
    messages: list[Message] = [
        {"role": "user", "content": [{"text": "<@U1>: hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ]

    assert keep_messages_after_last_assistant(messages) == []
