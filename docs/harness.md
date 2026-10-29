# Chatting with an AgentCore Harness

A managed harness is an agent AgentCore runs for you — a model, a system prompt, and the tools you attach — with no agent code to write or deploy. It has an ARN like a Runtime agent does, and that is all Welt needs, in every [deployment](../README.md#deployment).

## Create a Harness

Install [AgentCore CLI v1](https://github.com/aws/agentcore-cli#installation) and configure AWS credentials and a region where AgentCore Harness and your chosen Bedrock model are available.

From a directory outside any existing AgentCore project, create a project and add a harness:

```sh
agentcore create --name WeltHarness
cd WeltHarness
agentcore add harness --name assistant --system-prompt "You are a helpful assistant. Reply concisely."
```

Replace `app/assistant/harness.yaml` with:

```yaml
name: assistant
model:
  bedrockModelConfig:
    modelId: global.anthropic.claude-sonnet-4-6
memory:
  managedMemoryConfiguration: {}
```

Choose a model you can invoke in the deployment region. The system prompt lives in `app/assistant/system-prompt.md`; edit it to give the assistant your own instructions. See the CLI's [Harness configuration guide](https://github.com/aws/agentcore-cli/blob/v1.0.0/docs/harness-project-configuration.md) to add tools and skills.

Deploy the harness, then display its ARN:

```sh
agentcore deploy
agentcore status --json
```

### Memory and Conversation History

The configuration above enables managed AgentCore Memory, which incurs Memory charges. Welt sends a harness only the new messages from each Slack thread, using that thread's session ID; Memory provides the history for later turns. Disabling Memory leaves the harness with the new messages for that invocation.

## Connect Welt

Copy the Harness ARN from `agentcore status --json` into the `.env` file in your Welt repository:

```sh
AGENT_ARN=<harness-arn>
```

Start or restart Welt from that repository:

```sh
uv run --env-file .env main.py
```

Use the Slack app and tokens from the [Quick Start](../README.md#quick-start). Mention the bot or send it a DM, then reply in the same thread to continue the conversation. For hosting, follow the [resident process](socket.md) or [Lambda](lambda.md) guide with the Harness ARN.

The console shows a harness under its own ARN and under its endpoint's (`.../harness-endpoint/<name>`); either works, and the endpoint ARN selects that endpoint. The identity Welt runs as needs `bedrock-agentcore:InvokeHarness` on the harness ARN (the Lambda template grants it).

## Differences from a Runtime Agent

Some things work differently from a runtime agent:

- `FILE_INPUT_MODALITIES` is ignored, with a startup warning — a harness does not take file input.
- No files come back either: a harness reply carries no [`file` events](wire.md#file), so a file a server-side tool produces does not reach the thread.
- `AGENT_MANAGES_HISTORY` is ignored, with a startup warning — Welt always sends a harness only the new messages.
- [Interrupts](interrupts.md) are not available: a harness cannot stop mid-run to ask, so human-in-the-loop needs a runtime agent.
- Inline functions — the tools a harness leaves for its caller to run — are not supported. Welt runs no client-side tools, so a reply that calls one fails with a notice. Server-side tools (MCP servers, gateways, the built-in browser, code interpreter, shell, and file operations) work as usual.

## Remove the Example

From the `WeltHarness` project directory, remove the harness and deploy the empty project to delete its CloudFormation stack:

```sh
agentcore remove harness --name assistant
agentcore deploy --yes
```
