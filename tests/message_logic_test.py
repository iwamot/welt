from __future__ import annotations

import pytest

from app.message_logic import (
    build_slack_user_prefixed_text,
    build_tool_use_task_chunk,
    remove_bot_mention,
    slack_to_markdown,
    unescape_slack_formatting,
)


@pytest.mark.parametrize(
    "text, bot_user_id, expected",
    [
        ("<@U12345> hello", "U12345", "hello"),
        ("<@U12345>hello", "U12345", "hello"),
        ("<@U12345>    hello", "U12345", "hello"),
        ("hello", "U12345", "hello"),
        ("<@U67890> hello", "U12345", "<@U67890> hello"),
        ("<@None> hello", None, "<@None> hello"),
    ],
)
def test_remove_bot_mention(text, bot_user_id, expected):
    assert remove_bot_mention(text, bot_user_id) == expected


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


@pytest.mark.parametrize(
    "reply, text, expected",
    [
        ({"user": "U123"}, "hello", "<@U123>: hello"),
        ({"username": "someone"}, "hi", "<@someone>: hi"),
        ({}, "yo", "<@None>: yo"),
    ],
)
def test_build_slack_user_prefixed_text(reply, text, expected):
    result = build_slack_user_prefixed_text(reply, text)

    assert result == expected


# --- build_tool_use_task_chunk -----------------------------------------------


def test_task_chunk_with_name_and_id():
    result = build_tool_use_task_chunk(
        tool_use_id="tooluse_abc", tool_name="get_weather", status="in_progress"
    )

    assert result == {
        "type": "task_update",
        "id": "tooluse_abc",
        "title": "Using get_weather",
        "status": "in_progress",
    }


def test_task_chunk_without_name_or_id_uses_fallbacks():
    result = build_tool_use_task_chunk(
        tool_use_id=None, tool_name=None, status="complete"
    )

    assert result == {
        "type": "task_update",
        "id": "tool",
        "title": "Using a tool",
        "status": "complete",
    }


def test_task_chunk_title_is_truncated_to_chunk_limit():
    result = build_tool_use_task_chunk(
        tool_use_id="t", tool_name="x" * 300, status="in_progress"
    )

    assert len(result["title"]) == 256
