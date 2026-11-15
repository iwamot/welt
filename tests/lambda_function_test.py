from __future__ import annotations

from slack_bolt import Ack

from lambda_function import _slack_environ, just_ack


def test_just_ack_answers_at_once():
    ack = Ack()

    just_ack(ack)

    assert ack.response is not None
    assert ack.response.status == 200


def test_slack_environ_passes_the_environment_through_without_a_secret():
    environ = {"SLACK_BOT_TOKEN": "xoxb-env", "SLACK_SIGNING_SECRET": "sign-env"}

    assert _slack_environ(environ) == (environ, ())
