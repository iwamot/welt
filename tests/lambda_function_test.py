from __future__ import annotations

from slack_bolt import Ack

from lambda_function import just_ack


def test_just_ack_answers_at_once():
    ack = Ack()

    just_ack(ack)

    assert ack.response is not None
    assert ack.response.status == 200
