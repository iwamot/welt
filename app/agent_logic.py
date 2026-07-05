"""Pure logic for addressing the agent behind an AgentCore invoke call.

This module decides *where* a conversation goes and *as whom*: which invoke
API the configured ARN selects, the runtimeSessionId that keys the
conversation (one Slack thread = one session), and the runtimeUserId that
carries the verified Slack identity. The request payload lives in
`converse_logic`, the reply parsing in `stream_logic`.
"""

from __future__ import annotations

# runtimeSessionId constraints, taken as the intersection of the two invoke
# APIs so one session ID format serves both: InvokeAgentRuntime allows 33-256
# characters with no character-set restriction, InvokeHarness allows 33-100
# characters matching [a-zA-Z0-9][a-zA-Z0-9-_]*.
RUNTIME_SESSION_ID_MIN_LENGTH = 33


def build_runtime_session_id(
    *, team_id: str | None, channel_id: str, thread_ts: str
) -> str:
    """
    Build the AgentCore runtimeSessionId for a Slack conversation.

    The session key is the reply thread — one thread is one conversation, in
    channels and DMs alike — so an agent using AgentCore Memory continues the
    right conversation. The format is "slack_<team>_<channel>_<thread_ts>"
    ("-" when the team is unknown, the timestamp's dot flattened to "-"),
    staying inside the [a-zA-Z0-9-_] character set InvokeHarness allows.
    Padded with "_" to the 33-character minimum both invoke APIs share, in
    case Slack IDs run shorter than usual.

    Args:
        team_id (str | None): The Slack team ID.
        channel_id (str): The Slack channel ID.
        thread_ts (str): The thread timestamp Welt replies into.

    Returns:
        str: A deterministic session ID at least 33 characters long.
    """
    joined = "_".join(
        ["slack", team_id or "-", channel_id, thread_ts.replace(".", "-")]
    )
    return joined.ljust(RUNTIME_SESSION_ID_MIN_LENGTH, "_")


def build_runtime_user_id(*, team_id: str | None, user_id: str) -> str:
    """
    Build the AgentCore runtimeUserId for a verified Slack user.

    Welt has authenticated the Slack connection, so the agent may trust this
    identity (e.g. as an AgentCore Memory actor key) as long as only Welt's
    IAM role can invoke it.

    Args:
        team_id (str | None): The Slack team ID.
        user_id (str): The verified Slack user ID.

    Returns:
        str: The identity string, e.g. ``slack:T0123456:U0123456``.
    """
    return f"slack:{team_id or '-'}:{user_id}"


def parse_arn_region(arn: str) -> str | None:
    """
    Extract the region from an AgentCore ARN.

    The ARN already names the region the bedrock-agentcore client must call,
    so Welt derives it from AGENT_ARN instead of asking for a separate region
    setting — a separately configured region could only repeat the ARN or
    contradict it.

    Args:
        arn (str): The ARN configured as the agent target.

    Returns:
        str | None: The region, or None if the ARN does not carry one.
    """
    parts = arn.split(":", 5)
    if len(parts) == 6 and parts[3]:
        return parts[3]
    return None


def is_harness_arn(arn: str) -> bool:
    """
    Check whether an AgentCore ARN points at a managed harness.

    Welt accepts either resource kind in AGENT_ARN and picks the
    invoke API by it: a harness ARN (resource "harness/...") goes through
    `invoke_harness`, anything else through `invoke_agent_runtime`.

    Args:
        arn (str): The ARN configured as the agent target.

    Returns:
        bool: True if the ARN's resource is a harness.
    """
    parts = arn.split(":", 5)
    return len(parts) == 6 and parts[5].startswith("harness/")
