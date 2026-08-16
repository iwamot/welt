"""Pure logic for rendering agent interrupts and collecting the answers.

An interrupted agent run surfaces as `Interrupt` render events at the end of
the reply stream. Welt turns them into one button-carrying message in the
thread: per interrupt a body derived from its reason plus one button per
option, with the collection state (which interrupts are pending, which are
answered) kept in the message's own metadata so Welt stays stateless. A
button press records an answer into that state; once every pending interrupt
is answered, the recorded answers become the resume payload.

The reason contract: a reason shaped like `{"message": str, ...}` renders as
its message with the widgets its remaining keys ask for. `approve` and
`reject` (`{"label"?, "style"?}`) are the two buttons Welt words and values
itself; `options` (`[{"value", "label"?, "style"?}, ...]`) are buttons the
reason words and values itself; `input` (`{"label"?, "multiline"?}`) is a
free-text field (submitted with Enter, via dispatch_action). Any of them
combine, whichever answer comes first settling the question, and buttons
render approve, reject, then the reason's own. A string reason renders as
that text; anything else is shown as pretty-printed JSON in a code block.
Matching is all-or-nothing — one malformed field drops the whole reason to
the fallback, never a partial repair.

A reason that declares no widget at all — a string, a bare `{"message":
...}`, any other JSON — gets the default Approve / Reject buttons, since a
question with no way to answer it would never be answered. The defaults
are buttons and nothing else: a free-text field renders only where a
structured reason asks for one, so no answer can arrive that the question
never offered.

An option's value is any JSON value, and the answer it submits arrives
back the way it was declared. Slack carries a button's value as a string,
but that string is Welt's JSON envelope, so the declared value crosses
unchanged rather than flattened to text.

An answer also carries the widget that produced it — `option` or `input`
— since the two are told apart here, at the listener that received the
press, and nowhere downstream: a typed answer that reads like an option's
value is otherwise indistinguishable from the press of that option.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

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
# envelope that carries the interrupt id alongside the option value. The
# budget is spent by the option value's JSON, not by its characters: what
# Slack carries is the serialized form.
_OPTION_VALUE_MAX = 1800

_ALLOWED_REASON_KEYS = frozenset({"message", "approve", "reject", "options", "input"})
_ALLOWED_OPTION_KEYS = frozenset({"value", "label", "style"})
_ALLOWED_DECISION_KEYS = frozenset({"label", "style"})
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

    value: object
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


# The default buttons: Approve / Reject for a question that declared no
# widget of its own. Their values are booleans because the code reading
# them is code the agent's author did not write and cannot configure — a
# question reaches these buttons precisely because nothing declared what
# to send back. Booleans are what such code expects: Strands' steering
# annotates the response `bool` and tests it for truthiness (where every
# non-empty string, `"n"` included, reads as approval), and the default
# evaluator of its HumanInTheLoop intervention accepts True by identity.
# Deliberately no free-text field: an unrequested field would accept
# answers the asking side never offered — a question that wants free text
# asks for it with the structured reason's `input`.
DEFAULT_OPTIONS = (
    InterruptOption(value=True, label="Approve", style="primary"),
    InterruptOption(value=False, label="Reject", style="danger"),
)

# The same two buttons under the names a reason declares them by. A
# reason asking for one takes this rendering unless it says otherwise,
# which is what lets an adapter ask for approval without deciding how
# approval is worded.
_DECISION_DEFAULTS = {
    "approve": DEFAULT_OPTIONS[0],
    "reject": DEFAULT_OPTIONS[1],
}


def derive_interrupt_prompt(
    reason: object, *, text_limit: int = _MARKDOWN_TEXT_MAX
) -> InterruptPrompt:
    """
    Derive an interrupt's body text and buttons from its reason.

    Only the shape of the reason decides the rendering (Welt cannot know
    what produced it): the structured shape renders as its message, a
    non-empty string as that text, and everything else as pretty-printed
    JSON in a code block.

    The widgets follow from what the reason declared, and a reason that
    declared none gets the default buttons — the one rule covering a
    string, a bare `{"message": ...}`, and any other JSON alike. A reason
    asking only for a free-text field has declared one, and keeps it
    alone. Asking for `approve` or `reject` is asking for a default
    button by name, so a reason that named one keeps only what it named.

    Args:
        reason (object): The interrupt's reason, any JSON value.
        text_limit (int): The body's character budget — this question's
            share of the message's cumulative markdown cap.

    Returns:
        InterruptPrompt: The markdown body (clipped to the budget) and the
            widgets to render.
    """
    prompt = _parse_structured_reason(reason, text_limit)
    if prompt is None:
        if isinstance(reason, str) and reason:
            prompt = InterruptPrompt(text=_clip(reason, text_limit))
        else:
            prompt = InterruptPrompt(text=_fenced_json(reason, text_limit))
    if not prompt.options and prompt.input is None:
        return replace(prompt, options=DEFAULT_OPTIONS)
    return prompt


def _parse_structured_reason(reason: object, text_limit: int) -> InterruptPrompt | None:
    """
    Parse a reason against the structured shape, all-or-nothing.

    A structured reason carries `message` plus any of `approve` and
    `reject` (the two buttons Welt words and values itself), `options`
    (buttons the reason words and values itself), and `input` (a free-text
    field) — or none of them, which is a message the caller wants rendered
    as itself and leaves the answering to the default buttons. Whichever
    answer comes first settles the question.

    A key's presence is what asks for its widget; its value says how that
    widget looks. Buttons render in the order the keys are named here,
    `approve` and `reject` ahead of the reason's own.

    Args:
        reason (object): The interrupt's reason, any JSON value.
        text_limit (int): The body's character budget.

    Returns:
        InterruptPrompt | None: The prompt, or None when anything about the
            shape is off — unknown keys, a missing or empty message, a
            missing value, an option value too long for a Slack button, an
            unknown style, an option answering with the value `approve` or
            `reject` already answers with, or more buttons than one actions
            block can hold.
    """
    if not isinstance(reason, dict):
        return None
    keys = set(reason)
    if "message" not in keys:
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
    decisions: list[InterruptOption] = []
    for key, default in _DECISION_DEFAULTS.items():
        if key not in keys:
            continue
        decision = _parse_decision(reason.get(key), default)
        if decision is None:
            return None
        decisions.append(decision)
    options: tuple[InterruptOption, ...] = ()
    if "options" in keys:
        parsed_options = _parse_options(reason.get("options"))
        if parsed_options is None:
            return None
        options = parsed_options
    buttons = tuple(decisions) + options
    if len(buttons) > _MAX_OPTIONS:
        return None
    # Two buttons answering with one value leave the answer ambiguous, and
    # the reason that wrote both cannot have meant either.
    taken = {decision.value for decision in decisions}
    if any(
        isinstance(option.value, bool) and option.value in taken for option in options
    ):
        return None
    return InterruptPrompt(
        text=_clip(message, text_limit),
        options=buttons,
        input=input_field,
    )


def _parse_decision(spec: object, default: InterruptOption) -> InterruptOption | None:
    """
    Parse a structured reason's `approve` or `reject` field.

    The value it answers with is Welt's, not the reason's: these are the
    buttons an agent asks for when the decision is approval, so the answer
    is the same `true` / `false` the default buttons send and no adapter
    has to invent a vocabulary for it. What the reason may say is how the
    button looks, and saying nothing takes Welt's wording.

    Args:
        spec (object): The `approve` or `reject` value of a structured
            reason.
        default (InterruptOption): Welt's rendering of that button.

    Returns:
        InterruptOption | None: The button, or None when the shape is off —
            not a dict, unknown keys, an empty or non-string label, or an
            unknown style.
    """
    if not isinstance(spec, dict) or not set(spec) <= _ALLOWED_DECISION_KEYS:
        return None
    label = spec.get("label", default.label)
    if not isinstance(label, str) or not label:
        return None
    style = default.style
    if "style" in spec:
        given = spec.get("style")
        if not isinstance(given, str) or given not in _BUTTON_STYLES:
            return None
        style = given
    return InterruptOption(value=default.value, label=label, style=style)


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
        # Read by presence: an explicit null is a value the option declared,
        # an absent key is an option with nothing to submit.
        if "value" not in option:
            return None
        value = option["value"]
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) > _OPTION_VALUE_MAX:
            return None
        # An option without a label is labelled by its value: as itself
        # when it is text, as the JSON it is otherwise.
        label = option.get("label", value if isinstance(value, str) else rendered)
        if not isinstance(label, str) or not label:
            return None
        # Read by presence, not by None: an explicit null is a malformed
        # style rather than an omitted one, as it is for label and multiline.
        style: str | None = None
        if "style" in option:
            given = option.get("style")
            if not isinstance(given, str) or given not in _BUTTON_STYLES:
                return None
            style = given
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


def parse_action_answer(action: object) -> tuple[str, object, str] | None:
    """
    Decode one pressed action into its interrupt id and answer.

    Both answering widgets arrive as block_actions: a button press carries
    Welt's envelope in the action's `value`, a submitted text field carries
    the interrupt id in its action_id and the typed text as its `value`.

    Args:
        action (object): The pressed action from the block_actions payload.

    Returns:
        tuple[str, object, str] | None: The interrupt id, the answer — the
            option's declared value for a button, the typed text for a
            field — and the widget it came from, or None when the action is
            not one of Welt's answering widgets (or the submitted text is
            empty — nothing to answer with).
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
        return interrupt_id, value, "input"
    pressed = parse_button_value(action.get("value"))
    if pressed is None:
        return None
    interrupt_id, choice = pressed
    return interrupt_id, choice, "option"


def parse_button_value(value: object) -> tuple[str, object] | None:
    """
    Decode a pressed button's value into its interrupt id and option value.

    Slack carries the envelope as a string, but the option value inside it
    is read back as the JSON value the option declared — a boolean stays a
    boolean, which is what makes the default buttons answerable by code
    that tests the answer rather than compares it.

    Args:
        value (object): The `value` of the pressed action, as built by
            `build_interrupt_blocks`.

    Returns:
        tuple[str, object] | None: The interrupt id and the selected option
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
    if not isinstance(interrupt_id, str) or not interrupt_id:
        return None
    # Read by presence: the envelope always carries `v`, and the value it
    # carries may legitimately be null.
    if "v" not in decoded:
        return None
    return interrupt_id, decoded["v"]


# The widgets an answer can come from, named after the reason keys that
# declare them. The default buttons are the options Welt supplies for a
# reason that declared none, so their answers are `option` answers too.
ANSWER_SOURCES = ("option", "input")


@dataclass(frozen=True)
class Answer:
    """One recorded answer: what it chose, where from, and who gave it."""

    value: object
    source: str
    user: str


@dataclass(frozen=True)
class CollectionState:
    """What a button message has asked and collected so far.

    Welt keeps no state of its own, so this rides in the message's own
    metadata between presses. It is Welt-written, but a round trip through
    Slack is still a round trip through the outside world, which is why
    `parse_collection_state` — the only way back in — revalidates it.
    """

    pending: tuple[str, ...]
    answers: Mapping[str, Answer]


def initial_collection_state(interrupts: Sequence[Interrupt]) -> CollectionState:
    """
    Build the collection state for a freshly posted button message.

    Args:
        interrupts (Sequence[Interrupt]): The interrupts of one stop.

    Returns:
        CollectionState: The state — every interrupt pending, no answers yet.
    """
    return CollectionState(
        pending=tuple(interrupt.id for interrupt in interrupts), answers={}
    )


def build_collection_metadata(state: CollectionState) -> dict:
    """
    Wrap a collection state as Slack message metadata.

    Args:
        state (CollectionState): The collection state to carry.

    Returns:
        dict: The `metadata` argument for chat.postMessage / chat.update.
    """
    return {
        "event_type": METADATA_EVENT_TYPE,
        "event_payload": {
            "pending": list(state.pending),
            "answers": {
                interrupt_id: {
                    "value": answer.value,
                    "source": answer.source,
                    "user": answer.user,
                }
                for interrupt_id, answer in state.answers.items()
            },
        },
    }


def parse_collection_state(message: object) -> CollectionState | None:
    """
    Read the collection state back out of a button message.

    An answer that did not survive the round trip intact is dropped rather
    than repaired, which leaves its question pending: pressing again is
    the recovery, and resuming the agent with an answer nobody gave is not.

    Args:
        message (object): The message object (from the block_actions
            payload, or fetched with include_all_metadata).

    Returns:
        CollectionState | None: The validated state, or None when the
            message carries no intact welt_interrupt metadata.
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
    pending_ids = tuple(
        interrupt_id
        for interrupt_id in pending
        if isinstance(interrupt_id, str) and interrupt_id
    )
    if len(pending_ids) != len(pending):
        return None
    if not isinstance(answers, dict):
        return None
    return CollectionState(pending=pending_ids, answers=_parsed_answers(answers))


def _parsed_answers(answers: dict) -> dict[str, Answer]:
    """
    Validate the recorded answers of a state read back from metadata.

    Args:
        answers (dict): The state's `answers` value, straight from the
            message metadata.

    Returns:
        dict[str, Answer]: The entries carrying both a value and an
            answerer; anything else is left out.
    """
    parsed: dict[str, Answer] = {}
    for interrupt_id, answer in answers.items():
        if not isinstance(interrupt_id, str) or not isinstance(answer, dict):
            continue
        # The value is read by presence — any JSON value is an answer,
        # null included — while the answerer must be a name and the source
        # one of the widgets an answer can come from.
        source = answer.get("source")
        user = answer.get("user")
        if "value" not in answer or source not in ANSWER_SOURCES:
            continue
        if not isinstance(user, str):
            continue
        parsed[interrupt_id] = Answer(value=answer["value"], source=source, user=user)
    return parsed


def record_answer(
    state: CollectionState,
    *,
    interrupt_id: str,
    value: object,
    source: str,
    user_id: str,
) -> CollectionState | None:
    """
    Record one button press into a collection state.

    A repeated press for the same interrupt overwrites the earlier answer;
    near-simultaneous presses can still lose one update, which the presser
    recovers by pressing again (accepted, documented).

    Args:
        state (CollectionState): The current collection state.
        interrupt_id (str): The interrupt the pressed button belongs to.
        value (object): The answer the press submitted — the option's
            declared value, or the typed text.
        source (str): The widget the answer came from, one of
            `ANSWER_SOURCES`.
        user_id (str): The Slack user id of the presser, for the audit
            trail in the metadata.

    Returns:
        CollectionState | None: The new state, or None when the interrupt
            id is not one this message is collecting.
    """
    if interrupt_id not in state.pending:
        return None
    answers = dict(state.answers)
    answers[interrupt_id] = Answer(value=value, source=source, user=user_id)
    return CollectionState(pending=state.pending, answers=answers)


def is_fully_answered(state: CollectionState) -> bool:
    """
    Check whether every pending interrupt has an answer.

    Args:
        state (CollectionState): The collection state.

    Returns:
        bool: True when the collected answers cover all pending ids.
    """
    return all(interrupt_id in state.answers for interrupt_id in state.pending)


def build_interrupt_responses(state: CollectionState) -> dict:
    """
    Build the resume payload's `interrupt_responses` from a full state.

    A mapping of interrupt id to the answer and the widget that produced
    it — Welt's own vocabulary, deliberately framework-neutral; turning it
    into a framework's resume input is the agent-side adapter's job.

    The widget travels because only the listener that received the answer
    can tell a press from typed text, and an adapter that has to guess
    guesses from the value: a human who types what an option declared is
    otherwise indistinguishable from one who pressed it.

    Args:
        state (CollectionState): The collection state, which
            `is_fully_answered` must have accepted: the wire carries a
            resume only once every question has an answer.

    Returns:
        dict: The answer per interrupt id, in pending order, each carrying
            its `value` and its `source`.

    Raises:
        KeyError: If a pending interrupt has no answer yet.
    """
    return {
        interrupt_id: {
            "value": state.answers[interrupt_id].value,
            "source": state.answers[interrupt_id].source,
        }
        for interrupt_id in state.pending
    }


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
    blocks: object, *, action_id: str, presser_name: str, answer: object
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
        answer (object): The decoded answer, echoed for a text field.

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


def _receipt_label(block: object, action_id: str, answer: object) -> str | None:
    """
    Derive the receipt text if this block holds the answered widget.

    A button is receipted by its own label, so what it submitted never has
    to be rendered. A text field is receipted by the text that was typed,
    which is text already — the JSON rendering is the receipt's way of
    saying it can show any answer, not a claim that a field could submit
    something else.

    Args:
        block (object): One block of the message.
        action_id (str): The action_id of the answered widget.
        answer (object): The decoded answer.

    Returns:
        str | None: The pressed button's label, the submitted text for a
            text field, or None when this block does not hold the widget.
    """
    if not isinstance(block, dict):
        return None
    if block.get("type") == "input":
        element = block.get("element")
        if isinstance(element, dict) and element.get("action_id") == action_id:
            if isinstance(answer, str):
                return answer
            return json.dumps(answer, ensure_ascii=False)
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
