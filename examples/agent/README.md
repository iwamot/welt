# Example Agent

A small AgentCore Runtime agent that Welt can drive: it receives Welt's payload (Bedrock Converse-shaped `messages`), feeds it to a Strands agent, and streams the reply back. It does not import Welt's `app/` package — the JSON wire contract is the only thing the two sides share.

The agent-side half of that contract lives in [`welt_io.py`](welt_io.py), meant to be copied into your own agent as-is. It adapts both directions of the wire, which is JSON and cannot carry plain Strands values:

- **Inbound**, `decode_file_blocks` restores the raw bytes of the Converse image/document/video blocks Welt base64-encodes (a no-op when no files arrive).
- **Outbound**, `renderable_events` reduces the raw `stream_async` events — which carry non-JSON-serializable values that would not survive the SSE wire — to what Welt renders: text chunks (`data`), tool-use starts (`current_tool_use`), and tool completions (`tool_result`, slimmed to the toolUseId and status so tool output stays off the wire).

`agent.py` is then a plain Strands agent: the part you replace with your own. It carries one tool, `current_time`, so that tool use is easy to exercise end-to-end: asking the agent for the current time (the model has no clock) reliably triggers a tool call, which Welt renders as a task indicator in Slack — spinner while running, checkmark when the tool result arrives.

## File input

To let the agent receive Slack uploads, set Welt's `FILE_INPUT_MODALITIES` (e.g. `image,document`) and grant its Slack app the `files:read` scope. Allow only the modalities your model accepts: `agent.py` does not specify a model, and the Strands default (an Anthropic Claude model) takes image and document blocks but not video; allowing `video` requires configuring a model that supports Converse video input, such as Amazon Nova.

## Deploy

How you build and deploy agents is up to you — see [Host agents with Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html) for the current tooling. Whichever path you take:

- In the Amazon Bedrock console, enable model access for the model your agent uses.
- Note the deployed agent runtime ARN and set it as Welt's `AGENT_ARN`.

To smoke-test the deployment without Slack, invoke the runtime with a Welt-shaped payload:

```json
{"messages": [{"role": "user", "content": [{"text": "hello"}]}]}
```

To smoke-test the file path (with a file-input modality enabled), include a base64-encoded image block — this one is a single blue pixel:

```json
{"messages": [{"role": "user", "content": [
  {"text": "What color is this image?"},
  {"image": {"format": "png", "source": {"bytes": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="}}}
]}]}
```
