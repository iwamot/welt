from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.slack_markdown_logic import (
    attachments_to_markdown,
    blocks_to_markdown,
    slack_to_markdown,
    unescape_slack_formatting,
)

# What Slack's Markdown renderer made of a message exercising every syntax
# Welt's replies use, captured from conversations_replies (2026-08-28). The
# `text` beside the blocks is the summary Slack wrote, kept here because it
# is what reading a reply the old way got.
RENDERED = json.loads(
    (Path(__file__).parent / "fixtures" / "slack-rendered-markdown.json").read_text()
)


def _blocks_of(*types: str) -> list[dict]:
    return [block for block in RENDERED["blocks"] if block["type"] in types]


# --- the real message --------------------------------------------------------


def test_a_rendered_reply_reads_back_as_the_markdown_it_was_written_in():
    assert (
        blocks_to_markdown(RENDERED["blocks"])
        == """# Heading one

## Heading two

### Heading three

Plain paragraph with **bold**, *italic*, *underscore italic*, ~~strike~~, `inline code`,
a [labelled link](https://example.com), a bare https://example.com/bare URL,
a mention <@U0123456>, a channel <#C0123456>, and an emoji :tada:.

- bullet one
- bullet two
    - nested bullet
        - deeper bullet
- bullet three

1. ordered one
2. ordered two
    1. nested ordered
3. ordered three

> a quote line
> a second quote line

```python
def hello() -> str:
    return "world"
```

```
fence without a language
```

| fruit | count | note |
| --- | --- | --- |
| りんご | 3 | 日本語セル |
| banana | 5 | plain |

---

[alt text](https://example.com/image.png)

Final paragraph."""
    )


def test_what_the_blocks_carry_is_what_slack_left_out_of_the_text():
    # The reason for reading blocks at all: Slack's own summary flattens the
    # headings into the paragraph below them, drops the table entirely, and
    # appends a locale-dependent notice about the widgets.
    summary = RENDERED["text"]
    markdown = blocks_to_markdown(RENDERED["blocks"])

    assert "Heading one Heading two" in summary
    assert "| fruit | count | note |" not in summary
    assert "（インタラクティブ要素あり）" in summary

    assert "# Heading one\n\n## Heading two" in markdown
    assert "| fruit | count | note |" in markdown
    assert "（インタラクティブ要素あり）" not in markdown


def test_a_nested_ordered_list_keeps_counting_where_it_left_off():
    # Slack renders a nested list as a sibling element at a deeper indent, so
    # the item after it continues the outer numbering rather than restarting.
    markdown = blocks_to_markdown(_blocks_of("rich_text"))

    assert "2. ordered two\n    1. nested ordered\n3. ordered three" in markdown


def test_a_list_that_does_not_begin_at_one_is_numbered_from_where_it_does():
    # Slack writes `start` (and an `offset` one less) on a list that does
    # not begin at one (captured 2026-08-29).
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "style": "ordered",
                    "indent": 0,
                    "offset": 4,
                    "start": 5,
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "five"}],
                        },
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "six"}],
                        },
                    ],
                }
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "5. five\n6. six"


@pytest.mark.parametrize(
    "start, expected",
    [(3, "3. one"), (None, "1. one"), (0, "1. one"), (True, "1. one")],
)
def test_where_a_list_begins_is_read_from_what_says_so(start, expected):
    element = {
        "type": "rich_text_list",
        "style": "ordered",
        "elements": [
            {"type": "rich_text_section", "elements": [{"type": "text", "text": "one"}]}
        ],
    }
    if start is not None:
        element["start"] = start

    assert (
        blocks_to_markdown([{"type": "rich_text", "elements": [element]}]) == expected
    )


# --- blocks that carry no text -----------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"type": "actions", "elements": ["not an element", {"type": "button"}]},
        {"type": "actions", "elements": [{"type": "overflow"}]},
        {"type": "actions"},
        {"type": "input", "element": {"type": "plain_text_input"}},
        {"type": "input"},
        {"type": "header", "text": {"type": "plain_text", "text": ""}},
        {"type": "header"},
        {"type": "markdown"},
        {"type": "context"},
        {"type": "context", "elements": [{"type": "plain_text"}, "not a dict"]},
        {"type": "section"},
        {"type": "rich_text"},
        {"type": "table"},
        {"type": "table", "rows": ["not a row", []]},
        {"type": "rich_text", "elements": ["not an element", {"type": "unknown"}]},
        "not a block",
    ],
)
def test_a_block_carrying_no_text_contributes_nothing(block):
    assert blocks_to_markdown([block]) == ""


def test_blocks_that_are_not_a_list_read_as_nothing():
    assert blocks_to_markdown(None) == ""


# --- the widgets a message still offers --------------------------------------


def test_a_question_still_waiting_shows_what_it_offers():
    # Welt's own shape: the options as buttons, the free-text alternative as
    # an input block under its label. Answering removes them, so a widget
    # still in the message is one still waiting.
    blocks = [
        {"type": "markdown", "text": "Deploy to prod?"},
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Publish"}},
                {"type": "button", "text": {"type": "plain_text", "text": "Cancel"}},
            ],
        },
        {
            "type": "input",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "Or say why not"},
            "element": {"type": "plain_text_input", "multiline": False},
        },
    ]

    assert blocks_to_markdown(blocks) == (
        "Deploy to prod?\n\n[buttons: Publish | Cancel]\n\n[input: Or say why not]"
    )


def test_a_widget_that_is_not_a_button_is_named_on_its_own():
    blocks = [
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Go"}},
                {
                    "type": "static_select",
                    "placeholder": {"type": "plain_text", "text": "Pick a branch"},
                },
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "[buttons: Go]\n[menu: Pick a branch]"


def test_an_input_falls_back_to_what_it_prompts_for():
    blocks = [
        {
            "type": "input",
            "element": {
                "type": "plain_text_input",
                "placeholder": {"type": "plain_text", "text": "Type an answer"},
            },
        }
    ]

    assert blocks_to_markdown(blocks) == "[input: Type an answer]"


# --- the tools a reply used --------------------------------------------------

# What Slack draws from the `task_update` chunks Welt streams (captured
# 2026-08-28). Its own title is Slack's, written in the reader's language.
PLAN_BLOCK = {
    "type": "plan",
    "block_id": "plan-id",
    "title": "確認完了",
    "tasks": [
        {
            "task_id": "call_cb258eab53e2550e834e586bab1371a7",
            "title": "Using sample_draft_report",
            "status": "complete",
        }
    ],
}


def test_a_plan_says_which_tools_the_reply_used():
    assert blocks_to_markdown([PLAN_BLOCK]) == "[task: Using sample_draft_report]"


def test_slacks_own_word_for_the_plan_is_not_read():
    assert "確認完了" not in blocks_to_markdown([PLAN_BLOCK])


@pytest.mark.parametrize(
    "status, expected",
    [
        ("complete", "[task: Using a tool]"),
        ("error", "[task: Using a tool — error]"),
        ("in_progress", "[task: Using a tool — in progress]"),
        (None, "[task: Using a tool]"),
    ],
)
def test_a_tool_that_did_not_finish_says_so(status, expected):
    task = {"title": "Using a tool", "status": status}

    assert blocks_to_markdown([{"type": "plan", "tasks": [task]}]) == expected


def test_every_tool_of_a_plan_is_read_in_order():
    tasks = [
        {"title": "Using search", "status": "complete"},
        {"title": "Using publish", "status": "error"},
    ]

    assert blocks_to_markdown([{"type": "plan", "tasks": tasks}]) == (
        "[task: Using search]\n[task: Using publish — error]"
    )


def test_a_tool_note_sits_where_the_thread_shows_it():
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "Looking it up."}],
                }
            ],
        },
        PLAN_BLOCK,
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "Here it is."}],
                }
            ],
        },
    ]

    assert blocks_to_markdown(blocks) == (
        "Looking it up.\n\n[task: Using sample_draft_report]\n\nHere it is."
    )


@pytest.mark.parametrize(
    "tasks", [None, [], ["not a task"], [{"status": "complete"}], [{"title": ""}]]
)
def test_a_plan_naming_no_tool_says_nothing(tasks):
    assert blocks_to_markdown([{"type": "plan", "tasks": tasks}]) == ""


def test_a_task_card_says_the_same_as_a_plans_task():
    # How Slack drew the same task_update chunks before the plan block
    # not begin at one (captured 2026-08-29). one card per task, flat in the block.
    card = {
        "type": "task_card",
        "block_id": "task-tooluse_Uas5QtPTVrR6Dh4kLZ1UWY",
        "task_id": "tooluse_Uas5QtPTVrR6Dh4kLZ1UWY",
        "title": "Using current_time",
        "status": "complete",
    }

    assert blocks_to_markdown([card]) == "[task: Using current_time]"


def test_a_task_card_naming_no_tool_says_nothing():
    assert blocks_to_markdown([{"type": "task_card", "status": "complete"}]) == ""


# --- the blocks Welt builds itself -------------------------------------------


def test_a_question_body_is_read_whether_it_is_posted_or_read_back():
    # An interrupt's question goes up as a markdown block and comes back as
    # rich_text, and both shapes reach this converter.
    posted = {"type": "markdown", "text": "Deploy to **prod**?"}
    read_back = {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [
                    {"type": "text", "text": "Deploy to "},
                    {"type": "text", "text": "prod", "style": {"bold": True}},
                    {"type": "text", "text": "?"},
                ],
            }
        ],
    }

    assert blocks_to_markdown([posted]) == "Deploy to **prod**?"
    assert blocks_to_markdown([read_back]) == "Deploy to **prod**?"


def test_an_answered_question_carries_its_receipt():
    blocks = [
        {"type": "markdown", "text": "Deploy to prod?"},
        {"type": "actions", "elements": [{"type": "button"}]},
        {
            "type": "context",
            "elements": [
                {"type": "plain_text", "text": "“Publish” — answered by iwamot"}
            ],
        },
    ]

    assert blocks_to_markdown(blocks) == (
        "Deploy to prod?\n\n[context: “Publish” — answered by iwamot]"
    )


# --- the blocks other apps post ----------------------------------------------


def test_a_section_carries_its_fields_as_well_as_its_text():
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Build failed*"},
            "fields": [
                {"type": "mrkdwn", "text": "*Branch*\nmain"},
                {"type": "plain_text", "text": "Duration: 4m"},
            ],
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Rerun"},
            },
        }
    ]

    assert blocks_to_markdown(blocks) == (
        "**Build failed**\n**Branch**\nmain\nDuration: 4m\n[buttons: Rerun]"
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("<https://example.com|the docs>", "[the docs](https://example.com)"),
        ("<https://example.com>", "https://example.com"),
        ("<mailto:a@example.com|mail us>", "[mail us](mailto:a@example.com)"),
        ("ask <@U0123> in <#C0123>", "ask <@U0123> in <#C0123>"),
        ("<!here> please", "<!here> please"),
        ("2 &lt; 3", "2 < 3"),
    ],
)
def test_a_mrkdwn_link_is_written_back_as_markdown(text, expected):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]

    assert blocks_to_markdown(blocks) == expected


def test_a_picture_hung_on_a_section_is_shown_as_one():
    # A section's accessory is drawn to its right (captured 2026-08-28).
    block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "A section with a picture"},
        "accessory": {
            "type": "image",
            "image_url": "https://example.com/favicon.png",
            "alt_text": "the favicon",
        },
    }

    assert blocks_to_markdown([block]) == (
        "A section with a picture\n![the favicon](https://example.com/favicon.png)"
    )


def test_a_menu_hung_on_a_section_is_named_as_one():
    block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "Pick one"},
        "accessory": {
            "type": "static_select",
            "placeholder": {"type": "plain_text", "text": "Pick a branch"},
        },
    }

    assert blocks_to_markdown([block]) == "Pick one\n[menu: Pick a branch]"


def test_an_accessory_that_names_nothing_is_left_out():
    block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "Just words"},
        "accessory": {"type": "overflow"},
    }

    assert blocks_to_markdown([block]) == "Just words"


def test_an_attachments_picture_is_shown_by_its_url():
    # An attachment names no alt text for its image (captured 2026-08-28).
    attachment = {
        "title": "With a picture",
        "text": "the body",
        "image_url": "https://example.com/favicon.png",
        "image_width": 512,
    }

    assert attachments_to_markdown([attachment]) == (
        "With a picture\n\nthe body\n\n[image: https://example.com/favicon.png]"
    )


def test_an_attachment_falls_back_to_its_thumbnail():
    assert attachments_to_markdown([{"thumb_url": "https://example.com/t.png"}]) == (
        "[image: https://example.com/t.png]"
    )


def test_an_image_hosted_elsewhere_travels_with_its_url():
    blocks = [
        {
            "type": "image",
            "title": {"type": "plain_text", "text": "Latency"},
            "image_url": "https://example.com/chart.png",
            "alt_text": "p99 latency",
        }
    ]

    assert blocks_to_markdown(blocks) == (
        "Latency\n![p99 latency](https://example.com/chart.png)"
    )


def test_an_image_held_in_slack_is_named_instead():
    blocks = [
        {
            "type": "image",
            "slack_file": {"id": "F1"},
            "alt_text": "the receipt",
        }
    ]

    assert blocks_to_markdown(blocks) == "[image: the receipt]"


def test_an_image_saying_nothing_about_itself_contributes_nothing():
    assert blocks_to_markdown([{"type": "image", "slack_file": {"id": "F1"}}]) == ""


def test_a_video_is_named_linked_and_described():
    blocks = [
        {
            "type": "video",
            "title": {"type": "plain_text", "text": "Release demo"},
            "title_url": "https://example.com/watch",
            "description": {"type": "plain_text", "text": "Five minutes."},
            "alt_text": "demo",
        }
    ]

    assert blocks_to_markdown(blocks) == (
        "[video: Release demo](https://example.com/watch)\nFive minutes."
    )


def test_a_video_saying_only_that_it_is_one_still_says_that():
    assert blocks_to_markdown([{"type": "video"}]) == "[video: video]"


def test_a_file_reference_carries_no_words():
    assert blocks_to_markdown([{"type": "file", "external_id": "F1"}]) == ""


def test_a_question_body_from_before_markdown_blocks_is_still_read():
    # Welt posted a question body as a section block until #97, and threads
    # from then still hold them; reading the blocks reaches those too.
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Deploy to *prod*?"}},
        {"type": "actions", "elements": [{"type": "button"}]},
    ]

    assert blocks_to_markdown(blocks) == "Deploy to **prod**?"


def test_a_plain_text_object_is_read_as_it_stands():
    blocks = [{"type": "section", "text": {"type": "plain_text", "text": "a *b* c"}}]

    assert blocks_to_markdown(blocks) == "a *b* c"


@pytest.mark.parametrize(
    "text_object", [None, {}, {"type": "mrkdwn"}, {"type": "mrkdwn", "text": ""}]
)
def test_a_section_carrying_no_text_contributes_nothing(text_object):
    assert blocks_to_markdown([{"type": "section", "text": text_object}]) == ""


def test_a_fence_running_straight_out_of_a_paragraph_gets_its_own_line():
    # Slack ends the paragraph section without a newline when a fence
    # follows it (observed in a real thread), so the fence has to open one.
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "内容は以下の通りです："}],
                },
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": "fruit,count"}],
                    "language": "csv",
                },
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == (
        "内容は以下の通りです：\n```csv\nfruit,count\n```"
    )


def test_a_list_running_straight_out_of_a_paragraph_gets_its_own_line():
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "Steps:"}],
                },
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "one"}],
                        }
                    ],
                },
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "Steps:\n- one"


def test_a_fence_after_an_empty_paragraph_opens_no_line_of_its_own():
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {"type": "rich_text_section", "elements": []},
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": "code"}],
                },
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "```\ncode\n```"


def test_a_quote_opening_a_block_needs_no_line_of_its_own():
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_quote",
                    "elements": [{"type": "text", "text": "quoted"}],
                }
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "> quoted"


# --- the shapes the real message does not reach ------------------------------


@pytest.mark.parametrize(
    "element, expected",
    [
        ({"type": "user"}, ""),
        ({"type": "usergroup", "usergroup_id": "S123"}, "<!subteam^S123>"),
        ({"type": "broadcast", "range": "here"}, "<!here>"),
        ({"type": "emoji"}, ""),
        ({"type": "date", "timestamp": 1, "fallback": "Aug 28"}, "Aug 28"),
        ({"type": "date", "timestamp": 1}, ""),
        ({"type": "text"}, ""),
        ({"type": "an element this converter has never seen"}, ""),
        ({"type": "text", "text": "  ", "style": {"bold": True}}, "  "),
        ({"type": "text", "text": " hi ", "style": {"bold": True}}, " **hi** "),
        ({"type": "text", "text": "hi", "style": "not a dict"}, "hi"),
        ({"type": "link", "text": "label"}, "label"),
        ({"type": "link"}, ""),
        ({"type": "link", "url": "https://a", "text": "https://a"}, "https://a"),
        (
            {"type": "link", "url": "https://a", "text": "b", "style": {"bold": True}},
            "[**b**](https://a)",
        ),
    ],
)
def test_inline_elements_read_back_as_their_source_form(element, expected):
    blocks = [
        {
            "type": "rich_text",
            "elements": [{"type": "rich_text_section", "elements": [element]}],
        }
    ]

    assert blocks_to_markdown(blocks) == expected


def test_a_heading_deeper_than_markdown_goes_reads_as_a_top_level_one():
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Title"}, "level": 9}
    ]

    assert blocks_to_markdown(blocks) == "# Title"


def test_a_list_item_spanning_lines_stays_under_its_marker():
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "indent": "not a number",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "one\ntwo"}],
                        },
                        "not an item",
                    ],
                }
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "- one\n  two"


def test_a_list_of_an_unknown_style_reads_as_a_bullet_list():
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "one"}],
                        }
                    ],
                }
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "- one"


def test_a_quote_with_nothing_in_it_contributes_nothing():
    blocks = [{"type": "rich_text", "elements": [{"type": "rich_text_quote"}]}]

    assert blocks_to_markdown(blocks) == ""


def test_a_fence_is_read_for_its_characters_and_not_its_styles():
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_preformatted",
                    "elements": [
                        {
                            "type": "text",
                            "text": "**not bold**",
                            "style": {"bold": True},
                        },
                        {"type": "link", "url": "https://a", "text": "b"},
                    ],
                }
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == "```\n**not bold**https://a\n```"


def test_a_table_cell_is_written_so_the_table_can_be_read_back():
    def _cell(text: str) -> dict:
        return {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": text}],
                }
            ],
        }

    blocks = [
        {
            "type": "table",
            "rows": [
                [_cell("a"), _cell("b")],
                [_cell("one\ntwo"), "not a cell"],
            ],
        }
    ]

    assert blocks_to_markdown(blocks) == ("| a | b |\n| --- | --- |\n| one two |  |")


def test_a_pipe_in_a_cell_is_escaped():
    cell = {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [{"type": "text", "text": "a|b"}],
            }
        ],
    }

    assert blocks_to_markdown([{"type": "table", "rows": [[cell]]}]) == (
        "| a\\|b |\n| --- |"
    )


# --- attachments -------------------------------------------------------------

# What a GitHub workflow notification hangs off a message that has no blocks
# and no text of its own (captured 2026-08-28).
WORKFLOW_ATTACHMENT = {
    "fallback": "[iwamot/actions] Workflow triggered by <https://github.com/apps/renovate|renovate[bot]>",
    "pretext": "Workflow was triggered via pull_request by <https://github.com/apps/renovate|renovate[bot]>",
    "title": "<https://github.com/iwamot/actions/actions/runs/1|Validate #342>",
    "footer": "iwamot/actions",
    "color": "28a745",
    "fields": [
        {"title": "*Status*", "value": ":white_check_mark: Success", "short": True},
        {"title": "*Duration*", "value": "23s", "short": True},
    ],
    "actions": [{"type": "button", "text": "Re-run all jobs", "value": "{}"}],
    "mrkdwn_in": ["text"],
}


def test_a_workflow_notification_reads_as_what_it_shows():
    assert attachments_to_markdown([WORKFLOW_ATTACHMENT]) == (
        "Workflow was triggered via pull_request by "
        "[renovate[bot]](https://github.com/apps/renovate)\n"
        "\n"
        "[Validate #342](https://github.com/iwamot/actions/actions/runs/1)\n"
        "\n"
        "**Status**\n"
        ":white_check_mark: Success\n"
        "\n"
        "**Duration**\n"
        "23s\n"
        "\n"
        "iwamot/actions\n"
        "\n"
        "[buttons: Re-run all jobs]"
    )


def test_a_title_that_is_not_a_link_already_takes_the_one_given_for_it():
    attachment = {
        "author_name": "renovate",
        "author_link": "https://github.com/apps/renovate",
        "title": "Validate #342",
        "title_link": "https://github.com/iwamot/actions/actions/runs/1",
        "text": "It passed.",
    }

    assert attachments_to_markdown([attachment]) == (
        "[renovate](https://github.com/apps/renovate)\n"
        "\n"
        "[Validate #342](https://github.com/iwamot/actions/actions/runs/1)\n"
        "\n"
        "It passed."
    )


def test_an_attachment_showing_nothing_falls_back_to_what_it_says_it_shows():
    assert attachments_to_markdown([{"fallback": "Build failed"}]) == "Build failed"


def test_two_attachments_are_read_in_order():
    assert (
        attachments_to_markdown([{"text": "first"}, {"text": "second"}])
        == "first\n\nsecond"
    )


@pytest.mark.parametrize(
    "attachments",
    [None, [], ["not an attachment"], [{}], [{"color": "28a745"}], [{"title": ""}]],
)
def test_attachments_carrying_nothing_contribute_nothing(attachments):
    assert attachments_to_markdown(attachments) == ""


@pytest.mark.parametrize(
    "fields, expected",
    [
        ("not a list", ""),
        ([{"value": "3"}], "3"),
        ([{"title": "*Count*"}], "**Count**"),
        (["not a field", {}], ""),
    ],
)
def test_a_fields_list_is_read_for_whatever_its_fields_carry(fields, expected):
    assert attachments_to_markdown([{"fields": fields}]) == expected


# --- mrkdwn strings ----------------------------------------------------------


@pytest.mark.parametrize(
    "content, expected",
    [
        (
            """#include &lt;stdio.h&gt;
int main(int argc, char *argv[])
{
    printf("Hello, world!\n");
    return 0;
}""",
            """#include <stdio.h>
int main(int argc, char *argv[])
{
    printf("Hello, world!\n");
    return 0;
}""",
        ),
    ],
)
def test_unescape_slack_formatting(content, expected):
    result = unescape_slack_formatting(content)
    assert result == expected


@pytest.mark.parametrize(
    "content, expected",
    [
        (
            "Sentence with *bold text*, _italic text_ and ~strikethrough text~.",
            "Sentence with **bold text**, *italic text* and ~~strikethrough text~~.",
        ),
        (
            "Sentence with _*bold and italic text*_ and *_bold and italic text_*.",
            "Sentence with ***bold and italic text*** and ***bold and italic text***.",
        ),
        (
            "Code block ```*text*, _text_ and ~text~``` shouldn't be changed.",
            "Code block ```*text*, _text_ and ~text~``` shouldn't be changed.",
        ),
        (
            "Inline code `*text*, _text_ and ~text~` shouldn't be changed.",
            "Inline code `*text*, _text_ and ~text~` shouldn't be changed.",
        ),
        (
            "```Some `*bold text* inside inline code` inside a code block``` shouldn't be changed.",
            "```Some `*bold text* inside inline code` inside a code block``` shouldn't be changed.",
        ),
        (
            "* bullets shouldn't\n* be changed",
            "* bullets shouldn't\n* be changed",
        ),
        (
            "* not bold*, *not bold *, * not bold *, **, * *, *  *, *   *",
            "* not bold*, *not bold *, * not bold *, **, * *, *  *, *   *",
        ),
        (
            "_ not italic_, _not italic _, _ not italic _, __, _ _, _  _, _   _",
            "_ not italic_, _not italic _, _ not italic _, __, _ _, _  _, _   _",
        ),
        (
            "~ not strikethrough~, ~not strikethrough ~, ~ not strikethrough ~, ~~, ~ ~, ~  ~, ~   ~",
            "~ not strikethrough~, ~not strikethrough ~, ~ not strikethrough ~, ~~, ~ ~, ~  ~, ~   ~",
        ),
        (
            """The following multiline code block shouldn't be translated:
```
if 4*q + r - t < n*t:
    q, r, t, k, n, l = 10*q, 10*(r-n*t), t, k, (10*(3*q+r))//t - 10*n, l
else:
    q, r, t, k, n, l = q*l, (2*q+r)*l, t*l, k+1, (q*(7*k+2)+r*l)//(t*l), l+2
```""",
            """The following multiline code block shouldn't be translated:
```
if 4*q + r - t < n*t:
    q, r, t, k, n, l = 10*q, 10*(r-n*t), t, k, (10*(3*q+r))//t - 10*n, l
else:
    q, r, t, k, n, l = q*l, (2*q+r)*l, t*l, k+1, (q*(7*k+2)+r*l)//(t*l), l+2
```""",
        ),
        (
            "snake_case_names and dunders like __init__ shouldn't be changed",
            "snake_case_names and dunders like __init__ shouldn't be changed",
        ),
        (
            "bare arithmetic 2*3*4 and globs src/*.py shouldn't be changed",
            "bare arithmetic 2*3*4 and globs src/*.py shouldn't be changed",
        ),
        (
            "mid-word markers foo*bar*baz and approx~1~2 shouldn't be changed",
            "mid-word markers foo*bar*baz and approx~1~2 shouldn't be changed",
        ),
        (
            "punctuation-adjacent (*bold*) and *bold*. should be changed",
            "punctuation-adjacent (**bold**) and **bold**. should be changed",
        ),
        (
            "CJK-adjacent これは*太字*と_斜体_と~取り消し~です should be changed",
            "CJK-adjacent これは**太字**と*斜体*と~~取り消し~~です should be changed",
        ),
        (
            "already-Markdown **bold** shouldn't be changed",
            "already-Markdown **bold** shouldn't be changed",
        ),
    ],
)
def test_slack_to_markdown(content, expected):
    result = slack_to_markdown(content)

    assert result == expected
