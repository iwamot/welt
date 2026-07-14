# Files

Welt supports file input and output: what people upload to the Slack thread reaches the agent as part of the conversation, and what the agent generates is uploaded back into the thread.

## Input: Slack uploads to the agent

Disabled by default. Set `FILE_INPUT_MODALITIES` to the modalities to accept:

```sh
FILE_INPUT_MODALITIES=image,document,video
```

Allow only the modalities your model accepts — see [supported foundation models](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html).

Welt downloads the files attached to the thread and embeds them into the conversation as image/document/video blocks, newest first, within the Converse ceilings — see the wire contract's [Limits](wire.md#limits). The [encoding on the wire](wire.md#messages--a-conversation-turn) is base64, and an [agent-side adapter](../README.md#agent-side-adapters) decodes it back to bytes for you — see its documentation.

## Output: agent files to the thread

A generated file arrives as one [`file` event](wire.md#file) on the reply stream, and Welt uploads it into the thread, where it appears alongside the streamed reply. An [agent-side adapter](../README.md#agent-side-adapters) emits these for you — see its documentation for how a tool attaches a file.

![An agent-generated image uploaded into the Slack thread alongside the streamed reply](images/file-upload.png)

**Size limit**: a generated file is capped by the stream's chunk ceiling — see [Limits](wire.md#limits). For anything bigger, have the agent put the file somewhere else (for example S3) and reply with a link instead.
