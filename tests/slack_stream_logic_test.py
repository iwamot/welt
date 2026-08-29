from __future__ import annotations

from app.slack_stream_logic import PendingAppend, PendingAppends, note_after_reply

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
