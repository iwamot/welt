from __future__ import annotations

from app.payload_logic import (
    MAX_PAYLOAD_BYTES,
    compact_to_payload_budget,
    file_payload_bytes,
    reply_payload_bytes,
)
from app.slack_file_logic import FileToFetch


def _reply(
    user: str, text: str = "", files: list[tuple[str, int]] | None = None
) -> dict:
    reply: dict = {"user": user, "text": text}
    if files is not None:
        reply["files"] = [{"id": file_id, "size": size} for file_id, size in files]
    return reply


def _selection(file_id: str) -> FileToFetch:
    return FileToFetch(
        file_id=file_id,
        url=f"https://files.slack.com/{file_id}",
        modality="image",
        format="png",
        name=f"{file_id}.png",
    )


def _compact(replies: list[dict], file_ids: list[str], budget: int):
    kept_replies, kept_files = compact_to_payload_budget(
        replies,
        [_selection(f) for f in file_ids],
        bot_user_id="UBOT",
        display_names={},
        max_payload_bytes=budget,
    )
    return [r["text"] for r in kept_replies], [f.file_id for f in kept_files]


# --- estimates ---------------------------------------------------------------


def test_file_payload_bytes_counts_the_base64_growth():
    assert file_payload_bytes(3) - file_payload_bytes(0) == 4
    assert file_payload_bytes(3_000_000) - file_payload_bytes(0) == 4_000_000


def test_reply_payload_bytes_counts_json_escaping_not_characters():
    # agent_service encodes with json.dumps, whose default escapes non-ASCII
    # to \\uXXXX, so a Japanese character travels as six bytes and not three.
    ascii_reply = _reply("U0AAA", "aaaa")
    japanese_reply = _reply("U0AAA", "ああああ")

    grew_by = reply_payload_bytes(
        japanese_reply, bot_user_id="UBOT", display_names={}
    ) - reply_payload_bytes(ascii_reply, bot_user_id="UBOT", display_names={})

    assert grew_by == 4 * 5


def test_reply_payload_bytes_counts_the_markdown_the_text_becomes():
    # slack_to_markdown does not only shorten: `*bold*` leaves as `**bold**`,
    # so measuring the raw text would under-count an emphatic thread.
    plain = _reply("U0AAA", "bold")
    emphasised = _reply("U0AAA", "*bold*")

    grew_by = reply_payload_bytes(
        emphasised, bot_user_id="UBOT", display_names={}
    ) - reply_payload_bytes(plain, bot_user_id="UBOT", display_names={})

    assert grew_by == len("**bold**") - len("bold")


# --- compaction --------------------------------------------------------------


def test_nothing_is_dropped_when_it_already_fits():
    replies = [_reply("U0AAA", "old", [("F1", 10)]), _reply("U0AAA", "new")]

    assert _compact(replies, ["F1"], MAX_PAYLOAD_BYTES) == (["old", "new"], ["F1"])


def test_the_last_attachment_goes_before_any_message():
    replies = [
        _reply("U0AAA", "old", [("F1", 3_000_000), ("F2", 3_000_000)]),
        _reply("U0AAA", "new"),
    ]

    # Room for one of the two files, and both texts. The one attached first
    # is the one kept.
    budget = file_payload_bytes(3_000_000) + 1_000

    assert _compact(replies, ["F1", "F2"], budget) == (["old", "new"], ["F1"])


def test_a_message_goes_only_once_its_own_attachments_are_gone():
    replies = [
        _reply("U0AAA", "old", [("F1", 3_000_000)]),
        _reply("U0AAA", "mid", [("F2", 3_000_000)]),
        _reply("U0AAA", "new"),
    ]

    # Room for one file and two texts: the oldest message gives up its file,
    # and then, still one text too many, gives up itself.
    budget = (
        file_payload_bytes(3_000_000)
        + reply_payload_bytes(replies[1], bot_user_id="UBOT", display_names={})
        + reply_payload_bytes(replies[2], bot_user_id="UBOT", display_names={})
    )

    assert _compact(replies, ["F1", "F2"], budget) == (["mid", "new"], ["F2"])


def test_an_older_message_is_emptied_before_a_newer_one_is_touched():
    replies = [
        _reply("U0AAA", "old", [("F1", 3_000_000)]),
        _reply("U0AAA", "new", [("F2", 3_000_000)]),
    ]

    # Only the text fits, so both files go — the older one first.
    budget = 1_000

    kept_texts, kept_files = _compact(replies, ["F1", "F2"], budget)

    assert kept_files == []
    assert kept_texts == ["new"]


def test_the_last_reply_survives_even_when_it_does_not_fit():
    replies = [_reply("U0AAA", "the question", [("F1", 3_000_000)])]

    kept_texts, kept_files = _compact(replies, ["F1"], 1)

    assert kept_texts == ["the question"]
    assert kept_files == []


def test_both_files_of_the_oldest_message_go_before_the_message_does():
    replies = [
        _reply("U0AAA", "old", [("F1", 10), ("F2", 10)]),
        _reply("U0AAA", "new"),
    ]

    # Only the newest text fits, so both of the older message's files go
    # first, and the message itself follows.
    budget = reply_payload_bytes(replies[1], bot_user_id="UBOT", display_names={})

    assert _compact(replies, ["F1", "F2"], budget) == (["new"], [])


def test_a_reply_whose_files_key_is_not_a_list_has_nothing_to_give_up():
    replies = [
        {"user": "U0AAA", "text": "old", "files": "nonsense"},
        _reply("U0AAA", "new"),
    ]

    # With no attachment to drop, the reply itself is what goes.
    budget = reply_payload_bytes(replies[1], bot_user_id="UBOT", display_names={})

    assert _compact(replies, [], budget) == (["new"], [])


def test_malformed_file_entries_do_not_break_the_walk():
    replies = [
        {"user": "U0AAA", "text": "old", "files": ["nope", {"size": 5}, {"id": "F1"}]},
        _reply("U0AAA", "new"),
    ]

    budget = reply_payload_bytes(replies[1], bot_user_id="UBOT", display_names={})

    assert _compact(replies, ["F1"], budget) == (["new"], [])


def test_a_reply_of_welts_own_is_measured_by_what_its_blocks_say():
    # Slack writes the same summary for both of these — it leaves a table out
    # of `text` altogether — so measuring `text` would call them the same size
    # while one of them sends a table.
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

    said = {
        "user": "UBOT",
        "text": "Here they are:",
        "blocks": [_section("Here they are:")],
    }
    tabled = {
        **said,
        "blocks": [
            _section("Here they are:"),
            {"type": "table", "rows": [[_section("fruit")], [_section("apple")]]},
        ],
    }

    assert reply_payload_bytes(
        tabled, bot_user_id="UBOT", display_names={}
    ) > reply_payload_bytes(said, bot_user_id="UBOT", display_names={})


def test_non_string_text_is_measured_as_empty():
    assert reply_payload_bytes(
        {"user": "U0AAA", "text": None}, bot_user_id="UBOT", display_names={}
    ) == reply_payload_bytes(_reply("U0AAA", ""), bot_user_id="UBOT", display_names={})


def test_a_name_is_counted_as_it_will_be_sent():
    # The estimate runs on the same text build_messages will build, names
    # and all, so a long ID standing in for a short name cannot overcount.
    reply = _reply("U0AAAAAAAAA", "hello")

    named = reply_payload_bytes(
        reply, bot_user_id="UBOT", display_names={"U0AAAAAAAAA": "iwamot"}
    )
    unnamed = reply_payload_bytes(reply, bot_user_id="UBOT", display_names={})

    assert named < unnamed
