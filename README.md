# Welt

[![ghcr.io](https://img.shields.io/github/v/release/iwamot/welt?logo=docker&label=ghcr.io)](https://github.com/iwamot/welt/pkgs/container/welt)

**A Slack frontend for AI agents on Amazon Bedrock AgentCore.**

![Welt streaming an agent reply into a Slack thread](docs/images/streaming-demo.png)

Welt forwards conversations to your agent on AgentCore and streams the reply back into the Slack thread.

You focus on the agent — model, tools, MCP, memory. Welt handles the Slack side — tokens, event intake, history fetch, streaming rendering, and uploading the files your agent generates.

## Quick Start

### 1. Deploy the Example Agent

Deploy [`examples/agent/main.py`](examples/agent/main.py) — a small Strands agent with tools that tell the current time and generate images — with the [AgentCore CLI](https://github.com/aws/agentcore-cli):

```sh
agentcore create --name WeltExample --framework Strands --model-provider Bedrock --memory none
cd WeltExample

curl -o app/WeltExample/main.py https://raw.githubusercontent.com/iwamot/welt/main/examples/agent/main.py
uv add --project app/WeltExample welt-io strands-agents-tools

agentcore deploy
```

The example agent uses the Strands default model — currently an Anthropic Claude model — so enable access for it in the Amazon Bedrock console, in the region you deployed to. To try image generation too, also enable access for the Stability AI image models, in us-west-2 — the [`generate_image`](https://github.com/strands-agents/tools/blob/main/src/strands_tools/generate_image.py) tool defaults to Stable Image Core but may pick another.

### 2. Create a Slack App

- Go to <https://api.slack.com/apps> and create a new Slack app from [`manifest.yml`](manifest.yml).
- In **Basic Information > App-Level Tokens**, generate a token with the `connections:write` scope and copy it (`xapp-1-...`).
- In **Install App**, install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`).

### 3. Create a `.env` File

Save your Slack tokens and the agent runtime ARN from step 1 in a `.env` file ([`.env.sample`](.env.sample) lists all supported variables):

```sh
SLACK_APP_TOKEN=xapp-1-...
SLACK_BOT_TOKEN=xoxb-...
AGENT_ARN=arn:aws:bedrock-agentcore:...
```

### 4. Run Welt Container

Pass your current AWS credentials alongside the `.env` file — the identity needs permission to invoke your agent:

```sh
docker run -it \
  --env-file .env \
  --env-file <(aws configure export-credentials --format env-no-export) \
  ghcr.io/iwamot/welt:latest
```

### 5. Say Hello!

Invite the bot to a channel (`/invite @Welt`) and mention it, or send it a DM. Welt streams the agent's reply into the thread — ask for the current time and you'll see tool use too. Ask it to draw something, and the generated image is uploaded into the thread.

Once you're comfortable, swap in your own Strands agent: keep the [`welt-io`](https://github.com/iwamot/welt-io) adaptation from the example and point `AGENT_ARN` at your deployment.

## Configuration

Optional environment variables, all with working defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_MANAGES_HISTORY` | `false` | What Welt sends per turn: the full thread history (`false`), or only the new messages (`true`). |
| `FILE_INPUT_MODALITIES` | (empty) | Comma-separated Converse modalities to accept from Slack uploads (`image`, `document`, `video`); empty disables file input. Allow only modalities your model accepts. |
| `LOG_LEVEL` | `INFO` | Logging level for the whole process. |
| `REPLY_FAILURE_TEXT` | `:warning: Failed to reply. Please check the app logs.` | Message posted to the thread when replying fails. |
| `SLACK_STREAM_BUFFER_SIZE` | `256` | Markdown characters buffered before each streaming update; larger values mean fewer Slack API calls. |

## Supported Versions

While Welt is still 0.x, it shares minor versions with [welt-io](https://github.com/iwamot/welt-io): the supported pairing for Welt v0.Y is a welt-io 0.Y release, so upgrade them together. Other combinations may work, but come with no guarantee.

## Other Ways to Run

- [Running Welt on AWS Lambda](docs/lambda.md) — serve Welt on Lambda instead of a resident container.
- [Chatting with an AgentCore harness](docs/harness.md) — point `AGENT_ARN` at a managed harness instead of your own agent code.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## Related Projects

- [iwamot/collmbo](https://github.com/iwamot/collmbo) — A Slack bot for chatting with 100+ LLMs directly, no AI agent to implement or deploy. Pick Collmbo for plain LLM chat, Welt for your own agent.

## License

MIT
