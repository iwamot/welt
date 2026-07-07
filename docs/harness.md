# Chatting with an AgentCore Harness

To chat with a harness, set Welt's `AGENT_ARN` to the harness ARN instead of a runtime agent ARN — everything else works the same.

However, two environment variables are ignored, each with a startup warning:

- `FILE_INPUT_MODALITIES` — a harness does not take file input.
- `AGENT_MANAGES_HISTORY` — Welt always sends a harness only the new messages.
