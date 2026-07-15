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

## Request payload

Every request carries exactly one of two envelope keys, and key presence is the discriminator — `"messages" in payload` / `"interrupt_responses" in payload`:

| Envelope | Meaning |
|---|---|
| [`messages`](#messages--a-conversation-turn) | A conversation turn |
| [`interrupt_responses`](#interrupt_responses--resuming-a-run) | Answers resuming an interrupted run |

### `messages` — a conversation turn

The value is the conversation as [Bedrock Converse-shaped messages](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Message.html), oldest first:

```json
{
  "messages": [
    {"role": "user", "content": [{"text": "<@U0123456>: hello"}]}
  ]
}
```

- **Roles** — Welt's own earlier replies are `assistant` messages; everything else is a `user` message. The conversation always starts with a `user` message.
- **Attribution** — each `user` text is prefixed with the speaker's mention (`<@U0123456>: `), so the model can attribute turns in a multi-party thread.
- **History** — by default the payload carries the whole thread. When the agent keeps its own history (the operator sets `AGENT_MANAGES_HISTORY`), it carries only the messages after Welt's last reply — the ones the agent has not seen.

Slack uploads arrive as Converse `image` / `document` / `video` content blocks inside the `user` message of the reply that carried them — documents before the text block, images and videos after it (Converse rejects some block orders). JSON cannot carry raw bytes, so each block's `source.bytes` slot holds a **base64 string**, and decoding it back to bytes before the messages reach the model is the agent side's job:

```json
{"image": {"format": "png", "source": {"bytes": "<base64>"}}}
{"document": {"format": "pdf", "name": "report", "source": {"bytes": "<base64>"}}}
{"video": {"format": "mp4", "source": {"bytes": "<base64>"}}}
```

A document's `name` is pre-sanitized to what Converse accepts (alphanumerics, single spaces, hyphens, parentheses, square brackets). What Welt accepts from Slack and embeds is bounded by [Limits](#limits).

### `interrupt_responses` — resuming a run

The value maps each [`interrupt` event's](#interrupt) id to the answer a human gave — a button's `value`, or the submitted text:

```json
{
  "interrupt_responses": {
    "<id from the interrupt event>": "<the answer>"
  }
}
```

The mapping is deliberately framework-neutral; turning it into the framework's own resume input is the adapter's job. Welt sends it only after every pending question is answered — there is no partial resume.

## Reply events

Welt renders six event keys and ignores everything else, so extra framework events can stay on the stream. An event whose shape fails validation (a non-string where a string is required, malformed base64) is ignored too.

| Event | Shape | Welt renders it as |
|---|---|---|
| `data` | `{"data": "<text>"}` | A chunk of the streamed reply |
| `current_tool_use` | `{"current_tool_use": {"name": "...", "toolUseId": "..."}}` | A "using tool" indicator |
| `tool_result` | `{"tool_result": {"toolUseId": "...", "status": "success" \| "error"}}` | Closes that tool's indicator |
| `file` | `{"file": {"name": "...", "bytes": "<base64>"}}` | A file uploaded into the thread |
| `interrupt` | `{"interrupt": {"id": "...", "name": "...", "reason": <any JSON>}}` | A question, as buttons and/or a text field |
| `error` | `{"error": "<message>"}` | A reply failure notice |

`error` is normally emitted by the AgentCore Runtime SDK when the agent raises mid-stream — an adapter does not need to produce it.

### `file`

A generated file is one `file` event: `name` is the upload filename (extension included), `bytes` is the base64-encoded content — the inbound file encoding in reverse. Welt uploads each one into the Slack thread; [Files](files.md) covers the rendering, and [Limits](#limits) the size ceiling.

### `interrupt`

A run that pauses for human input ends its stream with one `interrupt` event per pending question:

- `id` — identifies the question in the [resume payload](#interrupt_responses--resuming-a-run).
- `name` — goes to Welt's log only.
- `reason` — any JSON value; its shape alone decides the Slack rendering (see [Interrupts](interrupts.md) for how each shape looks).

A **structured reason** renders as a message with the specified widgets. It is a JSON object with `message` plus `options`, `input`, or both:

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

| Field | Required | Constraints |
|---|---|---|
| `message` | yes | Non-empty string; the question body |
| `options` | one of `options` / `input` | Non-empty list of at most 25 buttons |
| `options[].value` | yes | Non-empty string, at most 1800 characters; becomes the answer |
| `options[].label` | no | Button text; defaults to `value` |
| `options[].style` | no | `"primary"` or `"danger"` only |
| `input` | one of `options` / `input` | Object; a free-text field whose submitted text becomes the answer |
| `input.label` | no | The field's label; defaults to `"Answer"` |
| `input.multiline` | no | Boolean; defaults to `false` |

Matching is all-or-nothing: one malformed field, or any key beyond the above, drops the whole reason to the fallback rendering — no partial repair. Any non-structured reason (a plain string, or any other JSON value) still renders as an answerable question with default widgets. The shapes are frozen at these fields; emoji, confirm dialogs, URLs and the like are beyond Welt's abstraction and will not be added.

## Limits

Inbound, the embedded file blocks stay within the Converse limits — Welt never sends more than:

| Modality | Files per conversation | Per-file size |
|---|---|---|
| `image` | 20 | 3.75 MB |
| `document` | 5 | 4.5 MB |
| `video` | 1 | 18.75 MB |

Outbound, a `file` event travels as one streamed chunk, and AgentCore Runtime caps a response chunk at **10 MB** — going over kills the stream. With base64's 4/3 growth, the practical ceiling is roughly **7 MB** of raw file, and there is no slicing protocol; for anything bigger, put the file somewhere else (for example S3) and reply with a link instead.

## Versioning

Welt's release version is the contract's version. While Welt is 0.x, a minor release may change the wire; adapters mirror the minor — a 0.Y adapter release implements Welt v0.Y's wire, and other combinations come with no guarantee.
