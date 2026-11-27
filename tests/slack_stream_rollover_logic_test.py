from __future__ import annotations

from itertools import product

from app.slack_stream_logic import open_fence, split_point

# Lines a reply is made of: prose, blank lines, table rows, fences that open
# (with and without a language, indented, wider) and fences that only close,
# and lines that only look like fences.
LINES = [
    "text",
    "",
    "| a | b |",
    "```",
    "```python",
    "   ```",
    "````",
    "    ```",
    "``",
    "x ```",
    "```  ",
]


def reference_open_fence(lines: list[str]) -> str | None:
    """The fence state, line by line, the way a reader tracks it."""
    opener = None
    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        body = line.lstrip(" ")
        ticks = len(body) - len(body.lstrip("`"))
        if indent > 3 or ticks < 3:
            continue
        if opener is None:
            opener = body
        elif body.strip("` ") == "" and ticks >= len(opener) - len(opener.lstrip("`")):
            opener = None
    return opener


def test_open_fence_follows_every_document_of_up_to_four_lines():
    checked = 0
    for n in range(5):
        for lines in product(LINES, repeat=n):
            text = "".join(line + "\n" for line in lines)
            assert open_fence(text) == reference_open_fence(list(lines)), text
            # A last line still on its way does not count yet.
            for partial in LINES:
                assert open_fence(text + partial) == open_fence(text), (text, partial)
            checked += 1
    assert checked > 10_000


def test_a_closing_fence_ends_the_block_it_opened():
    for lang in ["", "python", "ts"]:
        text = f"Code:\n\n```{lang}\nx = 1\n"
        assert open_fence(text) == f"```{lang}"
        assert open_fence(text + "```\n") is None
        assert open_fence(text + "```\nmore\n") is None


def reference_split_point(sent: str, incoming: str, limit: int):
    """The first end, by trying every one, that is long enough and ends a
    blank line outside a code block or any line inside one."""
    text = sent + incoming
    for k in range(len(incoming) + 1):
        end = len(sent) + k
        head = text[:end]
        if len(head) < limit or not head.endswith("\n"):
            continue
        fence = open_fence(head)
        if fence is not None or head.endswith("\n\n"):
            return k, fence
    return None


def test_split_point_is_the_first_safe_break_past_the_limit():
    pieces = ["a", "\n", "\n\n", "```\n", "```py\n", "| r |\n", "b\n\nc"]
    checked = 0
    for sent_parts in product(pieces, repeat=3):
        sent = "".join(sent_parts)
        for incoming_parts in product(pieces, repeat=2):
            incoming = "".join(incoming_parts)
            for limit in [0, 1, 3, len(sent), len(sent) + 2, len(sent + incoming) + 1]:
                saved = (sent, incoming)
                got = split_point(sent, incoming, limit)
                assert got == reference_split_point(sent, incoming, limit), (
                    sent,
                    incoming,
                    limit,
                )
                assert (sent, incoming) == saved
                checked += 1
    assert checked > 10_000


def test_a_long_reply_splits_at_the_paragraph_after_the_limit():
    sent = "word " * 2000 + "\n"
    assert split_point(sent, "more words\nmore\n\nnext", 10_000) == (
        len("more words\nmore\n\n"),
        None,
    )


def test_a_long_code_block_splits_between_its_lines_and_reopens():
    sent = "Here:\n\n```python\n" + "x = 1\n" * 2000 + "x = "
    assert split_point(sent, "1", 10_000) is None
    assert split_point(sent, "1\nz = 3\n", 10_000) == (
        len("1\n"),
        "```python",
    )
