"""Pure Slack text-formatting helpers.

These clean incoming Slack text for the agent (mention stripping,
mrkdwn → Markdown) and build streaming chunks for the reply. Outbound Markdown
needs no conversion — the chat streaming API renders `markdown_text`
server-side. They are pure so they can be covered by fixture-driven tests.
"""

from __future__ import annotations

import re


def remove_bot_mention(text: str, bot_user_id: str | None) -> str:
    """
    Remove the bot mention from the text.

    Args:
        text (str): The input text containing the bot mention.
        bot_user_id (str | None): The bot's user ID.

    Returns:
        str: The text with the bot mention removed.
    """
    return re.sub(rf"<@{bot_user_id}>\s*", "", text) if bot_user_id else text


def unescape_slack_formatting(content: str) -> str:
    """
    Unescape Slack formatting characters.

    Unescape &, < and >, since Slack replaces these with their HTML equivalents.
    See also: https://api.slack.com/reference/surfaces/formatting#escaping

    Args:
        content (str): The input string containing Slack formatting.

    Returns:
        str: The unescaped string.
    """
    return content.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def slack_to_markdown(content: str) -> str:
    """
    Convert Slack mrkdwn to Markdown format.

    Only spans that Slack itself renders as formatting are converted: a marker
    adjacent to an ASCII letter, digit, or another marker of the same kind is
    literal text in Slack's renderer, so `snake_case_names` and `2*3*4` pass
    through unchanged (CJK-adjacent markers do format, matching Slack).
    See also: https://api.slack.com/reference/surfaces/formatting#basics

    Args:
        content (str): The input string in Slack mrkdwn format.

    Returns:
        str: The converted string in Markdown format.
    """
    # Split the input string into parts based on code blocks and inline code
    parts = re.split(r"(?s)(```.+?```|`[^`\n]+?`)", content)

    # Apply the bold, italic, and strikethrough formatting to text not within code
    result = ""
    for part in parts:
        if not part.startswith("```") and not part.startswith("`"):
            for o, n in [
                # *bold* to **bold**
                (
                    r"(?<![A-Za-z0-9*])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![A-Za-z0-9*])",
                    r"**\1**",
                ),
                # _italic_ to *italic*
                (
                    r"(?<![A-Za-z0-9_])_(?!\s)([^_\n]+?)(?<!\s)_(?![A-Za-z0-9_])",
                    r"*\1*",
                ),
                # ~strike~ to ~~strike~~
                (
                    r"(?<![A-Za-z0-9~])~(?!\s)([^~\n]+?)(?<!\s)~(?![A-Za-z0-9~])",
                    r"~~\1~~",
                ),
            ]:
                part = re.sub(o, n, part)
        result += part
    return result


def build_slack_user_prefixed_text(reply: dict, text: str) -> str:
    """
    Build a Slack user-prefixed text message.

    Args:
        reply (dict): The reply dictionary containing user information.
        text (str): The text message to be prefixed.

    Returns:
        str: The formatted text message with user mention.
    """
    user_identifier = reply.get("user", reply.get("username"))
    return f"<@{user_identifier}>: {text}"


# Converse task_update chunks cap title/details at 256 characters.
TASK_CHUNK_TITLE_MAX_LENGTH = 256


def build_tool_use_task_chunk(
    *, tool_use_id: str | None, tool_name: str | None, status: str
) -> dict:
    """
    Build a task_update chunk showing a tool invocation in the reply timeline.

    Args:
        tool_use_id (str | None): The Strands toolUseId, used as the task ID
            so a later status change updates the same task.
        tool_name (str | None): The tool name, if the event carried one.
        status (str): The task status (in_progress / complete / error).

    Returns:
        dict: A chat.appendStream / chat.stopStream `chunks` entry.
    """
    title = f"Using {tool_name}" if tool_name else "Using a tool"
    return {
        "type": "task_update",
        "id": tool_use_id or "tool",
        "title": title[:TASK_CHUNK_TITLE_MAX_LENGTH],
        "status": status,
    }
