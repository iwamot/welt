from __future__ import annotations

import base64
import json
from copy import deepcopy
from itertools import product

from app.converse_logic import (
    DOCUMENT_NAME_MAX_LENGTH,
    ContentBlock,
    build_document_block,
    build_image_block,
    build_messages,
    build_video_block,
)
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


def _section(text: str) -> dict:
    return {
        "type": "rich_text",
        "elements": [
            {"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}
        ],
    }


# Replies whose cost differs from a naive reading of their `text`: JSON
# escaping (a Japanese character travels as six bytes, not three), the
# Markdown mrkdwn becomes (`*bold*` leaves as `**bold**`), Welt's own reply
# read from its blocks (Slack's summary leaves a table out of `text`), a
# non-string `text`, and a name standing in for a longer ID.
_SAID = {
    "user": "UBOT",
    "text": "Here they are:",
    "blocks": [_section("Here they are:")],
}
_ESTIMATED_REPLIES = [
    _reply(user, text)
    for user, text in product(
        ("U0AAA", "U0AAAAAAAAA", "UBOT"),
        (
            "",
            "aaaa",
            "hello",
            "ああああ",
            "bold",
            "*bold*",
            '"\\\n\t\x01',
            "hi <@U0AAAAAAAAA> 😀",
        ),
    )
] + [
    {"user": "U0AAA", "text": None},
    {"user": "UBOT", "text": None},
    _SAID,
    {
        **_SAID,
        "blocks": [
            _section("Here they are:"),
            {"type": "table", "rows": [[_section("fruit")], [_section("apple")]]},
        ],
    },
    {**_SAID, "user": "U0AAA"},
    {"user": "U0AAA", "text": "look", "files": [{"id": "F1", "name": "a.png"}]},
    {
        "user": "UBOT",
        "text": "",
        "files": [{"id": "F1", "title": "表", "name": "t.csv"}],
    },
]


def test_a_reply_is_estimated_at_what_its_message_adds_to_the_payload():
    # What a message adds is measured on the payload itself, encoded as
    # agent_service encodes it. The estimate may add a fixed allowance per
    # role, which must leave room for the separator before the next message,
    # but must not drift with what the reply says.
    for names in ({}, {"U0AAAAAAAAA": "iwamot", "U0AAA": "山田"}):
        allowances: dict[str, set[int]] = {"user": set(), "assistant": set()}
        for reply in _ESTIMATED_REPLIES:
            before = deepcopy(reply)
            # Welt's own reply is kept only between two of somebody else's.
            thread = [_reply("U0AAA", "open"), reply, _reply("U0AAA", "close")]
            messages = build_messages(thread, bot_user_id="UBOT", display_names=names)
            estimate = reply_payload_bytes(
                reply, bot_user_id="UBOT", display_names=names
            )
            assert reply == before
            if len(messages) == 3:
                message = messages[1]
                allowances[message["role"]].add(estimate - len(json.dumps(message)))
            else:
                # A reply of Welt's that says nothing adds no message.
                assert reply["user"] == "UBOT"
                assert estimate >= 0
        for role, allowance in allowances.items():
            assert len(allowance) == 1, (role, allowance)
            assert min(allowance) >= len(", ")


def test_a_file_is_estimated_at_what_its_block_adds_to_the_payload():
    # The block is measured as it travels: base64 inside the JSON wire, and
    # a document under the longest name Converse accepts.
    blocks_for = {
        "image": lambda data: build_image_block(image_format="webp", data_base64=data),
        "video": lambda data: build_video_block(
            video_format="three_gp", data_base64=data
        ),
        "document": lambda data: build_document_block(
            document_format="xlsx",
            name="a" * DOCUMENT_NAME_MAX_LENGTH,
            data_base64=data,
        ),
    }
    for modality, block_for in blocks_for.items():
        allowance: set[int] = set()
        for size in [*range(10), 3_000_000]:
            data = base64.b64encode(bytes(size)).decode("ascii")
            block = block_for(data)
            allowance.add(file_payload_bytes(size) - len(json.dumps(block)))
        assert len(allowance) == 1, (modality, allowance)
        assert min(allowance) >= len(", ")


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


def _image_block(selection: FileToFetch, size: int) -> ContentBlock:
    data = base64.b64encode(bytes(size)).decode("ascii")
    return build_image_block(image_format=selection.format, data_base64=data)


def test_the_thread_that_survives_fits_and_is_cut_only_from_the_oldest_end():
    # Every reply attaches files of its own: a file ID is not shared between
    # messages here. Sizes and budgets straddle a file, a reply, and both.
    users = ("U0AAA", "UBOT")
    texts = ("", "ああ")
    attachments = ((), (3_000,), (10, 3_000), (3_000, 1))
    budgets = (1, 200, 300, 2_500, 4_400, 5_000, 9_000, 20_000)
    for count in range(1, 4):
        for shapes in product(product(users, texts, attachments), repeat=count):
            replies = [
                _reply(
                    user,
                    text,
                    [(f"F{index}_{n}", size) for n, size in enumerate(sizes)],
                )
                for index, (user, text, sizes) in enumerate(shapes)
            ]
            sizes_by_id = {
                file["id"]: file["size"]
                for reply in replies
                for file in reply.get("files", [])
            }
            # Newest first, the way the selection hands them over; Welt's
            # own uploads are not taken.
            selections = [
                _selection(file["id"])
                for reply in reversed(replies)
                if reply["user"] != "UBOT"
                for file in reply.get("files", [])
            ]
            for budget in budgets:
                before = deepcopy((replies, selections))
                kept_replies, kept_files = compact_to_payload_budget(
                    replies,
                    selections,
                    bot_user_id="UBOT",
                    display_names={},
                    max_payload_bytes=budget,
                )
                assert (replies, selections) == before
                case = (shapes, budget)

                # A non-empty suffix of the thread, and a subsequence of the
                # selection.
                assert kept_replies == replies[len(replies) - len(kept_replies) :]
                assert kept_replies, case
                remaining = iter(selections)
                assert all(kept in remaining for kept in kept_files), case

                # What went is the dropped replies' files and the oldest kept
                # reply's last ones, so that reply keeps a prefix of its own.
                dropped_ids = {s.file_id for s in selections} - {
                    s.file_id for s in kept_files
                }
                oldest_ids = [f["id"] for f in kept_replies[0].get("files", [])]
                newer_ids = {
                    f["id"]
                    for reply in kept_replies[1:]
                    for f in reply.get("files", [])
                }
                assert not dropped_ids & newer_ids, case
                kept_ids = {s.file_id for s in kept_files}
                kept_of_oldest = [i for i in oldest_ids if i in kept_ids]
                selected_of_oldest = [
                    i for i in oldest_ids if i in {s.file_id for s in selections}
                ]
                assert kept_of_oldest == selected_of_oldest[: len(kept_of_oldest)]

                # What is sent fits, unless it is down to the last reply alone.
                blocks: dict[str, ContentBlock] = {
                    s.file_id: _image_block(s, sizes_by_id[s.file_id])
                    for s in kept_files
                }
                messages = build_messages(
                    kept_replies, bot_user_id="UBOT", file_blocks_by_id=blocks
                )
                sent = len(json.dumps({"messages": messages}))
                if len(kept_replies) > 1 or kept_files:
                    assert sent <= budget, case

                # Nothing went that the budget had room for: putting back
                # the last thing dropped would not have fitted.
                estimate = sum(
                    reply_payload_bytes(r, bot_user_id="UBOT", display_names={})
                    for r in kept_replies
                ) + sum(file_payload_bytes(sizes_by_id[s.file_id]) for s in kept_files)
                if len(selected_of_oldest) > len(kept_of_oldest):
                    restored = selected_of_oldest[len(kept_of_oldest)]
                    assert estimate + file_payload_bytes(sizes_by_id[restored]) > budget
                elif len(kept_replies) < len(replies):
                    restored_reply = replies[len(replies) - len(kept_replies) - 1]
                    assert (
                        estimate
                        + reply_payload_bytes(
                            restored_reply, bot_user_id="UBOT", display_names={}
                        )
                        > budget
                    ), case


def test_a_file_several_replies_carry_is_counted_once_per_reply():
    # A downloaded file goes into every message that lists it. The selection
    # holds it once per reply when slots allow, or only once when the
    # modality's slots run out.
    shared = ("S", 2_000)
    attachments = ((), (shared,), (("A", 10), shared), (shared, ("A", 10)))
    budgets = (1, 300, 2_500, 4_400, 5_000, 7_000, 9_000, 20_000)
    for count in range(1, 4):
        for shapes in product(attachments, repeat=count):
            replies = [
                _reply(
                    "U0AAA",
                    f"r{index}",
                    [
                        (file_id if file_id == "S" else f"{file_id}{index}", size)
                        for file_id, size in files
                    ],
                )
                for index, files in enumerate(shapes)
            ]
            sizes_by_id = {
                file["id"]: file["size"]
                for reply in replies
                for file in reply.get("files", [])
            }
            per_reply = [
                _selection(file["id"])
                for reply in reversed(replies)
                for file in reply.get("files", [])
            ]
            once = list({s.file_id: s for s in reversed(per_reply)}.values())[::-1]
            for selections, budget in product((per_reply, once), budgets):
                before = deepcopy((replies, selections))
                kept_replies, kept_files = compact_to_payload_budget(
                    replies,
                    selections,
                    bot_user_id="UBOT",
                    display_names={},
                    max_payload_bytes=budget,
                )
                assert (replies, selections) == before
                case = (shapes, [s.file_id for s in selections], budget)

                assert kept_replies, case
                assert kept_replies == replies[len(replies) - len(kept_replies) :]

                # A file a kept newer reply carries is still there.
                kept_ids = {s.file_id for s in kept_files}
                newer_ids = {
                    f["id"]
                    for reply in kept_replies[1:]
                    for f in reply.get("files", [])
                }
                selected_ids = {s.file_id for s in selections}
                assert newer_ids & selected_ids <= kept_ids, case

                # What is sent fits, unless it is down to the last reply alone.
                blocks: dict[str, ContentBlock] = {
                    s.file_id: _image_block(s, sizes_by_id[s.file_id])
                    for s in kept_files
                }
                messages = build_messages(
                    kept_replies, bot_user_id="UBOT", file_blocks_by_id=blocks
                )
                sent = len(json.dumps({"messages": messages}))
                if len(kept_replies) > 1 or kept_files:
                    assert sent <= budget, case
