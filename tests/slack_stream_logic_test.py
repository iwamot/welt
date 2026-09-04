from __future__ import annotations

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from app.slack_stream_logic import (
    PendingAppend,
    PendingAppends,
    is_message_too_long,
    note_after_reply,
)

# --- note_after_reply -------------------------------------------------------


def test_a_note_opens_on_a_line_of_its_own():
    assert note_after_reply(":warning: Stopped.") == "\n\n:warning: Stopped."


def test_a_note_is_set_off_from_a_sentence_stopped_mid_word():
    reply = "the congestion window is halved when the sender dete"

    assert (reply + note_after_reply("!")).splitlines()[-1] == "!"


# --- record ------------------------------------------------------------------


def test_a_fresh_tail_owes_nothing():
    assert PendingAppends().drain() == []


def test_recorded_appends_drain_oldest_first():
    pending = PendingAppends()

    pending.record(markdown_text="first")
    pending.record(markdown_text="second")

    assert pending.drain() == [
        PendingAppend(markdown_text="first", chunks=None),
        PendingAppend(markdown_text="second", chunks=None),
    ]


def test_records_chunks_alongside_markdown():
    pending = PendingAppends()

    pending.record(markdown_text="text", chunks=[{"type": "task_update"}])

    assert pending.drain() == [
        PendingAppend(markdown_text="text", chunks=[{"type": "task_update"}])
    ]


def test_records_an_append_that_carries_neither():
    pending = PendingAppends()

    pending.record()

    assert pending.drain() == [PendingAppend(markdown_text=None, chunks=None)]


# --- clear -------------------------------------------------------------------


def test_clearing_drops_the_whole_tail():
    pending = PendingAppends()
    pending.record(markdown_text="buffered")
    pending.record(markdown_text="delivered with it")

    pending.clear()

    assert pending.drain() == []


def test_clearing_an_empty_tail_is_a_no_op():
    pending = PendingAppends()

    pending.clear()

    assert pending.drain() == []


# --- drain -------------------------------------------------------------------


def test_draining_leaves_nothing_owed():
    pending = PendingAppends()
    pending.record(markdown_text="replayed")

    pending.drain()

    assert pending.drain() == []


def test_a_drained_list_is_the_callers_own():
    pending = PendingAppends()
    pending.record(markdown_text="replayed")
    replay = pending.drain()

    pending.record(markdown_text="recorded during the replay")
    pending.clear()

    assert replay == [PendingAppend(markdown_text="replayed", chunks=None)]


# --- is_message_too_long ------------------------------------------------------


def _stream_error(data: dict) -> SlackApiError:
    response = AsyncSlackResponse(
        client=AsyncWebClient(),
        http_verb="POST",
        api_url="https://slack.com/api/chat.appendStream",
        req_args={},
        data=data,
        headers={},
        status_code=200,
    )
    return SlackApiError("The request to the Slack API failed.", response)


def test_a_full_message_is_recognized():
    assert is_message_too_long(_stream_error({"ok": False, "error": "msg_too_long"}))


def test_another_api_error_is_not_a_full_message():
    assert not is_message_too_long(_stream_error({"ok": False, "error": "ratelimited"}))
    assert not is_message_too_long(_stream_error({"ok": False, "error": 42}))


def test_an_error_without_a_response_is_not_a_full_message():
    assert not is_message_too_long(SlackApiError("connection dropped", None))
