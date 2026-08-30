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

A harness ARN in `AGENT_ARN` is neither: it selects `InvokeHarness`, the exception above.

Local mode targets the surface the AgentCore SDK's local server provides — the session id travels in the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header. The agent doesn't have to be up when Welt starts; each conversation opens a fresh connection, so the agent can start, stop, or be swapped at any time.

In the reply stream, each event is one `data: {json}` SSE line carrying a JSON object. Anything else on the stream is ignored.

## Session and identity

Welt keys each Slack thread to one AgentCore session and passes the verified caller identity:

- **runtimeSessionId** — `slack_<team>_<channel>_<thread-ts>`, the timestamp's dot flattened to `-`, `_`-padded to the 33-character minimum. One thread (in channels and DMs alike) is one conversation, so an agent using AgentCore Memory continues the right one.
- **runtimeUserId** — `slack:<team>:<user>`, the Slack user Welt has verified. The agent may trust it — for example as a Memory actor key — as long as only Welt's IAM role can invoke it. Local mode sends no user id; the SDK's local server has no header for it.

The session id is also the correlation key across the boundary: it appears in Welt's own log lines, and AgentCore Observability carries it as the `session.id` attribute on the `InvokeAgentRuntime` span (and as `session_id` in the application logs), so a Slack thread and the agent's trace join without a separate identifier.

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
    {"role": "user", "content": [{"text": "@iwamot: hello"}]}
  ]
}
```

- **Roles** — Welt's own earlier replies are `assistant` messages; everything else is a `user` message.
- **Attribution** — each `user` text is prefixed with the speaker's name (`@iwamot: `), so the model can attribute turns in a multi-party thread. Whoever a message mentions is named the same way, Welt included: a thread whose parent called Welt runs on every reply, so the mention is what says which of them were addressed to it. Names come from the Slack profiles, and an app is named as the app (`@GitHub`); an ID that resolves to no name is written as the ID (`@U0123456`). Nothing states that a name is the agent's own — what says so is that its turn follows every message naming it. An `assistant` message carries no prefix, so nothing invites the model to write one.

  Only a person's mention is read this way. What Slack writes for a user group (`<!subteam^S0123>`), for a broadcast (`<!here>`) and for a channel (`<#C0123>`) is left as it came: each says on its own what it points at, where an ID says nothing without a lookup.
- **What a turn says** — the Markdown of the message as the thread shows it, read back from its blocks: headings, tables, quotes and fenced code come back as they were written. What hangs off a message as `attachments` — an app's whole notification, or Slack's unfurling of a link somebody pasted — is read under it, and a message whose blocks and attachments carry no words at all is read from its text instead.

  What the thread shows but does not say is named in brackets, so it does not read as something the sender wrote:

  | | |
  |---|---|
  | `[file: chart.png]` | a file the message shows, whether or not its bytes travel as a content block |
  | `[file: Weekly report (sample.csv)]` | a file given a title of its own, which is what the thread shows over its name |
  | `[task: Using search]` | what the reply did before answering, as the thread showed it happening; any status other than complete follows a dash (`— error` where it failed, `— in progress` where the reply is still running, or ended, before the tool came back); what a tool was given and what it returned the thread does not show, and neither travels |
  | `[buttons: Publish \| Cancel]` | a question still waiting to be answered; answering removes the widgets |
  | `[menu: Pick a branch]` | a menu still waiting to be picked from |
  | `[image: https://…]` | a picture the message shows but does not carry |
  | `[video: Release demo](https://…)` | a video the message embeds, linked where it says where it is, its description under it |
  | `[input: Or say why not]` | a field still waiting to be typed in |
  | `[context: “Publish” — answered by iwamot]` | the aside Slack draws small and grey under a message — a receipt, a notice |
- **History** — by default the payload carries the whole thread. When the agent keeps its own history (the operator sets `AGENT_MANAGES_HISTORY`), it carries only the messages after Welt's last reply — the ones the agent has not seen.

Slack uploads arrive as Converse `image` / `document` / `video` content blocks inside the `user` message of the reply that carried them — documents before the text block, images and videos after it. That order is Welt's own; Converse accepts any. What it does require is that a message carrying a document carry a text block as well, which the attribution prefix above keeps there. JSON cannot carry raw bytes, so each block's `source.bytes` slot holds a **base64 string**, and getting it back to bytes before the model sees it is the agent side's job — whether the adapter decodes it, or hands the string to a framework whose own content shape takes base64:

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

The mapping is deliberately framework-neutral; turning it into the framework's own resume input is the adapter's job. Welt sends it only after every pending question is answered — there is no partial resume, since most of the frameworks the adapters cover resume a run as a whole rather than a question at a time.

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

A **structured reason** renders as a message with the specified widgets. It is a JSON object with `message` plus any of `approve`, `reject`, `options`, and `input` — a key's presence asks for its widget, and its value says how that widget looks:

```json
{
  "message": "Deploy to prod?",
  "approve": {"label": "Deploy"},
  "reject": {"label": "Cancel"},
  "options": [{"value": "later", "label": "Ask me later"}],
  "input": {"label": "Or tell me what to change", "multiline": false}
}
```

`approve` and `reject` are the two buttons Welt words and values itself: they answer with `true` and `false`, the same values the default buttons send, so an agent asking for a decision does not have to invent a vocabulary for it. Their `label` and `style` default to Welt's own — `Approve` (primary) and `Reject` (danger) — and a reason may override either. Ask for both, one, or neither.

`options` are buttons the reason words and values itself. A pressed button answers with its `value` — any JSON value, and the answer arrives as the value it was declared as. The text submitted in the field answers with itself; whichever answer comes first settles the question. An option's `label` defaults to its `value` (rendered as JSON where the value is not text), and the field's to `"Answer"`. A default is taken by leaving the key out: a key carrying `null` is an omitted `label` or `style` only when the key is absent, since `null` is itself a value an option may declare.

Buttons render in one row, `approve` and `reject` ahead of the reason's own.

A reason carrying `message` alone declares no widget, and is answered by the default Approve / Reject buttons — the same buttons any reason without a widget gets, a plain string and any other JSON value included. A reason asking only for `input` has declared a widget and keeps its field alone, and one asking only for `approve` keeps that button alone.

Matching is all-or-nothing: one malformed field, one key beyond `message` / `approve` / `reject` / `options` / `input`, or a value Slack cannot render — an empty `message`, an `options` key with no options at all, more than 25 buttons across all of them, an option `value` whose JSON runs past 1800 characters, a `style` other than `primary` or `danger`, an option answering with the `true` or `false` a named decision already answers with — drops the whole reason to the fallback rendering, with no partial repair. Labels that overrun Slack's element caps are clipped instead, the way bodies are.

Those caps are Welt's rendering limits rather than parts of the vocabulary, so an adapter need not repeat them. The shape is frozen at the fields shown above; emoji, confirm dialogs, URLs and the like are beyond Welt's abstraction and will not be added.

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

## Limits

Inbound, Welt never embeds more than this per conversation:

| Modality | Files | Per-file size | Enforced by |
|---|---|---|---|
| `image` | 20 | 3,932,160 bytes | the model — Anthropic's is the strictest, capping the base64 form at 5 MiB. Converse checks neither the count nor the size, so the count is Welt's own stopping point |
| `document` | 5 | 4,500,000 bytes | Converse, on both. `4.5 MB` here means 4,500,000, not 4.5 MiB |
| `video` | 1 | 18,750,000 bytes | Nova, the only family that reads video, capping the base64 form at 25,000,000 |

One payload as a whole stays under **30,000,000 bytes**, text and encoded files together. Converse refuses a request whose body passes 32,000,000 — the same boundary on both model families — and the distance is deliberate rather than a margin for error: a thread carrying this much already takes long enough to send that the last two megabytes buy nothing. A thread over the budget is trimmed from its oldest end, an attachment at a time: the oldest message gives up its last attachment, then the rest of them backwards, then itself, and only then does the next-oldest message give up anything. What a thread loses first is old pictures, not what anyone said, and what a message loses first is what was attached to it last. The newest message is never dropped, and nothing is announced in the thread.

The three per-file ceilings are quoted in bytes because the three sources count in three different units. Every number here was measured on 2026-08-26 through Converse, against `nova-lite` and `claude-haiku-4-5`. An agent that reaches its model through some other API meets that API's ceilings instead: Bedrock's OpenAI-compatible endpoint, measured the same day, refuses a request past about 32 MiB, which leaves the budget above clear of both. A model outside those families, or an API neither of these covers, may be stricter, and its refusal arrives in the thread as an `error` event rather than as anything Welt could have withheld. None of these is a promise the next one keeps.

Outbound, a `file` event travels as one streamed chunk, and AgentCore Runtime caps a response chunk at **10 MB** — going over kills the stream. That figure is AgentCore's published quota rather than something measured here, and the page it comes from counts a megabyte as a million bytes: its 100 MB request payload is the 100,000,000 the API model states. With base64's 4/3 growth, the practical ceiling is roughly **7 MB** of raw file, and there is no slicing protocol; for anything bigger, put the file somewhere else (for example S3) and reply with a link instead.

## Versioning

Welt's release version is the contract's version. While Welt is 0.x, a minor release may change the wire, so an adapter release is supported with the Welt release whose minor matches it. From 1.0 on, the wire stays compatible within a major version, so an adapter release is supported with any Welt release that shares its major, and the minor versions move independently on both sides. Support is best effort either way, and other combinations come with no guarantee.
