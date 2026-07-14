from __future__ import annotations

import pytest

from app.env import load_env, require_env

_RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/my_agent-abcdefghij"
)
_HARNESS_ARN = (
    "arn:aws:bedrock-agentcore:us-west-2:123456789012:harness/MyHarness-XyZ1234567"
)

_REQUIRED = {
    "AGENT_ARN": _RUNTIME_ARN,
    "SLACK_BOT_TOKEN": "xoxb-test",
}


def test_required_variables_and_defaults():
    result = load_env(_REQUIRED)

    assert result.agent_arn == _RUNTIME_ARN
    assert result.agent_region == "us-west-2"
    assert result.slack_bot_token == _REQUIRED["SLACK_BOT_TOKEN"]
    assert result.log_level == "INFO"
    assert result.deps_log_level == "INFO"
    assert result.slack_stream_buffer_size == 256
    assert result.file_input_modalities == ()
    assert result.agent_manages_history is False
    assert result.boot_warnings == ()


def test_overrides_are_applied():
    result = load_env(
        {
            **_REQUIRED,
            "LOG_LEVEL": "DEBUG",
            "DEPS_LOG_LEVEL": "WARNING",
            "SLACK_STREAM_BUFFER_SIZE": "1024",
            "FILE_INPUT_MODALITIES": "image,document",
            "AGENT_MANAGES_HISTORY": "true",
        }
    )

    assert result.log_level == "DEBUG"
    assert result.deps_log_level == "WARNING"
    assert result.slack_stream_buffer_size == 1024
    assert result.file_input_modalities == ("image", "document")
    assert result.agent_manages_history is True


def test_missing_bot_token_is_rejected():
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN must be set"):
        load_env({"AGENT_ARN": _RUNTIME_ARN})


@pytest.mark.parametrize("environ", [{}, {"AGENT_ARN": ""}])
def test_unset_agent_arn_is_local_mode(environ: dict[str, str]):
    result = load_env({"SLACK_BOT_TOKEN": "xoxb-test", **environ})

    assert result.agent_arn is None
    assert result.agent_region is None
    assert result.boot_warnings == ()


def test_require_env_returns_the_value():
    assert require_env({"SLACK_APP_TOKEN": "xapp-test"}, "SLACK_APP_TOKEN") == (
        "xapp-test"
    )


@pytest.mark.parametrize("environ", [{}, {"SLACK_SIGNING_SECRET": ""}])
def test_require_env_rejects_missing_or_empty_values(environ: dict[str, str]):
    with pytest.raises(ValueError, match="SLACK_SIGNING_SECRET must be set"):
        require_env(environ, "SLACK_SIGNING_SECRET")


def test_local_mode_keeps_runtime_only_settings():
    result = load_env(
        {
            "SLACK_BOT_TOKEN": "xoxb-test",
            "FILE_INPUT_MODALITIES": "image",
            "AGENT_MANAGES_HISTORY": "true",
        }
    )

    assert result.file_input_modalities == ("image",)
    assert result.agent_manages_history is True
    assert result.boot_warnings == ()


def test_arn_without_region_is_rejected():
    environ = {**_REQUIRED, "AGENT_ARN": "runtime/my_agent-abcdefghij"}

    with pytest.raises(ValueError, match="AGENT_ARN must be a full"):
        load_env(environ)


def test_non_integer_buffer_size_is_rejected():
    with pytest.raises(ValueError, match="SLACK_STREAM_BUFFER_SIZE must be an"):
        load_env({**_REQUIRED, "SLACK_STREAM_BUFFER_SIZE": "many"})


def test_harness_arn_ignores_file_input_with_a_warning():
    environ = {
        **_REQUIRED,
        "AGENT_ARN": _HARNESS_ARN,
        "FILE_INPUT_MODALITIES": "image",
    }

    result = load_env(environ)

    assert result.file_input_modalities == ()
    assert result.boot_warnings == (
        "Ignoring FILE_INPUT_MODALITIES: AGENT_ARN is a harness ARN, "
        "and InvokeHarness accepts text content only",
    )


def test_harness_arn_without_runtime_only_settings_boots_quietly():
    result = load_env({**_REQUIRED, "AGENT_ARN": _HARNESS_ARN})

    assert result.agent_arn == _HARNESS_ARN
    assert result.file_input_modalities == ()
    assert result.boot_warnings == ()


def test_harness_arn_defaults_to_agent_managed_history():
    result = load_env({**_REQUIRED, "AGENT_ARN": _HARNESS_ARN})

    assert result.agent_manages_history is True


@pytest.mark.parametrize("value", ["false", "False"])
def test_explicit_false_agent_manages_history_is_applied(value: str):
    result = load_env({**_REQUIRED, "AGENT_MANAGES_HISTORY": value})

    assert result.agent_manages_history is False


def test_empty_agent_manages_history_falls_back_to_the_default():
    result = load_env({**_REQUIRED, "AGENT_MANAGES_HISTORY": ""})

    assert result.agent_manages_history is False


def test_harness_arn_ignores_disabled_history_management_with_a_warning():
    environ = {
        **_REQUIRED,
        "AGENT_ARN": _HARNESS_ARN,
        "AGENT_MANAGES_HISTORY": "false",
    }

    result = load_env(environ)

    assert result.agent_manages_history is True
    assert result.boot_warnings == (
        "Ignoring AGENT_MANAGES_HISTORY: AGENT_ARN is a harness "
        "ARN, and the harness keeps the conversation history itself",
    )


def test_boot_warnings_are_in_ascending_variable_name_order():
    environ = {
        **_REQUIRED,
        "AGENT_ARN": _HARNESS_ARN,
        "AGENT_MANAGES_HISTORY": "false",
        "FILE_INPUT_MODALITIES": "image",
    }

    result = load_env(environ)

    assert result.boot_warnings == (
        "Ignoring AGENT_MANAGES_HISTORY: AGENT_ARN is a harness "
        "ARN, and the harness keeps the conversation history itself",
        "Ignoring FILE_INPUT_MODALITIES: AGENT_ARN is a harness ARN, "
        "and InvokeHarness accepts text content only",
    )


def test_non_boolean_agent_manages_history_is_rejected():
    environ = {**_REQUIRED, "AGENT_MANAGES_HISTORY": "yes"}

    with pytest.raises(ValueError, match="AGENT_MANAGES_HISTORY must be"):
        load_env(environ)
