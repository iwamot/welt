# Welt

[![ghcr.io](https://img.shields.io/github/v/release/iwamot/welt?logo=docker&label=ghcr.io)](https://github.com/iwamot/welt/pkgs/container/welt)

**A Slack frontend for AI agents on Amazon Bedrock AgentCore.**

![Welt streaming an agent reply into a Slack thread, paused on an approval question with buttons and a text field](docs/images/interrupt-question.png)

Welt forwards conversations to your agent on AgentCore and streams the reply back into the Slack thread.

You focus on the agent — model, tools, MCP, memory. Welt handles the Slack side — tokens, event intake, history fetch, streaming rendering, and uploading the files your agent generates.

The pieces line up like this:

```
Slack ⇄ Welt ⇄ AgentCore Runtime
                └── your agent, using an adapter for Welt's JSON wire
```

Adapters exist for Strands Agents (Python) and Mastra (TypeScript), and more may follow — see [Agent-Side Adapters](#agent-side-adapters). The Quick Start below deploys welt-io's example agent.

## Quick Start

### 1. Deploy the Example Agent

Deploy [welt-io's example agent](https://github.com/iwamot/welt-io/tree/main/examples/agent) by following its README, and note the agent runtime ARN; step 3 needs it. (Prefer TypeScript? [welt-io-mastra's example agent](https://github.com/iwamot/welt-io-mastra/tree/main/examples/agent) works just as well here.)

### 2. Create a Slack App

- Go to <https://api.slack.com/apps> and create a new Slack app from [`manifest.yml`](manifest.yml).
- In **Basic Information > App-Level Tokens**, generate a token with the `connections:write` scope and copy it (`xapp-1-...`).
- In **Install App**, install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`).

### 3. Get the Code and Create a `.env` File

Clone this repository:

```sh
git clone https://github.com/iwamot/welt.git
cd welt
```

Then save your Slack tokens and the agent runtime ARN from step 1 in a `.env` file at the repository root ([`.env.sample`](.env.sample) lists all supported variables):

```sh
SLACK_APP_TOKEN=xapp-1-...
SLACK_BOT_TOKEN=xoxb-...
AGENT_ARN=arn:aws:bedrock-agentcore:...
```

### 4. Run Welt

Welt picks up your AWS credentials the standard SDK way — environment variables, `AWS_PROFILE`, an SSO session — and the identity needs permission to invoke your agent. Run Welt with [uv](https://docs.astral.sh/uv/):

```sh
uv run --env-file .env main.py
```

### 5. Say Hello!

Invite the bot to a channel (`/invite @Welt`) and mention it, or send it a DM. Welt streams the agent's reply into the thread; the example agent's README suggests things to try.

Once you're comfortable, swap in your own agent and point `AGENT_ARN` at its deployment — see [Agent-Side Adapters](#agent-side-adapters) below.

## Features

- [Files](docs/files.md) — file input from Slack uploads, and uploading the files your agent generates back into the thread.
- [Interrupts](docs/interrupts.md) — human-in-the-loop: a tool (or hook) that interrupts pauses the run and becomes buttons or a text field in the thread; the answer resumes it.

## Agent-Side Adapters

The wire between Welt and the agent is plain JSON; each feature page above documents its part of the contract. Each adapter maps the wire to one framework's types and carries its own example agent:

- [welt-io](https://github.com/iwamot/welt-io) — Strands Agents (Python)
- [welt-io-mastra](https://github.com/iwamot/welt-io-mastra) — Mastra (TypeScript)

Other stacks can implement the contract directly.

## Configuration

Optional environment variables, all with working defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_MANAGES_HISTORY` | `false` | What Welt sends per turn: the full thread history (`false`), or only the new messages (`true`). |
| `FILE_INPUT_MODALITIES` | (empty) | Comma-separated modalities to accept from Slack uploads; empty disables file input. See [Files](docs/files.md). |
| `LOG_LEVEL` | `INFO` | Logging level for the whole process. |
| `SLACK_STREAM_BUFFER_SIZE` | `256` | Markdown characters buffered before each streaming update; larger values mean fewer Slack API calls. |

## Other Ways to Run

- Running the container image — the same Socket Mode process, packaged as [`ghcr.io/iwamot/welt`](https://github.com/iwamot/welt/pkgs/container/welt) for hosting on AWS. Supply the same variables as the `.env` file through the hosting environment (an ECS task definition, ...) and let its IAM role provide the AWS credentials:

  ```sh
  docker run -it \
    -e SLACK_APP_TOKEN=xapp-1-... \
    -e SLACK_BOT_TOKEN=xoxb-... \
    -e AGENT_ARN=arn:aws:bedrock-agentcore:... \
    ghcr.io/iwamot/welt:latest
  ```
- [Running Welt on AWS Lambda](docs/lambda.md) — serve Welt on Lambda instead of a resident process: no always-on process, no cost while idle.
- [Chatting with an AgentCore harness](docs/harness.md) — point `AGENT_ARN` at a managed harness instead of your own agent code.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## Related Projects

- [iwamot/collmbo](https://github.com/iwamot/collmbo) — A Slack bot for chatting with 100+ LLMs directly, no AI agent to implement or deploy. Pick Collmbo for plain LLM chat, Welt for your own agent.

## License

MIT
