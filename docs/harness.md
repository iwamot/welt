# Chatting with an AgentCore Harness

A managed harness is an agent AgentCore runs for you — a model, a system prompt, and the tools you attach — with no agent code to write or deploy. It has an ARN like a Runtime agent does, and that is all Welt needs, in every [deployment](../README.md#deployment).

To chat with a harness, set Welt's `AGENT_ARN` to the harness ARN instead of a runtime agent ARN — everything else works the same.

The console shows a harness under its own ARN and under its endpoint's (`.../harness-endpoint/<name>`); either works, and the endpoint ARN selects that endpoint, the way `AGENT_QUALIFIER` does. The identity Welt runs as needs `bedrock-agentcore:InvokeHarness` on the harness ARN (the Lambda template grants it).

However, three things work differently from a runtime agent:

- `FILE_INPUT_MODALITIES` is ignored, with a startup warning — a harness does not take file input.
- `AGENT_MANAGES_HISTORY` is ignored, with a startup warning — Welt always sends a harness only the new messages.
- Inline functions — the tools a harness leaves for its caller to run — are not supported. Welt runs no client-side tools, so a reply that calls one fails with a notice. Server-side tools (MCP servers, gateways, the built-in browser, code interpreter, shell, and file operations) work as usual.
