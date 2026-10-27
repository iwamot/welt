"""Environment configuration for the Welt frontend.

All environment variables are read and validated in one place, at startup:
`load_env` turns the process environment into a fully validated `Env`, so a
missing or inconsistent setting fails at boot instead of on the first
message, and the rest of the app never touches `os.environ`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from app.agent_logic import (
    is_harness_arn,
    parse_arn_region,
    parse_gateway_region,
    split_endpoint_arn,
)
from app.slack_file_logic import Modality, parse_file_input_modalities


@dataclass(frozen=True)
class Env:
    """Validated configuration read from environment variables."""

    # An AgentCore Runtime agent ARN or a managed harness ARN; Welt picks the
    # invoke API (invoke_agent_runtime / invoke_harness) by the resource kind.
    # An endpoint ARN of either kind is split on load: the resource ARN
    # stays here and the endpoint name goes to `agent_qualifier`, the way
    # both invoke APIs take them. None selects URL-only Gateway mode when
    # agent_gateway_url is set, or local HTTP mode when it is unset.
    agent_arn: str | None
    # The signing region, taken from the ARN or, in URL-only mode, from the
    # Gateway URL. None means local HTTP mode, which needs no AWS client.
    agent_region: str | None
    # The endpoint `agent_arn` named, sent as the `qualifier` both invoke
    # APIs take. None sends no qualifier at all, which AgentCore resolves to
    # the DEFAULT endpoint — so an ARN that names no endpoint gets DEFAULT.
    # Local mode has no endpoints, so this is None whenever `agent_arn` is.
    agent_qualifier: str | None
    # Optional IAM-authenticated Gateway target base URL, in the Runtime's
    # region. The same client carries turns and interrupt responses.
    agent_gateway_url: str | None
    # The Slack bot token (xoxb) for Web API calls. Transport credentials
    # (the Socket Mode xapp token, the HTTP signing secret) belong to the
    # entry points, which read them with `require_env`.
    slack_bot_token: str
    # Logging level for Welt's own loggers: the `app` package and the entry
    # points. Dependencies follow `deps_log_level` instead.
    log_level: str
    # Logging level for everything else — the root logger, which dependency
    # libraries (botocore, slack_bolt, ...) inherit. Separate from
    # `log_level` because botocore's DEBUG output includes credential
    # material (the Authorization header, the string to sign), so raising
    # Welt's verbosity must not drag the dependencies along.
    deps_log_level: str
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

    With AGENT_GATEWAY_URL alone, Welt addresses the Gateway over signed
    HTTP and derives its signing region from that URL. With AGENT_ARN too,
    Welt uses the Runtime SDK's endpoint-override mode. With neither set,
    Welt invokes the local agent over HTTP; `agent_service.log_local_mode`
    names that target URL in the log.

    AGENT_ARN carries the endpoint too: an ARN that names one
    (`.../runtime-endpoint/<name>`, `.../harness-endpoint/<name>`) is split
    into the resource ARN the invoke call takes and the qualifier that
    selects the endpoint, and an ARN that names none resolves to DEFAULT.
    Both resource kinds are configured the same way.

    Settings the target cannot honor are ignored, each reported as a
    `boot_warnings` entry instead of failing the boot. A harness ignores the
    Runtime-only FILE_INPUT_MODALITIES and AGENT_MANAGES_HISTORY: it accepts
    text content only and always keeps the conversation history itself.

    Returns:
        Env: The validated configuration.

    Raises:
        ValueError: If a required variable is missing or a value is
            malformed.
    """
    agent_arn = environ.get("AGENT_ARN") or None
    agent_gateway_url = environ.get("AGENT_GATEWAY_URL") or None
    arn_endpoint: str | None = None
    if agent_arn is None:
        agent_region = None
        harness = False
        if agent_gateway_url is not None:
            agent_region = parse_gateway_region(agent_gateway_url)
            if agent_region is None:
                raise ValueError(
                    "AGENT_GATEWAY_URL must be an HTTPS AgentCore Gateway URL "
                    "including a region when AGENT_ARN is unset"
                )
    else:
        agent_region = parse_arn_region(agent_arn)
        if agent_region is None:
            raise ValueError(
                "AGENT_ARN must be a full AgentCore ARN including a "
                f"region, got {agent_arn!r}"
            )
        harness = is_harness_arn(agent_arn)
        agent_arn, arn_endpoint = split_endpoint_arn(agent_arn)
    slack_stream_buffer_size = _get_int(environ, "SLACK_STREAM_BUFFER_SIZE", 256)
    if slack_stream_buffer_size < 1:
        raise ValueError(
            "SLACK_STREAM_BUFFER_SIZE must be at least 1, "
            f"got {slack_stream_buffer_size}"
        )
    # boot_warnings stays in ascending variable-name order.
    boot_warnings: list[str] = []
    if agent_gateway_url is not None and harness:
        agent_gateway_url = None
        boot_warnings.append(
            "Ignoring AGENT_GATEWAY_URL: Gateway routing is for Runtime agents"
        )
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
        agent_qualifier=arn_endpoint,
        agent_gateway_url=agent_gateway_url,
        slack_bot_token=require_env(environ, "SLACK_BOT_TOKEN"),
        log_level=_get_log_level(environ, "LOG_LEVEL"),
        deps_log_level=_get_log_level(environ, "DEPS_LOG_LEVEL"),
        slack_stream_buffer_size=slack_stream_buffer_size,
        file_input_modalities=file_input_modalities,
        agent_manages_history=agent_manages_history,
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
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}") from None


def _get_log_level(environ: Mapping[str, str], name: str) -> str:
    # The names `logging` itself accepts, so a typo fails here rather than
    # in logging.basicConfig after load_env has returned.
    value = environ.get(name) or "INFO"
    if value not in logging.getLevelNamesMapping():
        raise ValueError(f"{name} must be a logging level name, got {value!r}")
    return value


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
