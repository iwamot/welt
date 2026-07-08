# Example Agent

The example agent for Welt's [Quick Start](../../README.md#quick-start).

## Stack

| Package | Role |
|---------|------|
| [Bedrock AgentCore SDK](https://github.com/aws/bedrock-agentcore-sdk-python) | Serves the endpoint |
| [Strands Agents](https://github.com/strands-agents/sdk-python) | Runs the model and the tools |
| [Strands Agents Tools](https://github.com/strands-agents/tools) | Provides the `generate_image` tool |
| [welt-io](https://github.com/iwamot/welt-io) | Adapts the wire to Welt |

## Why welt-io?

The wire between Welt and the agent is JSON:

- **Inbound** — restores the raw bytes of the file blocks Welt base64-encodes.
- **Outbound** — reduces the Strands event stream, which carries values that would not survive the wire, to the events Welt renders.

## Optional: file input

The agent can also read files uploaded to Slack — disabled by default. To try it, set in Welt's `.env`:

```sh
FILE_INPUT_MODALITIES=image,document
```

These two are what the default model (currently Anthropic Claude) accepts; `video` needs a model that takes Converse video input — see [supported foundation models](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html).
