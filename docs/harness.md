# Chatting with an AgentCore Harness

To chat with a harness, set Welt's `AGENT_ARN` to the harness ARN instead of a runtime agent ARN — everything else works the same.

However, three things work differently from a runtime agent:

- `FILE_INPUT_MODALITIES` is ignored, with a startup warning — a harness does not take file input.
- `AGENT_MANAGES_HISTORY` is ignored, with a startup warning — Welt always sends a harness only the new messages.
- Inline functions — the tools a harness leaves for its caller to run — are not supported. Welt runs no client-side tools, so a reply that calls one fails with a notice. Server-side tools (MCP servers, gateways, the built-in browser, code interpreter, shell, and file operations) work as usual.
