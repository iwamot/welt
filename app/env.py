"""Environment configuration for the Welt frontend.

All environment variables are read and validated in one place, at startup:
`load_env` turns the process environment into a fully validated `Env`, so a
missing or inconsistent setting fails at boot instead of on the first
message, and the rest of the app never touches `os.environ`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.agent_logic import is_harness_arn, parse_arn_region
from app.slack_file_logic import Modality, parse_file_input_modalities

_DEFAULT_REPLY_FAILURE_TEXT = ":warning: Failed to reply. Please check the app logs."


@dataclass(frozen=True)
class Env:
    """Validated configuration read from environment variables."""

    # An AgentCore Runtime agent ARN or a managed harness ARN; Welt picks the
    # invoke API (invoke_agent_runtime / invoke_harness) by the resource kind.
    agent_arn: str
    # The agent's region, taken from the ARN; the bedrock-agentcore client
    # targets it directly, so no separate region setting exists.
    agent_region: str
    # The Slack bot token (xoxb) for Web API calls. Transport credentials
    # (the Socket Mode xapp token, the HTTP signing secret) belong to the
    # entry points, which read them with `require_env`.
    slack_bot_token: str
    # Logging level for the whole process, not just Slack.
    log_level: str
    # Markdown characters buffered in memory before each chat.appendStream
    # call; larger values mean fewer API calls (chat.appendStream is
    # rate-limit Tier 4).
    slack_stream_buffer_size: int
    # Converse content-block modalities ("image", "document", "video") to
    # accept from Slack. Empty disables file input entirely.
    file_input_modalities: tuple[Modality, ...]
    # Whether the agent keeps the conversation history itself (always true
    # for a harness, which stores it server-side). When true, Welt sends
    # only the messages the agent has not seen yet instead of the whole
    # thread, which the agent would otherwise duplicate.
    agent_manages_history: bool
    # The message posted to the thread when replying fails. Static text only:
    # error details stay in the log, so they never leak into the channel.
    reply_failure_text: str
    # Warnings collected during validation: Runtime-only settings that a
    # harness target cannot honor, which Welt ignores. The entry points log
    # these once logging is configured (load_env runs before that, because
    # the logging level itself comes from the environment).
    boot_warnings: tuple[str, ...]


def load_env(environ: Mapping[str, str]) -> Env:
    """
    Read and validate Welt's configuration from environment variables.

    Args:
        environ (Mapping[str, str]): The process environment (`os.environ`).

    Settings that only apply to a Runtime agent (FILE_INPUT_MODALITIES,
    AGENT_MANAGES_HISTORY) are ignored for a harness target, each reported
    as a `boot_warnings` entry instead of failing the boot: the harness
    accepts text content only and always keeps the conversation history
    itself, so Welt behaves accordingly no matter what they say.

    Returns:
        Env: The validated configuration.

    Raises:
        ValueError: If a required variable is missing or a value is
            malformed.
    """
    agent_arn = require_env(environ, "AGENT_ARN")
    agent_region = parse_arn_region(agent_arn)
    if agent_region is None:
        raise ValueError(
            "AGENT_ARN must be a full AgentCore ARN including a "
            f"region, got {agent_arn!r}"
        )
    harness = is_harness_arn(agent_arn)
    # boot_warnings stays in ascending variable-name order.
    boot_warnings: list[str] = []
    agent_manages_history = _get_bool(environ, "AGENT_MANAGES_HISTORY", harness)
    if harness and not agent_manages_history:
        agent_manages_history = True
        boot_warnings.append(
            "Ignoring AGENT_MANAGES_HISTORY: AGENT_ARN is a harness "
            "ARN, and the harness keeps the conversation history itself"
        )
    file_input_modalities = parse_file_input_modalities(
        environ.get("FILE_INPUT_MODALITIES", "")
    )
    if harness and file_input_modalities:
        file_input_modalities = ()
        boot_warnings.append(
            "Ignoring FILE_INPUT_MODALITIES: AGENT_ARN is a harness ARN, "
            "and InvokeHarness accepts text content only"
        )
    return Env(
        agent_arn=agent_arn,
        agent_region=agent_region,
        slack_bot_token=require_env(environ, "SLACK_BOT_TOKEN"),
        log_level=environ.get("LOG_LEVEL", "INFO"),
        slack_stream_buffer_size=_get_int(environ, "SLACK_STREAM_BUFFER_SIZE", 256),
        file_input_modalities=file_input_modalities,
        agent_manages_history=agent_manages_history,
        # An empty value could not be posted to Slack, so it means "use the
        # default" rather than failing on the first error.
        reply_failure_text=environ.get("REPLY_FAILURE_TEXT")
        or _DEFAULT_REPLY_FAILURE_TEXT,
        boot_warnings=tuple(boot_warnings),
    )


def require_env(environ: Mapping[str, str], name: str) -> str:
    """
    Read a required environment variable, rejecting missing or empty values.

    Entry points use this for their transport credentials (`SLACK_APP_TOKEN`
    for Socket Mode, `SLACK_SIGNING_SECRET` for HTTP), which vary by entry
    and so stay out of the shared `Env`.

    Args:
        environ (Mapping[str, str]): The process environment (`os.environ`).
        name (str): The name of the environment variable.

    Returns:
        str: The non-empty value.

    Raises:
        ValueError: If the variable is missing or empty.
    """
    value = environ.get(name)
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def _get_int(environ: Mapping[str, str], name: str, default: int) -> int:
    value = environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}") from None


def _get_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name)
    if not value:
        return default
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(f"{name} must be 'true' or 'false', got {value!r}")
