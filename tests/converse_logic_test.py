from __future__ import annotations

import pytest

from app.converse_logic import (
    DOCUMENT_NAME_MAX_LENGTH,
    DocumentBlock,
    Message,
    build_document_block,
    build_image_block,
    build_messages,
    build_video_block,
    collect_user_ids,
    keep_messages_after_last_assistant,
    keep_replies_after_last_bot_reply,
    sanitize_document_name,
)


def _build(replies: list[dict], **overrides: object) -> list[Message]:
    kwargs: dict = {"bot_user_id": "U0WELT"}
    kwargs.update(overrides)
    return build_messages(replies, **kwargs)


def test_user_message_is_prefixed_and_role_user():
    result = _build([{"user": "U0AAA", "text": "hello"}])

    assert result == [{"role": "user", "content": [{"text": "@U0AAA: hello"}]}]


def test_bot_message_is_assistant_and_not_prefixed():
    replies = [
        {"user": "U0AAA", "text": "hi"},
        {"user": "U0WELT", "text": "hello there"},
        {"user": "U0AAA", "text": "thanks"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: hi"}]},
        {"role": "assistant", "content": [{"text": "hello there"}]},
        {"role": "user", "content": [{"text": "@U0AAA: thanks"}]},
    ]


def test_trailing_bot_replies_are_dropped():
    replies = [
        {"user": "U0AAA", "text": "question"},
        {"user": "U0WELT", "text": "partial answer"},
        {"user": "U0WELT", "text": "loading ..."},
    ]

    result = _build(replies)

    assert result == [{"role": "user", "content": [{"text": "@U0AAA: question"}]}]


def test_leading_bot_replies_are_dropped():
    # An overlong thread truncated to its newest replies can open with the
    # bot's own replies; Converse requires the conversation to start with a
    # user message.
    replies = [
        {"user": "U0WELT", "text": "old answer"},
        {"user": "U0AAA", "text": "follow-up"},
        {"user": "U0WELT", "text": "answer"},
        {"user": "U0AAA", "text": "question"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: follow-up"}]},
        {"role": "assistant", "content": [{"text": "answer"}]},
        {"role": "user", "content": [{"text": "@U0AAA: question"}]},
    ]


def test_a_mention_only_reply_is_the_mention_it_was():
    replies = [
        {"user": "U0AAA", "text": "<@U0WELT>"},
        {"user": "U0AAA", "text": "real message"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: @U0WELT"}]},
        {"role": "user", "content": [{"text": "@U0AAA: real message"}]},
    ]


def test_mention_only_opener_keeps_conversation_starting_with_user():
    # A thread that opens with a mention-only call and the bot's answer must
    # not start with an assistant message (Converse rejects that), so the
    # opener has to survive as a user message.
    replies = [
        {"user": "U0AAA", "text": "<@U0WELT>"},
        {"user": "U0WELT", "text": "how can I help?"},
        {"user": "U0AAA", "text": "hello"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: @U0WELT"}]},
        {"role": "assistant", "content": [{"text": "how can I help?"}]},
        {"role": "user", "content": [{"text": "@U0AAA: hello"}]},
    ]


def test_non_string_text_becomes_prefix_only_user_message():
    replies = [
        {"user": "U0AAA", "text": 123},
        {"user": "U0AAA", "text": "ok"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: "}]},
        {"role": "user", "content": [{"text": "@U0AAA: ok"}]},
    ]


def test_the_mention_stays_and_mrkdwn_is_converted():
    result = _build([{"user": "U0AAA", "text": "<@U0WELT> *bold*"}])

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: @U0WELT **bold**"}]}
    ]


def test_slack_formatting_unescaped():
    result = _build([{"user": "U0AAA", "text": "a &lt;b&gt; c"}])

    assert result == [{"role": "user", "content": [{"text": "@U0AAA: a <b> c"}]}]


def test_bot_user_id_none_treats_all_as_user_and_drops_nothing():
    replies = [
        {"user": "U0AAA", "text": "a"},
        {"user": "U0WELT", "text": "b"},
    ]

    result = _build(replies, bot_user_id=None)

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: a"}]},
        {"role": "user", "content": [{"text": "@U0WELT: b"}]},
    ]


def test_empty_replies():
    assert _build([]) == []


def test_bot_reply_with_empty_text_in_middle_is_skipped():
    replies = [
        {"user": "U0AAA", "text": "question"},
        {"user": "U0WELT", "text": ""},
        {"user": "U0AAA", "text": "still there?"},
    ]

    result = _build(replies)

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: question"}]},
        {"role": "user", "content": [{"text": "@U0AAA: still there?"}]},
    ]


# --- what Welt's own replies say ---------------------------------------------


def _section(text: str) -> dict:
    return {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [{"type": "text", "text": text}],
            }
        ],
    }


def test_welts_reply_is_read_from_its_blocks_and_not_from_slacks_summary():
    # Slack writes the `text` beside a rendered reply itself: the heading
    # runs into the paragraph below it and the table is gone. The blocks are
    # what Welt streamed.
    reply = {
        "user": "U0WELT",
        "text": "Results Here they are:",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Results"}},
            _section("Here they are:"),
            {
                "type": "table",
                "rows": [[_section("fruit")], [_section("apple")]],
            },
        ],
    }

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [
            {"text": ("# Results\n\nHere they are:\n\n| fruit |\n| --- |\n| apple |")}
        ],
    }


def test_a_reply_that_only_asked_a_question_says_what_it_asked():
    # The message carries the question and, once answered, the receipt; the
    # widgets between them carry no text. Its own `text` is the fixed summary
    # Slack shows in a notification.
    reply = {
        "user": "U0WELT",
        "text": "The agent needs your decision to continue.",
        "blocks": [
            {"type": "markdown", "text": "Deploy to prod?"},
            {"type": "actions", "elements": [{"type": "button"}]},
            {
                "type": "context",
                "elements": [
                    {"type": "plain_text", "text": "“Publish” — answered by iwamot"}
                ],
            },
        ],
    }

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [
            {"text": "Deploy to prod?\n\n[context: “Publish” — answered by iwamot]"}
        ],
    }


def test_a_reply_that_only_used_a_tool_says_which_one():
    # Calling a tool without saying anything first leaves a message whose
    # only block is the plan, and whose `text` is the notice Slack writes in
    # the reader's own language.
    reply = {
        "user": "U0WELT",
        "text": "このメッセージにはインタラクティブ要素が含まれます。",
        "blocks": [
            {
                "type": "plan",
                "title": "確認完了",
                "tasks": [{"title": "Using sample_draft_report", "status": "complete"}],
            }
        ],
    }

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [{"text": "[task: Using sample_draft_report]"}],
    }


def test_another_apps_text_stands_in_where_its_blocks_carry_none():
    # An app writes its own `text` beside its blocks, so a message drawn as
    # a widget and nothing else still says what it is for.
    reply = {
        "user": "U0CI",
        "bot_id": "B_CI",
        "text": "Approve this deploy?",
        "blocks": [{"type": "actions", "elements": [{"type": "button"}]}],
    }

    assert _build([reply]) == [
        {"role": "user", "content": [{"text": "@U0CI: Approve this deploy?"}]}
    ]


def test_a_persons_text_stands_in_where_a_block_reads_as_nothing():
    # A shape this converter reads nothing from — a block type Slack adds
    # later, say — must not take the person's words down with it.
    reply = {
        "user": "U0AAA",
        "text": "look at this",
        "blocks": [{"type": "a block type from after this was written"}],
    }

    assert _build([reply]) == [
        {"role": "user", "content": [{"text": "@U0AAA: look at this"}]}
    ]


def test_a_reply_posted_as_plain_text_is_read_from_its_text():
    # The failure notice goes up with `text` and no blocks of its own.
    reply = {"user": "U0WELT", "text": ":warning: Failed to reply. Please try again."}

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [{"text": ":warning: Failed to reply. Please try again."}],
    }


def test_a_file_welt_sent_is_named_in_the_history():
    # A files_upload_v2 message carries neither text nor blocks, so without
    # this the agent would not know on its next turn that it had sent a file.
    reply = {
        "user": "U0WELT",
        "text": "",
        "files": [{"id": "F1", "name": "chart.png"}, {"id": "F2", "title": "notes.md"}],
    }

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [{"text": "[file: chart.png]\n[file: notes.md]"}],
    }


def test_a_file_sent_with_a_reply_is_named_after_what_the_reply_said():
    reply = {
        "user": "U0WELT",
        "blocks": [_section("Here is the chart.")],
        "files": [{"id": "F1", "name": "chart.png"}],
    }

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [{"text": "Here is the chart.\n\n[file: chart.png]"}],
    }


def test_an_attachment_slack_names_in_neither_field_is_left_out():
    reply = {
        "user": "U0WELT",
        "files": [{"id": "F1"}, "not a file", {"id": "F2", "name": "kept.png"}],
    }

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [{"text": "[file: kept.png]"}],
    }


def test_a_reply_whose_files_key_is_not_a_list_is_read_for_its_words_alone():
    reply = {"user": "U0WELT", "blocks": [_section("said something")], "files": "no"}

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [{"text": "said something"}],
    }


def test_welts_own_attachments_are_read_as_well():
    # Welt is an app like any other: what it hangs off a message is part of
    # what the message shows.
    reply = {
        "user": "U0WELT",
        "text": "",
        "attachments": [{"title": "A title", "text": "the body"}],
    }

    result = _build(
        [{"user": "U0AAA", "text": "ask"}, reply, {"user": "U0AAA", "text": "ok"}]
    )

    assert result[1] == {
        "role": "assistant",
        "content": [{"text": "A title\n\nthe body"}],
    }


# --- what another app's posts say --------------------------------------------


def test_another_app_is_read_from_its_blocks_behind_its_mention():
    # An app's `text` is the fallback it chose, often thinner than what it
    # drew and sometimes empty; its blocks are the message.
    reply = {
        "user": "U0CI",
        "bot_id": "B_CI",
        "text": "Build failed",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Build failed* on <@U0WELT>'s PR"},
                "fields": [{"type": "mrkdwn", "text": "<https://ci.example.com|logs>"}],
            }
        ],
    }

    assert _build([reply]) == [
        {
            "role": "user",
            "content": [
                {
                    "text": (
                        "@U0CI: **Build failed** on @U0WELT's PR\n"
                        "[logs](https://ci.example.com)"
                    )
                }
            ],
        }
    ]


def test_a_file_another_app_uploaded_is_named():
    # Bot attachments are never downloaded, so naming it is all the model
    # can be told about it.
    reply = {
        "user": "U0CI",
        "bot_id": "B_CI",
        "files": [{"id": "F1", "name": "build.log"}],
    }

    assert _build([reply]) == [
        {
            "role": "user",
            "content": [{"text": "@U0CI: [file: build.log]"}],
        }
    ]


def test_another_app_posting_plain_text_is_read_from_it():
    reply = {"user": "U0CI", "bot_id": "B_CI", "text": "deploy *finished*"}

    assert _build([reply]) == [
        {"role": "user", "content": [{"text": "@U0CI: deploy **finished**"}]}
    ]


def test_another_app_saying_nothing_still_says_who_posted():
    reply = {
        "user": "U0CI",
        "bot_id": "B_CI",
        "text": "",
        "blocks": [{"type": "input"}],
    }

    assert _build([reply]) == [{"role": "user", "content": [{"text": "@U0CI: "}]}]


def test_an_unfurl_under_a_persons_message_is_read_like_any_other():
    # Slack hangs its own preview of a link under the message, and the
    # thread shows it as surely as it shows what they typed.
    reply = {
        "user": "U0AAA",
        "text": "see https://example.com",
        "attachments": [
            {"title": "Example Domain", "text": "This domain is for use in..."}
        ],
    }

    assert _build([reply]) == [
        {
            "role": "user",
            "content": [
                {
                    "text": (
                        "@U0AAA: see https://example.com\n\n"
                        "Example Domain\n\nThis domain is for use in..."
                    )
                }
            ],
        }
    ]


def test_another_apps_attachments_are_read_behind_its_mention():
    reply = {
        "user": "U0CI",
        "bot_id": "B_CI",
        "text": "",
        "attachments": [{"fallback": "Build failed"}],
    }

    assert _build([reply]) == [
        {"role": "user", "content": [{"text": "@U0CI: Build failed"}]}
    ]


def test_a_person_is_read_from_their_blocks_too():
    # Slack renders what a person composes into blocks, and those carry the
    # structure their `text` loses — a fence stays a fence, and a URL they
    # pasted arrives as a URL rather than in Slack's angle brackets.
    reply = {
        "user": "U0AAA",
        "text": "see <https://example.com>\n```\nls -la\n```",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "text", "text": "see "},
                            {
                                "type": "link",
                                "url": "https://example.com",
                                "text": "example.com",
                                "truncated": True,
                            },
                            {"type": "text", "text": "\n"},
                        ],
                    },
                    {
                        "type": "rich_text_preformatted",
                        "elements": [{"type": "text", "text": "ls -la"}],
                    },
                ],
            }
        ],
    }

    assert _build([reply]) == [
        {
            "role": "user",
            "content": [{"text": "@U0AAA: see https://example.com\n```\nls -la\n```"}],
        }
    ]


def test_a_person_posting_plain_text_is_read_from_it():
    reply = {"user": "U0AAA", "text": "*bold* please"}

    assert _build([reply]) == [
        {"role": "user", "content": [{"text": "@U0AAA: **bold** please"}]}
    ]


def test_a_person_calling_welt_is_read_as_having_called_it():
    reply = {
        "user": "U0AAA",
        "text": "<@U0WELT> hello",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "user", "user_id": "U0WELT"},
                            {"type": "text", "text": " hello"},
                        ],
                    }
                ],
            }
        ],
    }

    # A thread whose parent called Welt runs on every reply, so the mention
    # is the only thing that says which of them were addressed to it.
    assert _build([reply]) == [
        {"role": "user", "content": [{"text": "@U0AAA: @U0WELT hello"}]}
    ]


def test_a_file_a_person_attached_is_named_beside_the_bytes():
    # An image reaches the model as the picture and nothing else; the thread
    # shows a name over it, and so does the turn.
    reply = {"user": "U0AAA", "text": "look", "files": [{"id": "F1", "name": "a.png"}]}
    block = build_image_block(image_format="png", data_base64="AAA")

    assert _build([reply], file_blocks_by_id={"F1": block}) == [
        {
            "role": "user",
            "content": [{"text": "@U0AAA: look\n\n[file: a.png]"}, block],
        }
    ]


def test_a_file_nobody_could_read_is_named_all_the_same():
    # File input off, an unsupported kind, one too large, one the payload
    # budget dropped: the thread still shows it, so the turn still names it.
    reply = {"user": "U0AAA", "text": "look", "files": [{"id": "F1", "name": "a.zip"}]}

    assert _build([reply]) == [
        {"role": "user", "content": [{"text": "@U0AAA: look\n\n[file: a.zip]"}]}
    ]


def test_a_file_given_a_title_of_its_own_shows_both():
    # The thread shows the title; the name is what it downloads as.
    reply = {
        "user": "U0AAA",
        "text": "look",
        "files": [
            {"id": "F1", "name": "sample.csv", "title": "Weekly report"},
            {"id": "F2", "name": "plain.csv", "title": "plain.csv"},
            {"id": "F3", "title": "Named by nothing else"},
        ],
    }

    assert _build([reply]) == [
        {
            "role": "user",
            "content": [
                {
                    "text": (
                        "@U0AAA: look\n\n"
                        "[file: Weekly report (sample.csv)]\n"
                        "[file: plain.csv]\n"
                        "[file: Named by nothing else]"
                    )
                }
            ],
        }
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


def test_sanitize_document_name_truncates_to_the_length_converse_accepts():
    assert sanitize_document_name("a" * 250) == "a" * DOCUMENT_NAME_MAX_LENGTH
    assert sanitize_document_name(f"{'a' * 199} bc") == "a" * 199


def test_file_blocks_attached_documents_before_text_media_after():
    image_block = build_image_block(image_format="png", data_base64="IMG")
    video_block = build_video_block(video_format="mp4", data_base64="VID")
    document_block = build_document_block(
        document_format="pdf", name="doc", data_base64="DOC"
    )
    replies = [
        {
            "user": "U0AAA",
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
                {"text": "@U0AAA: see attached"},
                image_block,
                video_block,
            ],
        }
    ]


def test_document_reply_without_text_still_carries_a_text_block():
    # Converse refuses a message that holds a document and no text ("A text
    # block must be included when using documents"), so the attribution
    # prefix has to survive even a reply that said nothing at all.
    document_block = build_document_block(
        document_format="pdf", name="doc", data_base64="DOC"
    )
    replies = [{"user": "U0AAA", "text": "", "files": [{"id": "F_DOC"}]}]

    result = _build(replies, file_blocks_by_id={"F_DOC": document_block})

    assert result == [
        {"role": "user", "content": [document_block, {"text": "@U0AAA: "}]}
    ]


def test_reply_with_file_but_empty_text_is_kept():
    image_block = build_image_block(image_format="png", data_base64="IMG")
    replies = [{"user": "U0AAA", "text": "<@U0WELT>", "files": [{"id": "F1"}]}]

    result = _build(replies, file_blocks_by_id={"F1": image_block})

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: @U0WELT"}, image_block]}
    ]


def test_malformed_file_entries_are_ignored():
    image_block = build_image_block(image_format="png", data_base64="IMG")
    replies = [
        {
            "user": "U0AAA",
            "text": "hi",
            "files": ["not-a-dict", {"id": 123}, {"id": "F1"}],
        }
    ]

    result = _build(replies, file_blocks_by_id={"F1": image_block})

    assert result == [
        {"role": "user", "content": [{"text": "@U0AAA: hi"}, image_block]}
    ]


def test_files_not_fetched_are_ignored():
    replies = [{"user": "U0AAA", "text": "hi", "files": [{"id": "F_UNKNOWN"}]}]

    result = _build(replies, file_blocks_by_id={})

    assert result == [{"role": "user", "content": [{"text": "@U0AAA: hi"}]}]


def _document(name: str) -> DocumentBlock:
    return build_document_block(document_format="pdf", name=name, data_base64="DOC")


def test_documents_sharing_a_name_are_renamed_apart():
    document_block = _document("report")
    replies = [
        {"user": "U0AAA", "text": "one", "files": [{"id": "F1"}]},
        {"user": "U0AAA", "text": "two", "files": [{"id": "F2"}]},
        {"user": "U0BBB", "text": "three", "files": [{"id": "F3"}]},
    ]

    result = _build(
        replies,
        file_blocks_by_id={
            "F1": document_block,
            "F2": document_block,
            "F3": document_block,
        },
    )

    assert result == [
        {"role": "user", "content": [document_block, {"text": "@U0AAA: one"}]},
        {
            "role": "user",
            "content": [_document("report (2)"), {"text": "@U0AAA: two"}],
        },
        {
            "role": "user",
            "content": [_document("report (3)"), {"text": "@U0BBB: three"}],
        },
    ]
    # The renaming leaves the block it was given untouched.
    assert document_block == _document("report")


def test_documents_with_distinct_names_are_left_alone():
    one, two = _document("one"), _document("two")
    replies = [{"user": "U0AAA", "text": "hi", "files": [{"id": "F1"}, {"id": "F2"}]}]

    result = _build(replies, file_blocks_by_id={"F1": one, "F2": two})

    assert result == [{"role": "user", "content": [one, two, {"text": "@U0AAA: hi"}]}]


def test_renamed_document_name_stays_within_the_length_converse_accepts():
    long_name = "a" * DOCUMENT_NAME_MAX_LENGTH
    document_block = _document(long_name)
    replies = [{"user": "U0AAA", "text": "hi", "files": [{"id": "F1"}, {"id": "F2"}]}]

    result = _build(
        replies, file_blocks_by_id={"F1": document_block, "F2": document_block}
    )

    renamed = _document(f"{'a' * (DOCUMENT_NAME_MAX_LENGTH - 4)} (2)")
    assert result == [
        {
            "role": "user",
            "content": [document_block, renamed, {"text": "@U0AAA: hi"}],
        }
    ]
    assert len(renamed["document"]["name"]) == DOCUMENT_NAME_MAX_LENGTH


# --- harness delta -----------------------------------------------------------


def test_messages_after_last_assistant_are_kept():
    messages: list[Message] = [
        {"role": "user", "content": [{"text": "@U0AAA: hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
        {"role": "user", "content": [{"text": "@U0AAA: follow-up"}]},
        {"role": "user", "content": [{"text": "@U0BBB: me too"}]},
    ]

    assert keep_messages_after_last_assistant(messages) == messages[2:]


def test_conversation_without_assistant_is_kept_whole():
    messages: list[Message] = [
        {"role": "user", "content": [{"text": "@U0AAA: hi"}]},
        {"role": "user", "content": [{"text": "@U0BBB: hello"}]},
    ]

    assert keep_messages_after_last_assistant(messages) == messages


def test_conversation_ending_with_assistant_keeps_nothing():
    messages: list[Message] = [
        {"role": "user", "content": [{"text": "@U0AAA: hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ]

    assert keep_messages_after_last_assistant(messages) == []


# --- keep_replies_after_last_bot_reply ---------------------------------------


def _kept(replies: list[dict]) -> list[str]:
    return [
        r["text"]
        for r in keep_replies_after_last_bot_reply(replies, bot_user_id="U0WELT")
    ]


def test_replies_after_welts_last_reply_are_kept():
    replies = [
        {"user": "U0AAA", "text": "first"},
        {"user": "U0WELT", "text": "answer"},
        {"user": "U0AAA", "text": "second"},
        {"user": "U0BBB", "text": "third"},
    ]

    assert _kept(replies) == ["second", "third"]


def test_a_thread_welt_has_not_answered_keeps_everything():
    replies = [{"user": "U0AAA", "text": "first"}, {"user": "U0BBB", "text": "second"}]

    assert _kept(replies) == ["first", "second"]


def test_an_empty_bot_reply_is_not_the_cut():
    # build_messages skips a bot reply with no text, so it never becomes an
    # assistant message and must not be read as one here either. It stays in
    # the kept replies, where build_messages skips it as it always would.
    replies = [
        {"user": "U0AAA", "text": "first"},
        {"user": "U0WELT", "text": "answer"},
        {"user": "U0AAA", "text": "second"},
        {"user": "U0WELT", "text": "   "},
        {"user": "U0AAA", "text": "third"},
    ]

    assert _kept(replies) == ["second", "   ", "third"]
    assert _build(keep_replies_after_last_bot_reply(replies, bot_user_id="U0WELT")) == (
        keep_messages_after_last_assistant(_build(replies))
    )


def test_a_turn_that_only_sent_a_file_is_the_cut():
    # The file message says something now, so the agent that kept the earlier
    # turns is not sent them again.
    replies = [
        {"user": "U0AAA", "text": "draw it"},
        {"user": "U0WELT", "text": "", "files": [{"id": "F1", "name": "chart.png"}]},
        {"user": "U0AAA", "text": "thanks"},
    ]

    assert _kept(replies) == ["thanks"]
    assert _build(keep_replies_after_last_bot_reply(replies, bot_user_id="U0WELT")) == (
        keep_messages_after_last_assistant(_build(replies))
    )


def test_a_turn_that_said_nothing_at_all_is_not_the_cut():
    # It contributes no assistant message, so it cannot be where the history
    # the agent already holds ends.
    replies = [
        {"user": "U0AAA", "text": "first"},
        {"user": "U0WELT", "text": "answer"},
        {"user": "U0AAA", "text": "second"},
        {"user": "U0WELT", "text": "", "blocks": [{"type": "file"}]},
        {"user": "U0AAA", "text": "third"},
    ]

    assert _kept(replies)[0] == "second"
    assert _build(keep_replies_after_last_bot_reply(replies, bot_user_id="U0WELT")) == (
        keep_messages_after_last_assistant(_build(replies))
    )


def test_the_cut_agrees_with_the_message_level_trim():
    replies = [
        {"user": "U0AAA", "text": "first"},
        {"user": "U0WELT", "text": "answer"},
        {"user": "U0AAA", "text": "second"},
    ]

    by_reply = keep_replies_after_last_bot_reply(replies, bot_user_id="U0WELT")
    by_message = keep_messages_after_last_assistant(_build(replies))

    assert _build(by_reply) == by_message


# --- names ------------------------------------------------------------------


def test_a_thread_is_read_for_who_it_mentions():
    replies = [
        {"user": "U0AAA", "text": "<@U0WELT> ask <@W0CCC> too"},
        {"user": "U0BBB", "text": "no mentions here"},
    ]

    assert collect_user_ids(replies, bot_user_id="U0WELT") == {
        "U0AAA",
        "U0BBB",
        "U0WELT",
        "W0CCC",
    }


def test_the_files_in_a_reply_mention_nobody():
    replies = [
        {"user": "U0AAA", "text": "look", "files": [{"id": "F1", "name": "a.png"}]}
    ]

    assert collect_user_ids(replies, bot_user_id="U0WELT") == {"U0AAA"}


def test_a_mention_that_is_not_an_id_is_not_read_as_one():
    # A message from a webhook is prefixed with the name it posted under.
    assert (
        collect_user_ids(
            [{"username": "some-webhook", "text": "hi"}], bot_user_id="U0WELT"
        )
        == set()
    )


def test_everyone_is_said_by_name():
    result = _build(
        [{"user": "U0AAA", "text": "<@U0WELT> ping <@U0BBB>"}],
        bot_user_id="U0WELT",
        display_names={"U0AAA": "iwamot", "U0WELT": "Welt", "U0BBB": "taro"},
    )

    assert result == [
        {"role": "user", "content": [{"text": "@iwamot: @Welt ping @taro"}]}
    ]


def test_an_id_with_no_name_is_written_as_the_id():
    result = _build(
        [{"user": "U0AAA", "text": "hi <@U0BBB>"}], display_names={"U0AAA": "iwamot"}
    )

    assert result == [{"role": "user", "content": [{"text": "@iwamot: hi @U0BBB"}]}]


@pytest.mark.parametrize(
    "written, expected",
    [
        ("<@U0BBB>", "@taro"),
        # The labelled form an older client wrote is left as it came.
        ("<@U0BBB|handle>", "<@U0BBB|handle>"),
        # Each of these says on its own what it points at, so each is left
        # as it came.
        ("<!here>", "<!here>"),
        ("<!channel>", "<!channel>"),
        ("<!subteam^S0123>", "<!subteam^S0123>"),
        ("<#C0123>", "<#C0123>"),
    ],
)
def test_only_a_person_is_written_as_a_name(written, expected):
    # An `<@U0123>` an agent copied out of the history would post as a real
    # mention and notify somebody, and says nothing to a model besides.
    result = _build(
        [{"user": "U0AAA", "text": f"see {written}"}],
        display_names={"U0BBB": "taro"},
    )

    assert result == [
        {"role": "user", "content": [{"text": f"@U0AAA: see {expected}"}]}
    ]


def test_a_named_speakers_files_are_left_alone():
    reply = {"user": "U0AAA", "text": "look", "files": [{"id": "F1", "name": "a.png"}]}
    block = build_image_block(image_format="png", data_base64="AAA")

    result = _build(
        [reply], file_blocks_by_id={"F1": block}, display_names={"U0AAA": "iwamot"}
    )

    assert result == [
        {
            "role": "user",
            "content": [{"text": "@iwamot: look\n\n[file: a.png]"}, block],
        }
    ]
