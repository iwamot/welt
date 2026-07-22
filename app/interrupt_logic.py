"""Pure logic for rendering agent interrupts and collecting the answers.

An interrupted agent run surfaces as `Interrupt` render events at the end of
the reply stream. Welt turns them into one button-carrying message in the
thread: per interrupt a body derived from its reason plus one button per
option, with the collection state (which interrupts are pending, which are
answered) kept in the message's own metadata so Welt stays stateless. A
button press records an answer into that state; once every pending interrupt
is answered, the recorded answers become the resume payload.

The reason contract: a reason shaped like `{"message": str, "options":
[{"value", "label"?, "style"?}, ...]}` renders as its message with the
specified buttons, and `{"message": str, "input": {"label"?, "multiline"?}}`
as its message with a free-text field (submitted with Enter, via
dispatch_action); the two can be combined, whichever answer comes first
settling the question. A string reason renders as that text; anything else
is shown as pretty-printed JSON in a code block. The two fallback renderings
get the default Approve / Deny buttons, and nothing else: a free-text field
renders only where a structured reason asks for one, so no answer can
arrive that the question never offered. Matching is all-or-nothing — one
malformed field drops the whole reason to the fallback, never a partial
repair.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.bolt_logic import INTERRUPT_ACTION_PREFIX
from app.stream_logic import Interrupt

# The metadata event type marking a message as Welt's interrupt collector.
METADATA_EVENT_TYPE = "welt_interrupt"

# Slack rendering limits. A question body renders as a markdown block, and
# the 12,000-character markdown cap is cumulative across one message, so a
# stop's questions split it evenly. Labels have per-element caps. Bodies and
# labels are clipped to fit; a structured reason with too many options falls
# back instead (a partial button row could not be answered completely, so it
# must not render).
_MARKDOWN_TEXT_MAX = 12000
_TEXT_OBJECT_MAX = 3000
_BUTTON_LABEL_MAX = 75
_MAX_OPTIONS = 25

# A button value must fit Slack's 2000-character cap together with the JSON
# envelope that carries the interrupt id alongside the option value.
_OPTION_VALUE_MAX = 1800

_ALLOWED_REASON_KEYS = frozenset({"message", "options", "input"})
_ALLOWED_OPTION_KEYS = frozenset({"value", "label", "style"})
_ALLOWED_INPUT_KEYS = frozenset({"label", "multiline"})
_BUTTON_STYLES = frozenset({"primary", "danger"})

# Slack caps an input block's label at 2000 characters.
_INPUT_LABEL_MAX = 2000

# The action_id of a free-text field: the listener-matched prefix, the kind
# marker, and the interrupt id (a text answer cannot carry the id in its
# value the way a button does — the value is whatever the human typed).
_INPUT_ACTION_ID_PREFIX = INTERRUPT_ACTION_PREFIX + "input_"


@dataclass(frozen=True)
class InterruptOption:
    """One button: the response value it submits, its label, its style."""

    value: str
    label: str
    style: str | None = None


@dataclass(frozen=True)
class InterruptInput:
    """A free-text field: its label and whether it is multiline."""

    label: str = "Answer"
    multiline: bool = False


@dataclass(frozen=True)
class InterruptPrompt:
    """The rendering of one interrupt: markdown body plus buttons or a field."""

    text: str
    options: tuple[InterruptOption, ...] = ()
    input: InterruptInput | None = None


# The fallback buttons: Approve / Deny, whose y / n values satisfy
# the default evaluator of Strands' HumanInTheLoop intervention without any
# configuration. Deliberately no free-text field: an unrequested field
# would accept answers the asking side never offered (a typed `t` silently
# trusts a tool under HumanInTheLoop, for one) — a question that wants free
# text asks for it with the structured reason's `input`.
DEFAULT_OPTIONS = (
    InterruptOption(value="y", label="Approve", style="primary"),
    InterruptOption(value="n", label="Deny"),
)


def derive_interrupt_prompt(
    reason: object, *, text_limit: int = _MARKDOWN_TEXT_MAX
) -> InterruptPrompt:
    """
    Derive an interrupt's body text and buttons from its reason.

    Only the shape of the reason decides the rendering (Welt cannot know
    what produced it): the structured shape renders as its message with the
    specified widgets, a non-empty string as that text with the default
    Approve / Deny buttons, and everything
    else as pretty-printed JSON in a code block with the same defaults.

    Args:
        reason (object): The interrupt's reason, any JSON value.
        text_limit (int): The body's character budget — this question's
            share of the message's cumulative markdown cap.

    Returns:
        InterruptPrompt: The markdown body (clipped to the budget) and the
            buttons to render.
    """
    structured = _parse_structured_reason(reason, text_limit)
    if structured is not None:
        return structured
    if isinstance(reason, str) and reason:
        return InterruptPrompt(
            text=_clip(reason, text_limit),
            options=DEFAULT_OPTIONS,
        )
    return InterruptPrompt(
        text=_fenced_json(reason, text_limit),
        options=DEFAULT_OPTIONS,
    )


def _parse_structured_reason(reason: object, text_limit: int) -> InterruptPrompt | None:
    """
    Parse a reason against the structured shape, all-or-nothing.

    A structured reason carries `message` plus `options` (choice buttons),
    `input` (a free-text field), or both — buttons with a free-text
    alternative, whichever answer comes first settling the question.

    Args:
        reason (object): The interrupt's reason, any JSON value.
        text_limit (int): The body's character budget.

    Returns:
        InterruptPrompt | None: The prompt, or None when anything about the
            shape is off — unknown keys, a missing or empty message or
            value, an option value too long for a Slack button, an unknown
            style, or more options than one actions block can hold.
    """
    if not isinstance(reason, dict):
        return None
    keys = set(reason)
    if "message" not in keys or keys == {"message"}:
        return None
    if not keys <= _ALLOWED_REASON_KEYS:
        return None
    message = reason.get("message")
    if not isinstance(message, str) or not message:
        return None
    input_field = None
    if "input" in keys:
        input_field = _parse_input_field(reason.get("input"))
        if input_field is None:
            return None
    options: tuple[InterruptOption, ...] = ()
    if "options" in keys:
        parsed_options = _parse_options(reason.get("options"))
        if parsed_options is None:
            return None
        options = parsed_options
    return InterruptPrompt(
        text=_clip(message, text_limit),
        options=options,
        input=input_field,
    )


def _parse_options(options: object) -> tuple[InterruptOption, ...] | None:
    """
    Parse a structured reason's `options` field, all-or-nothing.

    Args:
        options (object): The `options` value of a structured reason.

    Returns:
        tuple[InterruptOption, ...] | None: The options, or None when the
            shape is off.
    """
    if not isinstance(options, list) or not 0 < len(options) <= _MAX_OPTIONS:
        return None
    parsed: list[InterruptOption] = []
    for option in options:
        if not isinstance(option, dict) or not set(option) <= _ALLOWED_OPTION_KEYS:
            return None
        value = option.get("value")
        if not isinstance(value, str) or not 0 < len(value) <= _OPTION_VALUE_MAX:
            return None
        label = option.get("label", value)
        if not isinstance(label, str) or not label:
            return None
        style = option.get("style")
        if style is not None:
            if not isinstance(style, str) or style not in _BUTTON_STYLES:
                return None
        parsed.append(InterruptOption(value=value, label=label, style=style))
    return tuple(parsed)


def _parse_input_field(input_spec: object) -> InterruptInput | None:
    """
    Parse a structured reason's `input` field, all-or-nothing.

    Args:
        input_spec (object): The `input` value of a structured reason.

    Returns:
        InterruptInput | None: The field, or None when the shape is off —
            not a dict, unknown keys, an empty or non-string label, or a
            non-boolean multiline flag.
    """
    if not isinstance(input_spec, dict) or not set(input_spec) <= _ALLOWED_INPUT_KEYS:
        return None
    label = input_spec.get("label", "Answer")
    if not isinstance(label, str) or not label:
        return None
    multiline = input_spec.get("multiline", False)
    if not isinstance(multiline, bool):
        return None
    return InterruptInput(label=label, multiline=multiline)


def _clip(text: str, limit: int) -> str:
    """
    Clip text to a length limit, marking the cut with an ellipsis.

    Args:
        text (str): The text to clip.
        limit (int): The maximum length in characters.

    Returns:
        str: The text, unchanged if it fits.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _fenced_json(reason: object, text_limit: int) -> str:
    """
    Render a reason as pretty-printed JSON in a markdown code block.

    Args:
        reason (object): The interrupt's reason; guaranteed JSON-native,
            since it was decoded from the JSON wire.
        text_limit (int): The body's character budget, fence included.

    Returns:
        str: The fenced code block, its content clipped so the whole body
            (fence included) fits the budget.
    """
    dumped = json.dumps(reason, ensure_ascii=False, indent=2)
    budget = text_limit - len("```\n\n```")
    return f"```\n{_clip(dumped, budget)}\n```"


def build_interrupt_blocks(interrupts: Sequence[Interrupt]) -> list[dict]:
    """
    Build the blocks of the button message for a stop's interrupts.

    Per interrupt, a markdown block carrying the body derived from its
    reason — the questions split the message's cumulative markdown budget
    evenly — followed by its answering widget: an actions block with its
    buttons, or an input block with its free-text field (dispatch_action,
    so Enter submits a block_actions payload just like a button press). A
    press alone must identify which question was answered with what — a button
    carries the interrupt id and the option value in its `value`, a text
    field carries the interrupt id in its action_id (its value is whatever
    the human typed). Every action_id starts with the listener-matched
    prefix.

    Args:
        interrupts (Sequence[Interrupt]): The interrupts of one stop.

    Returns:
        list[dict]: The Block Kit blocks for chat.postMessage.
    """
    blocks: list[dict] = []
    for index, interrupt in enumerate(interrupts):
        prompt = derive_interrupt_prompt(
            interrupt.reason, text_limit=_MARKDOWN_TEXT_MAX // len(interrupts)
        )
        blocks.append({"type": "markdown", "text": prompt.text})
        # One question can render several widget blocks (buttons plus a
        # free-text alternative). Slack rejects duplicate block_ids within
        # a message, so each widget gets its own id; the shared group stem
        # lets the first answer retire them together.
        group_id = f"{INTERRUPT_ACTION_PREFIX}q_{index}"
        if prompt.options:
            elements: list[dict] = []
            for option_index, option in enumerate(prompt.options):
                element: dict = {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": _clip(option.label, _BUTTON_LABEL_MAX),
                    },
                    "action_id": f"{INTERRUPT_ACTION_PREFIX}{index}_{option_index}",
                    "value": json.dumps(
                        {"iid": interrupt.id, "v": option.value},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
                if option.style is not None:
                    element["style"] = option.style
                elements.append(element)
            blocks.append(
                {
                    "type": "actions",
                    "block_id": f"{group_id}_options",
                    "elements": elements,
                }
            )
        if prompt.input is not None:
            blocks.append(
                {
                    "type": "input",
                    "block_id": f"{group_id}_input",
                    "dispatch_action": True,
                    "label": {
                        "type": "plain_text",
                        "text": _clip(prompt.input.label, _INPUT_LABEL_MAX),
                    },
                    "element": {
                        "type": "plain_text_input",
                        "action_id": f"{_INPUT_ACTION_ID_PREFIX}{interrupt.id}",
                        "multiline": prompt.input.multiline,
                        "dispatch_action_config": {
                            "trigger_actions_on": ["on_enter_pressed"]
                        },
                    },
                }
            )
    return blocks


def parse_action_answer(action: object) -> tuple[str, str] | None:
    """
    Decode one pressed action into its interrupt id and answer.

    Both answering widgets arrive as block_actions: a button press carries
    Welt's envelope in the action's `value`, a submitted text field carries
    the interrupt id in its action_id and the typed text as its `value`.

    Args:
        action (object): The pressed action from the block_actions payload.

    Returns:
        tuple[str, str] | None: The interrupt id and the answer, or None
            when the action is not one of Welt's answering widgets (or the
            submitted text is empty — nothing to answer with).
    """
    if not isinstance(action, dict):
        return None
    if action.get("type") == "plain_text_input":
        action_id = action.get("action_id")
        if not isinstance(action_id, str):
            return None
        if not action_id.startswith(_INPUT_ACTION_ID_PREFIX):
            return None
        interrupt_id = action_id[len(_INPUT_ACTION_ID_PREFIX) :]
        value = action.get("value")
        if not interrupt_id or not isinstance(value, str) or not value:
            return None
        return interrupt_id, value
    return parse_button_value(action.get("value"))


def parse_button_value(value: object) -> tuple[str, str] | None:
    """
    Decode a pressed button's value into its interrupt id and option value.

    Args:
        value (object): The `value` of the pressed action, as built by
            `build_interrupt_blocks`.

    Returns:
        tuple[str, str] | None: The interrupt id and the selected option
            value, or None when the value is not Welt's envelope (a button
            from some other message, or a mangled payload).
    """
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    interrupt_id = decoded.get("iid")
    choice = decoded.get("v")
    if not isinstance(interrupt_id, str) or not interrupt_id:
        return None
    if not isinstance(choice, str):
        return None
    return interrupt_id, choice


def initial_collection_state(interrupts: Sequence[Interrupt]) -> dict:
    """
    Build the collection state for a freshly posted button message.

    Args:
        interrupts (Sequence[Interrupt]): The interrupts of one stop.

    Returns:
        dict: The state — every interrupt pending, no answers yet.
    """
    return {"pending": [interrupt.id for interrupt in interrupts], "answers": {}}


def build_collection_metadata(state: dict) -> dict:
    """
    Wrap a collection state as Slack message metadata.

    Args:
        state (dict): The collection state (pending ids and answers).

    Returns:
        dict: The `metadata` argument for chat.postMessage / chat.update.
    """
    return {"event_type": METADATA_EVENT_TYPE, "event_payload": state}


def parse_collection_state(message: object) -> dict | None:
    """
    Read the collection state back out of a button message.

    Args:
        message (object): The message object (from the block_actions
            payload, or fetched with include_all_metadata).

    Returns:
        dict | None: The validated state, or None when the message carries
            no intact welt_interrupt metadata.
    """
    if not isinstance(message, dict):
        return None
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if metadata.get("event_type") != METADATA_EVENT_TYPE:
        return None
    state = metadata.get("event_payload")
    if not isinstance(state, dict):
        return None
    pending = state.get("pending")
    answers = state.get("answers")
    if not isinstance(pending, list) or not pending:
        return None
    if not all(
        isinstance(interrupt_id, str) and interrupt_id for interrupt_id in pending
    ):
        return None
    if not isinstance(answers, dict):
        return None
    return {"pending": pending, "answers": answers}


def record_answer(
    state: dict, *, interrupt_id: str, value: str, user_id: str
) -> dict | None:
    """
    Record one button press into a collection state.

    A repeated press for the same interrupt overwrites the earlier answer;
    near-simultaneous presses can still lose one update, which the presser
    recovers by pressing again (accepted, documented).

    Args:
        state (dict): The current collection state.
        interrupt_id (str): The interrupt the pressed button belongs to.
        value (str): The option value the press selected.
        user_id (str): The Slack user id of the presser, for the audit
            trail in the metadata.

    Returns:
        dict | None: The new state, or None when the interrupt id is not
            one this message is collecting.
    """
    if interrupt_id not in state["pending"]:
        return None
    answers = dict(state["answers"])
    answers[interrupt_id] = {"value": value, "user": user_id}
    return {"pending": state["pending"], "answers": answers}


def is_fully_answered(state: dict) -> bool:
    """
    Check whether every pending interrupt has an answer.

    Args:
        state (dict): The collection state.

    Returns:
        bool: True when the collected answers cover all pending ids.
    """
    answers = state["answers"]
    return all(interrupt_id in answers for interrupt_id in state["pending"])


def build_interrupt_responses(state: dict) -> dict:
    """
    Build the resume payload's `interrupt_responses` from a full state.

    A plain mapping of interrupt id to the chosen answer — Welt's own
    vocabulary, deliberately framework-neutral; turning it into a
    framework's resume input is the agent-side adapter's job.

    Args:
        state (dict): The fully answered collection state.

    Returns:
        dict: The answer value per interrupt id, in pending order.
    """
    answers = state["answers"]
    return {
        interrupt_id: _answer_value(answers.get(interrupt_id))
        for interrupt_id in state["pending"]
    }


def _answer_value(answer: object) -> str:
    """
    Extract the selected value from one recorded answer.

    Args:
        answer (object): An entry of the state's `answers` (Welt-written,
            but round-tripped through Slack metadata, so shape-checked).

    Returns:
        str: The recorded option value, or an empty string when the entry
            lost its shape in the round trip.
    """
    if isinstance(answer, dict):
        value = answer.get("value")
        if isinstance(value, str):
            return value
    return ""


# The per-widget block_id suffixes of build_interrupt_blocks, named after
# the reason contract's widget keys.
_WIDGET_ID_SUFFIXES = ("_options", "_input")


def _widget_group(block_id: object) -> str | None:
    """
    Extract a widget block_id's question-group stem.

    Widget block_ids are unique within a message (Slack rejects
    duplicates), so a question's widgets share a group stem plus a
    per-widget suffix. Comparing stems for equality — rather than prefix
    matching — keeps `q_1` from also claiming `q_10`'s widgets.

    Args:
        block_id (object): A block's block_id, if any.

    Returns:
        str | None: The stem before the widget suffix, or None for ids
            without one (foreign blocks, or none at all).
    """
    if not isinstance(block_id, str):
        return None
    for suffix in _WIDGET_ID_SUFFIXES:
        if block_id.endswith(suffix):
            return block_id[: -len(suffix)]
    return None


def replace_answered_blocks(
    blocks: object, *, action_id: str, presser_name: str, answer: str
) -> list | None:
    """
    Rewrite a button message's blocks after a question is answered.

    The question's widget blocks become one context line carrying the
    answer — the pressed button's label, or the submitted text — and who
    gave it: the visible receipt, and the guard against double answers
    (the widgets are gone). A question rendering both buttons and a text
    field retires them together, whichever answered first. The line is
    plain text (no escaping needed), and the answerer deliberately not a
    mention. Other blocks (including other questions' still-pending
    widgets) are kept as they are.

    Args:
        blocks (object): The message's current blocks.
        action_id (str): The action_id of the answered widget.
        presser_name (str): The answerer's display name.
        answer (str): The decoded answer, echoed for a text field.

    Returns:
        list | None: The new blocks, or None when no block carries the
            answered widget (already replaced, or a foreign message).
    """
    if not isinstance(blocks, list):
        return None
    target_index = None
    label = None
    for index, block in enumerate(blocks):
        label = _receipt_label(block, action_id, answer)
        if label is not None:
            target_index = index
            break
    if target_index is None or label is None:
        return None
    target = blocks[target_index]
    group_id = (
        _widget_group(target.get("block_id")) if isinstance(target, dict) else None
    )
    updated: list = []
    inserted = False
    for index, block in enumerate(blocks):
        in_group = index == target_index or (
            group_id is not None
            and isinstance(block, dict)
            and _widget_group(block.get("block_id")) == group_id
        )
        if not in_group:
            updated.append(block)
            continue
        if inserted:
            continue  # the question's other widget, retired with the first
        updated.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "plain_text",
                        # Plain-text objects cap at 3000 characters, in a
                        # context line as anywhere else.
                        "text": _clip(
                            f"“{label}” — answered by {presser_name}",
                            _TEXT_OBJECT_MAX,
                        ),
                    }
                ],
            }
        )
        inserted = True
    return updated


def append_context_notice(blocks: Sequence, text: str) -> list:
    """
    Append a context-line notice to a message's blocks.

    Used for the resume-failure notice under the approval buttons — the
    same understated visual language as the presser line, plain text so
    nothing in it parses as a mention.

    Args:
        blocks (Sequence): The message's current blocks.
        text (str): The notice text.

    Returns:
        list: The blocks with the notice appended.
    """
    return [
        *blocks,
        {"type": "context", "elements": [{"type": "plain_text", "text": text}]},
    ]


def _receipt_label(block: object, action_id: str, answer: str) -> str | None:
    """
    Derive the receipt text if this block holds the answered widget.

    Args:
        block (object): One block of the message.
        action_id (str): The action_id of the answered widget.
        answer (str): The decoded answer.

    Returns:
        str | None: The pressed button's label, the submitted text for a
            text field, or None when this block does not hold the widget.
    """
    if not isinstance(block, dict):
        return None
    if block.get("type") == "input":
        element = block.get("element")
        if isinstance(element, dict) and element.get("action_id") == action_id:
            return answer
        return None
    if block.get("type") != "actions":
        return None
    elements = block.get("elements")
    if not isinstance(elements, list):
        return None
    for element in elements:
        if not isinstance(element, dict) or element.get("action_id") != action_id:
            continue
        text = element.get("text")
        label = text.get("text") if isinstance(text, dict) else None
        return label if isinstance(label, str) and label else "Selected"
    return None


def pick_display_name(user: object) -> str | None:
    """
    Pick a human-readable name from a users.info user object.

    Args:
        user (object): The `user` value of a users.info response.

    Returns:
        str | None: The profile display name, falling back to the profile
            real name, then the top-level real name and username; None when
            nothing usable is present.
    """
    if not isinstance(user, dict):
        return None
    profile = user.get("profile")
    if isinstance(profile, dict):
        for key in ("display_name", "real_name"):
            name = profile.get(key)
            if isinstance(name, str) and name:
                return name
    for key in ("real_name", "name"):
        name = user.get(key)
        if isinstance(name, str) and name:
            return name
    return None
