"""A small AgentCore agent that Welt can drive.

Receives Welt's payload (Bedrock Converse-shaped `messages`), feeds it to a
Strands agent, and yields the renderable subset of its `stream_async` events —
the AgentCore Runtime SDK emits each one as SSE, which Welt renders into
Slack. Both directions of the wire adaptation live in the `welt-io` package
(https://github.com/iwamot/welt-io); this module is a plain Strands agent.

This example is a standalone deployable and must not import Welt's `app/`
package; the JSON wire contract is the only thing the two sides share.
"""

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands_tools import generate_image
from welt_io import decode_file_blocks, renderable_events

# generate_image saves each image under ./output as a side effect, and the
# temp dir is the writable path in the AgentCore Runtime container.
os.chdir(tempfile.gettempdir())

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
    decode_file_blocks(messages)  # base64 file bytes -> raw bytes, in place
    agent = Agent(tools=[current_time, generate_image], callback_handler=None)
    # Reduce the stream to the JSON-serializable events Welt renders
    async for event in renderable_events(agent.stream_async(messages)):
        yield event


if __name__ == "__main__":
    app.run()
