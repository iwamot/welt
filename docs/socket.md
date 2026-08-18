# Running Welt as a Resident Process

As a resident process, Welt connects to Slack over Socket Mode: `main.py` holds the connection open, and Slack pushes events down it. Nothing has to be reachable from the internet, so there is no request URL to configure. The container image [`ghcr.io/iwamot/welt`](https://github.com/iwamot/welt/pkgs/container/welt) packages that process for hosting.

The setup below assumes you already have an agent on AgentCore Runtime, or a [managed harness](harness.md), for Welt to invoke.

## Setup

1. Create a Slack app from [`manifest.yml`](../manifest.yml) — [the pre-filled creation screen][create-app] is the quickest way there. Two tokens come out of it:

   - In **Basic Information > App-Level Tokens**, generate a token with the `connections:write` scope (`xapp-1-...`).
   - In **Install App**, install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`).

2. Run the image with those tokens and the ARN to invoke:

   ```sh
   docker run -it \
     -e SLACK_APP_TOKEN=xapp-1-... \
     -e SLACK_BOT_TOKEN=xoxb-... \
     -e AGENT_ARN=arn:aws:bedrock-agentcore:... \
     ghcr.io/iwamot/welt:latest
   ```

   Welt picks up AWS credentials the standard SDK way. On your machine, pass them in as environment variables too (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` if you have one). Hosting it on AWS works the same way: supply the variables through the hosting environment (an ECS task definition, ...) and let its IAM role provide the AWS credentials. Either identity needs `bedrock-agentcore:InvokeAgentRuntime`, and `bedrock-agentcore:InvokeAgentRuntimeForUser` because Welt sends the verified Slack user as the [`runtimeUserId`](wire.md#session-and-identity).

## Notes

- [Configuration](../README.md#configuration) lists the optional environment variables.
- Running from source works the same way: `uv run --env-file .env main.py`, as in the Quick Start, with `AGENT_ARN` added to the `.env` file.

[create-app]: https://api.slack.com/apps?new_app=1&manifest_yaml=display_information%3A%0A%20%20name%3A%20Welt%0Afeatures%3A%0A%20%20app_home%3A%0A%20%20%20%20home_tab_enabled%3A%20false%0A%20%20%20%20messages_tab_enabled%3A%20true%0A%20%20%20%20messages_tab_read_only_enabled%3A%20false%0A%20%20bot_user%3A%0A%20%20%20%20display_name%3A%20Welt%0A%20%20%20%20always_online%3A%20true%0Aoauth_config%3A%0A%20%20scopes%3A%0A%20%20%20%20bot%3A%0A%20%20%20%20%20%20-%20channels%3Ahistory%0A%20%20%20%20%20%20-%20chat%3Awrite%0A%20%20%20%20%20%20-%20files%3Aread%0A%20%20%20%20%20%20-%20files%3Awrite%0A%20%20%20%20%20%20-%20groups%3Ahistory%0A%20%20%20%20%20%20-%20im%3Ahistory%0A%20%20%20%20%20%20-%20mpim%3Ahistory%0A%20%20%20%20%20%20-%20reactions%3Awrite%0A%20%20%20%20%20%20-%20users%3Aread%0Asettings%3A%0A%20%20event_subscriptions%3A%0A%20%20%20%20bot_events%3A%0A%20%20%20%20%20%20-%20message.channels%0A%20%20%20%20%20%20-%20message.groups%0A%20%20%20%20%20%20-%20message.im%0A%20%20%20%20%20%20-%20message.mpim%0A%20%20interactivity%3A%0A%20%20%20%20is_enabled%3A%20true%0A%20%20socket_mode_enabled%3A%20true%0A
