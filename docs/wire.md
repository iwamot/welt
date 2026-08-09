# Wire Contract

Welt talks to the agent in plain JSON: one request payload in, a stream of events out. This page is the complete specification — read it to build an [agent-side adapter](../README.md#agent-side-adapters) or to implement the contract directly in another stack.

To *use* Welt with an existing adapter, you do not need this page: the [feature pages](../README.md#features) cover Welt's behavior, and the adapter's own documentation covers the agent code.

One exception: a [managed harness](harness.md) is invoked through AgentCore's typed `InvokeHarness` API, not this wire.

## Transport

The wire rides AgentCore's invoke surface, in one of two modes:

| Mode | Request | Reply |
|---|---|---|
| Deployed (`AGENT_ARN` is a Runtime agent ARN) | `InvokeAgentRuntime` with the JSON payload | SSE stream |
| Local (`AGENT_ARN` unset) | `POST http://localhost:8080/invocations`, `Accept: text/event-stream` | SSE stream |

Local mode targets the surface the AgentCore SDK's local server provides — the session id travels in the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header. The agent doesn't have to be up when Welt starts; each conversation opens a fresh connection, so the agent can start, stop, or be swapped at any time.

In the reply stream, each event is one `data: {json}` SSE line carrying a JSON object. Anything else on the stream is ignored.

## Session and identity

Welt keys each Slack thread to one AgentCore session and passes the verified caller identity:

- **runtimeSessionId** — `slack_<team>_<channel>_<thread-ts>`, the timestamp's dot flattened to `-`, `_`-padded to the 33-character minimum. One thread (in channels and DMs alike) is one conversation, so an agent using AgentCore Memory continues the right one.
- **runtimeUserId** — `slack:<team>:<user>`, the Slack user Welt has verified. The agent may trust it — for example as a Memory actor key — as long as only Welt's IAM role can invoke it. Local mode sends no user id; the SDK's local server has no header for it.

The session id is also the correlation key across the boundary: it appears in Welt's own log lines, and AgentCore Observability keys its traces by the same value, so a Slack thread and the agent's trace join without a separate identifier.

## Request payload

Every request carries exactly one of two envelope keys, and key presence is the discriminator — `"messages" in payload` / `"interrupt_responses" in payload`:

| Envelope | Meaning |
|---|---|
| [`messages`](#messages--a-conversation-turn) | A conversation turn |
| [`interrupt_responses`](#interrupt_responses--resuming-a-run) | Answers resuming an interrupted run |

[`request-payload.schema.json`](../schema/request-payload.schema.json) states the payload's shapes as a JSON Schema — which roles and content blocks are allowed where, and the `format` tokens a file block can carry — and Welt's own tests check what it builds against it, so the two cannot drift. The sections below cover what a schema cannot say: what the parts mean, in what order they arrive, and where their values come from.

### `messages` — a conversation turn

The value is the conversation as [Bedrock Converse-shaped messages](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Message.html), oldest first:

```json
{
  "messages": [
    {"role": "user", "content": [{"text": "<@U0123456>: hello"}]}
  ]
}
```

- **Roles** — Welt's own earlier replies are `assistant` messages; everything else is a `user` message.
- **Attribution** — each `user` text is prefixed with the speaker's mention (`<@U0123456>: `), so the model can attribute turns in a multi-party thread.
- **History** — by default the payload carries the whole thread. When the agent keeps its own history (the operator sets `AGENT_MANAGES_HISTORY`), it carries only the messages after Welt's last reply — the ones the agent has not seen.

Slack uploads arrive as Converse `image` / `document` / `video` content blocks inside the `user` message of the reply that carried them — documents before the text block, images and videos after it (Converse rejects some block orders). JSON cannot carry raw bytes, so each block's `source.bytes` slot holds a **base64 string**, and getting it back to bytes before the model sees it is the agent side's job — whether the adapter decodes it, or hands the string to a framework whose own content shape takes base64:

```json
{"image": {"format": "png", "source": {"bytes": "<base64>"}}}
{"document": {"format": "pdf", "name": "report", "source": {"bytes": "<base64>"}}}
{"video": {"format": "mp4", "source": {"bytes": "<base64>"}}}
```

A document's `name` is its handle for the model, not the name of the file in Slack: Welt puts the Slack file name through what Converse accepts, and, because Converse rejects a request whose messages carry two documents under one name, gives a name an earlier document already took a ` (2)`, ` (3)`, … suffix. What Welt accepts from Slack and embeds is bounded by [Limits](#limits).

### `interrupt_responses` — resuming a run

The value maps each [`interrupt` event's](#interrupt) id to the answer a human gave and the widget that produced it:

```json
{
  "interrupt_responses": {
    "<id from the interrupt event>": { "value": "<the answer>", "source": "option" }
  }
}
```

`value` is any JSON value: a pressed button carries back whatever its option declared, so a question offering `{"value": false}` is answered with `false`, not with `"false"`. Submitted text is always a string. A question that declared no widget is answered by the default buttons, whose values are `true` and `false`.

`source` names the widget the answer came from — `"option"` for a pressed button, `"input"` for a submitted text field — after the reason key that declares it. It travels because only Welt can tell the two apart: a human who types what an option declared would otherwise be indistinguishable from one who pressed it.

The mapping is deliberately framework-neutral; turning it into the framework's own resume input is the adapter's job. Welt sends it only after every pending question is answered — there is no partial resume.

### Malformed payloads

Welt sends what this page and [`request-payload.schema.json`](../schema/request-payload.schema.json) describe, and its own tests hold it to that. The schema is how Welt checks itself, not a gate an adapter is expected to run against every field: an adapter may take the shape of what arrives as correct. A payload that departs from the contract is a bug on the sending side rather than an input to interpret, so failing on one — wherever the failure surfaces, in the adapter or in the framework below it — is the right outcome.

One thing an adapter does refuse: a content block of a kind this contract does not carry. A `messages` turn holds only `text`, `image`, `document`, and `video` blocks; a `toolUse` or `toolResult` block is not a malformed version of one of those but a forged conversation turn, and an adapter that rebuilt it into the agent's history would let whoever reached the runtime — not necessarily Welt — put words the model treats as its own past tool calls and their results into the run. Refusing an unknown block kind is a trust-boundary check, not the meticulous field validation the schema saves an adapter from.

What an adapter must not do is quietly turn a malformed payload into something usable:

- **Dropping a malformed message** and decoding the rest leaves the agent answering a conversation with a turn missing.
- **Reading a `messages` value that is not an array as an empty conversation** hands the agent zero turns instead of saying it understood none.
- **Decoding base64 that was never valid** yields plausible garbage, in the decoders that discard invalid characters rather than failing.

Beyond the block-kind check, none of this asks for validation of its own: what an adapter does not inspect it may pass on unchanged, leaving the framework beneath it to refuse. The rule is only that a violation must not reach the agent as a smaller, emptier, or corrupted version of itself.

This is the reverse of the [reply direction](#reply-events), where Welt ignores what it cannot render. Skipping an event it does not recognize costs nothing; every entry here is something a human said.

## Reply events

Welt renders six event keys and ignores everything else, so an event may carry more than the keys named here. One whose shape Welt cannot read (a non-string where a string is required, malformed base64) is ignored too, as is an empty `data` — the models do emit empty text deltas, and nothing renders for them.

| Event | Welt renders it as |
|---|---|
| `data` | A chunk of the streamed reply, as standard Markdown |
| `current_tool_use` | A "using tool" indicator |
| `tool_result` | Closes that tool's indicator |
| `file` | A file uploaded into the thread |
| `interrupt` | A question, as buttons and/or a text field |
| `error` | A reply failure notice |

This direction has no machine-readable specification, unlike the [request payload](#request-payload), because Welt is the receiving side of it: what an event carries is decided by the agent frameworks and AWS. A constraint stated about someone else's output holds only until the next framework, or the next version of one, so this page describes what Welt reads and how it renders that, rather than what an agent is allowed to put on the stream. The empty `data` above is the kind of thing a constraint like that gets wrong.

`error` is normally emitted by the AgentCore Runtime SDK when the agent raises mid-stream — an adapter does not need to produce it. An empty string renders as `unknown error`.

### `current_tool_use` and `tool_result`

Welt reads two fields from an invocation, and two from its result:

```json
{"current_tool_use": {"name": "get_weather", "toolUseId": "tooluse_abc"}}
{"tool_result": {"toolUseId": "tooluse_abc", "status": "success"}}
```

`name` titles the indicator; an event without one renders as "Using a tool", and a name arriving later under the same id fills it in. `toolUseId` identifies the indicator and pairs the invocation with its result — further events under one id keep updating it rather than opening another. `status` on the result, `"success"` or `"error"`, decides whether the indicator ends complete or failed. The tool's own output has no place on this stream: it belongs to the agent's conversation with the model.

### `file`

A generated file is one `file` event: `name` is the upload filename (extension included), `bytes` is the base64-encoded content — the inbound file encoding in reverse. Welt uploads each one into the Slack thread; [Files](files.md) covers the rendering, and [Limits](#limits) the size ceiling. An event whose content decodes to no bytes is skipped with a warning in Welt's log, since Slack refuses an empty upload and the failure would cost the whole reply.

### `interrupt`

A run that pauses for human input ends its stream with one `interrupt` event per pending question:

- `id` — identifies the question in the [resume payload](#interrupt_responses--resuming-a-run).
- `name` — goes to Welt's log only.
- `reason` — any JSON value; its shape alone decides the Slack rendering (see [Interrupts](interrupts.md) for how each shape looks).

A **structured reason** renders as a message with the specified widgets. It is a JSON object with `message` plus `options`, `input`, both, or neither:

```json
{
  "message": "Deploy to prod?",
  "options": [
    {"value": "approve", "label": "Deploy", "style": "primary"},
    {"value": "reject", "label": "Cancel"}
  ],
  "input": {"label": "Or tell me what to change", "multiline": false}
}
```

A pressed button answers with its `value` — any JSON value, and the answer arrives as the value it was declared as. The text submitted in the field answers with itself; whichever comes first settles the question. A button's `label` defaults to its `value` (rendered as JSON where the value is not text), and the field's to `"Answer"`. A default is taken by leaving the key out: a key carrying `null` is an omitted `label` or `style` only when the key is absent, since `null` is itself a value an option may declare.

A reason carrying `message` alone declares no widget, and is answered by the default Approve / Deny buttons — the same buttons any reason without a widget gets, a plain string and any other JSON value included. A reason asking only for `input` has declared a widget and keeps its field alone.

Matching is all-or-nothing: one malformed field, one key beyond `message` / `options` / `input`, or a value Slack cannot render — an empty `message`, an `options` key with no options at all or more than 25 of them, an option `value` whose JSON runs past 1800 characters, a `style` other than `primary` or `danger` — drops the whole reason to the fallback rendering, with no partial repair. Labels that overrun Slack's element caps are clipped instead, the way bodies are.

Those caps are Welt's rendering limits rather than parts of the vocabulary, so an adapter need not repeat them. The shape is frozen at the fields shown above; emoji, confirm dialogs, URLs and the like are beyond Welt's abstraction and will not be added.

## Limits

Inbound, the embedded file blocks stay within the Converse limits — Welt never sends more than:

| Modality | Files per conversation | Per-file size |
|---|---|---|
| `image` | 20 | 3.75 MB |
| `document` | 5 | 4.5 MB |
| `video` | 1 | 18.75 MB |

Outbound, a `file` event travels as one streamed chunk, and AgentCore Runtime caps a response chunk at **10 MB** — going over kills the stream. With base64's 4/3 growth, the practical ceiling is roughly **7 MB** of raw file, and there is no slicing protocol; for anything bigger, put the file somewhere else (for example S3) and reply with a link instead.

## Checking an implementation by hand

Nothing tests the wire end to end. Welt's tests and an adapter's tests each look at their own side, and what actually travels between them is covered by neither — so a change to either side is worth exercising by hand.

Run the agent locally (its own documentation says how) and point Welt at it by leaving `AGENT_ARN` unset. One Slack thread that streams text, calls a tool, uploads a file, and stops on an interrupt reaches every event key.

What the thread cannot show is what those events carried. Read the stream directly for that — the agent answers with raw SSE, without Welt in the way:

```sh
curl -N -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":[{"text":"what time is it?"}]}]}' \
  http://localhost:8080/invocations
```

The AgentCore SDK for TypeScript answers a request without a session id with 400, and a streaming response without an explicit `Accept` with 406; the Python SDK asks for neither:

```sh
curl -N -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'x-amzn-bedrock-agentcore-runtime-session-id: manual-check-0000000000000000000000' \
  -d '{"messages":[{"role":"user","content":[{"text":"what time is it?"}]}]}' \
  http://localhost:8080/invocations
```

The id goes in the header rather than the body, where it would show up as a payload key this contract does not have. Anything non-empty works locally; 33 characters or more keeps the same command usable against a deployment, which is where the [Runtime's minimum](#session-and-identity) applies.

Read the `data:` lines for what the events carry beyond what Welt reads. Extra fields cost bytes on every event and are the one thing the Slack side cannot show you.

## Versioning

Welt's release version is the contract's version. While Welt is 0.x, a minor release may change the wire, so an adapter release is supported with the Welt release whose minor matches it. From 1.0 on, the wire stays compatible within a major version, so an adapter release is supported with any Welt release that shares its major, and the minor versions move independently on both sides. Support is best effort either way, and other combinations come with no guarantee.
