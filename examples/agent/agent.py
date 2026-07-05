"""A small AgentCore agent that Welt can drive.

Receives Welt's payload (Bedrock Converse-shaped `messages`), feeds it to a
Strands agent, and yields the renderable subset of its `stream_async` events —
the AgentCore Runtime SDK emits each one as SSE, which Welt renders into
Slack. Both directions of the wire adaptation live in `welt_io`; this module
is a plain Strands agent.

This example is a standalone deployable and must not import Welt's `app/`
package; the JSON wire contract is the only thing the two sides share.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from welt_io import decode_file_blocks, renderable_events

app = BedrockAgentCoreApp()


@tool
def current_time() -> str:
    """
    Get the current date and time in UTC.

    Returns:
        str: The current UTC time in ISO 8601 format.
    """
    return datetime.now(UTC).isoformat()


@app.entrypoint
async def invoke(payload: dict) -> AsyncIterator[dict]:
    """
    Stream a reply to the conversation Welt sent.

    Args:
        payload (dict): The invocation payload, with Converse-shaped
            `messages` built by Welt from the Slack thread, file blocks
            base64-encoded.

    Yields:
        dict: The renderable subset of Strands `stream_async` events.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        yield {
            "data": "I received an empty conversation, so there is nothing to reply to."
        }
        return
    decode_file_blocks(messages)
    agent = Agent(tools=[current_time], callback_handler=None)
    async for event in renderable_events(agent.stream_async(messages)):
        yield event


if __name__ == "__main__":
    app.run()
