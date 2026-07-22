from __future__ import annotations

import json

from app.interrupt_logic import (
    DEFAULT_OPTIONS,
    METADATA_EVENT_TYPE,
    InterruptInput,
    InterruptOption,
    InterruptPrompt,
    append_context_notice,
    build_collection_metadata,
    build_interrupt_blocks,
    build_interrupt_responses,
    derive_interrupt_prompt,
    initial_collection_state,
    is_fully_answered,
    parse_action_answer,
    parse_button_value,
    parse_collection_state,
    pick_display_name,
    record_answer,
    replace_answered_blocks,
)
from app.stream_logic import Interrupt

# --- derive_interrupt_prompt: the structured shape ---------------------------


def test_structured_reason_renders_message_and_options():
    reason = {
        "message": "Deploy to prod?",
        "options": [
            {"value": "approve", "label": "Deploy", "style": "primary"},
            {"value": "reject", "label": "Cancel"},
        ],
    }

    assert derive_interrupt_prompt(reason) == InterruptPrompt(
        text="Deploy to prod?",
        options=(
            InterruptOption(value="approve", label="Deploy", style="primary"),
            InterruptOption(value="reject", label="Cancel"),
        ),
    )


def test_structured_option_label_defaults_to_its_value():
    reason = {"message": "Sure?", "options": [{"value": "y"}]}

    assert derive_interrupt_prompt(reason).options == (
        InterruptOption(value="y", label="y", style=None),
    )


def test_structured_danger_style_is_kept():
    reason = {"message": "Sure?", "options": [{"value": "n", "style": "danger"}]}

    assert derive_interrupt_prompt(reason).options[0].style == "danger"


def test_structured_message_is_kept_verbatim_and_clipped():
    reason = {"message": "a <b> & c" + "x" * 12000, "options": [{"value": "y"}]}

    text = derive_interrupt_prompt(reason).text

    assert text.startswith("a <b> & c")
    assert len(text) == 12000
    assert text.endswith("…")


# --- derive_interrupt_prompt: fallbacks --------------------------------------


def test_string_reason_gets_default_buttons():
    prompt = derive_interrupt_prompt(
        'Tool "use_aws" requires human approval. Input: {"region": "us-east-1"}'
    )

    assert prompt.text == (
        'Tool "use_aws" requires human approval. Input: {"region": "us-east-1"}'
    )
    assert prompt.options == DEFAULT_OPTIONS
    assert prompt.input is None


def test_string_reason_is_kept_verbatim_and_clipped():
    prompt = derive_interrupt_prompt("<ok> & " + "x" * 12000)

    assert prompt.text.startswith("<ok> & ")
    assert len(prompt.text) == 12000
    assert prompt.text.endswith("…")


def test_single_key_dict_reason_falls_back_to_a_code_block():
    prompt = derive_interrupt_prompt({"reason": "Authorize get_patient_vitals"})

    assert prompt.text == ('```\n{\n  "reason": "Authorize get_patient_vitals"\n}\n```')
    assert prompt.options == DEFAULT_OPTIONS


def test_raw_data_dict_reason_falls_back_to_a_code_block():
    prompt = derive_interrupt_prompt({"paths": ["/etc/passwd", "/etc/shadow"]})

    assert prompt.text.startswith("```\n{\n")
    assert '"/etc/passwd"' in prompt.text
    assert prompt.options == DEFAULT_OPTIONS


def test_none_reason_falls_back_to_a_code_block():
    assert derive_interrupt_prompt(None).text == "```\nnull\n```"


def test_empty_string_reason_falls_back_to_a_code_block():
    assert derive_interrupt_prompt("").text == '```\n""\n```'


def test_code_block_fallback_is_kept_verbatim_and_clipped_inside_the_fence():
    prompt = derive_interrupt_prompt({"cmd": "<rm> & " + "y" * 13000})

    assert "<rm> &" in prompt.text
    assert len(prompt.text) == 12000
    assert prompt.text.endswith("…\n```")


def test_almost_structured_reasons_fall_back():
    base_option = {"value": "y", "label": "Yes"}
    almost = [
        {"message": "Sure?", "options": [base_option], "extra": True},
        {"message": "Sure?"},
        {"options": [base_option]},
        {"message": 42, "options": [base_option]},
        {"message": "", "options": [base_option]},
        {"message": "Sure?", "options": "not a list"},
        {"message": "Sure?", "options": []},
        {"message": "Sure?", "options": [base_option] * 26},
        {"message": "Sure?", "options": ["not a dict"]},
        {"message": "Sure?", "options": [{"value": "y", "emoji": True}]},
        {"message": "Sure?", "options": [{"label": "Yes"}]},
        {"message": "Sure?", "options": [{"value": 42}]},
        {"message": "Sure?", "options": [{"value": ""}]},
        {"message": "Sure?", "options": [{"value": "v" * 1801}]},
        {"message": "Sure?", "options": [{"value": "y", "label": 42}]},
        {"message": "Sure?", "options": [{"value": "y", "label": ""}]},
        {"message": "Sure?", "options": [{"value": "y", "style": "default"}]},
        {"message": "Sure?", "options": [{"value": "y", "style": 42}]},
    ]

    for reason in almost:
        assert derive_interrupt_prompt(reason).options == DEFAULT_OPTIONS, reason


def test_value_at_the_length_cap_still_matches_the_structured_shape():
    reason = {"message": "Sure?", "options": [{"value": "v" * 1800}]}

    assert derive_interrupt_prompt(reason).options != DEFAULT_OPTIONS


def test_input_reason_renders_a_text_field():
    reason = {"message": "Which city?", "input": {"label": "City"}}

    assert derive_interrupt_prompt(reason) == InterruptPrompt(
        text="Which city?",
        input=InterruptInput(label="City", multiline=False),
    )


def test_input_reason_defaults_and_multiline():
    prompt = derive_interrupt_prompt({"message": "Notes?", "input": {}})
    assert prompt.input == InterruptInput(label="Answer", multiline=False)

    multiline = derive_interrupt_prompt(
        {"message": "Notes?", "input": {"multiline": True}}
    )
    assert multiline.input == InterruptInput(label="Answer", multiline=True)


def test_mixed_reason_carries_both_buttons_and_field():
    reason = {
        "message": "Which city?",
        "options": [{"value": "tokyo", "label": "Tokyo"}],
        "input": {"label": "City"},
    }

    prompt = derive_interrupt_prompt(reason)

    assert prompt.options == (InterruptOption(value="tokyo", label="Tokyo"),)
    assert prompt.input == InterruptInput(label="City", multiline=False)


def test_almost_input_reasons_fall_back():
    almost = [
        {"message": "Q?", "input": "not a dict"},
        {"message": "Q?", "input": {"label": ""}},
        {"message": "Q?", "input": {"label": 42}},
        {"message": "Q?", "input": {"multiline": "yes"}},
        {"message": "Q?", "input": {"placeholder": "hm"}},
        {"message": "Q?", "input": {}, "options": "not a list"},
        {"message": "Q?", "input": {}, "extra": True},
    ]

    for reason in almost:
        prompt = derive_interrupt_prompt(reason)
        assert prompt.input is None, reason
        assert prompt.options == DEFAULT_OPTIONS, reason


# --- build_interrupt_blocks ---------------------------------------------------


def test_blocks_carry_one_section_and_one_actions_row_per_interrupt():
    interrupts = [
        Interrupt(id="i-1", name="a", reason="First?"),
        Interrupt(
            id="i-2",
            name="b",
            reason={
                "message": "Second?",
                "options": [{"value": "go", "style": "primary"}],
            },
        ),
    ]

    blocks = build_interrupt_blocks(interrupts)

    # The first question is a fallback rendering, so it gets the default
    # widgets: the Approve / Deny buttons, and no free-text field.
    assert [block["type"] for block in blocks] == [
        "markdown",
        "actions",
        "markdown",
        "actions",
    ]
    assert blocks[0] == {"type": "markdown", "text": "First?"}
    assert blocks[2] == {"type": "markdown", "text": "Second?"}

    first_row = blocks[1]["elements"]
    assert [element["action_id"] for element in first_row] == [
        "welt_interrupt_0_0",
        "welt_interrupt_0_1",
    ]
    assert json.loads(first_row[0]["value"]) == {"iid": "i-1", "v": "y"}
    assert first_row[0]["style"] == "primary"
    assert "style" not in first_row[1]
    assert blocks[1]["block_id"] == "welt_interrupt_q_0_options"

    second_row = blocks[3]["elements"]
    assert second_row[0]["action_id"] == "welt_interrupt_1_0"
    assert json.loads(second_row[0]["value"]) == {"iid": "i-2", "v": "go"}


def test_block_button_labels_are_clipped():
    reason = {"message": "Sure?", "options": [{"value": "y", "label": "L" * 80}]}
    blocks = build_interrupt_blocks([Interrupt(id="i-1", name="a", reason=reason)])

    label = blocks[1]["elements"][0]["text"]["text"]

    assert len(label) == 75
    assert label.endswith("…")


def test_input_reason_becomes_an_input_block():
    reason = {"message": "Which city?", "input": {"label": "City"}}
    blocks = build_interrupt_blocks([Interrupt(id="i-1", name="q", reason=reason)])

    assert blocks[0] == {"type": "markdown", "text": "Which city?"}
    assert blocks[1] == {
        "type": "input",
        "block_id": "welt_interrupt_q_0_input",
        "dispatch_action": True,
        "label": {"type": "plain_text", "text": "City"},
        "element": {
            "type": "plain_text_input",
            "action_id": "welt_interrupt_input_i-1",
            "multiline": False,
            "dispatch_action_config": {"trigger_actions_on": ["on_enter_pressed"]},
        },
    }


def test_bodies_split_the_cumulative_markdown_budget():
    # Slack's 12,000-character markdown cap is cumulative across a message,
    # so two questions get 6,000 each.
    interrupts = [
        Interrupt(id="i-1", name="a", reason="a" * 12000),
        Interrupt(id="i-2", name="b", reason="b" * 12000),
    ]

    blocks = build_interrupt_blocks(interrupts)

    bodies = [block["text"] for block in blocks if block["type"] == "markdown"]
    assert [len(body) for body in bodies] == [6000, 6000]
    assert all(body.endswith("…") for body in bodies)


# --- collection state ----------------------------------------------------------


def test_initial_state_and_metadata_wrap():
    interrupts = [
        Interrupt(id="i-1", name="a", reason=None),
        Interrupt(id="i-2", name="b", reason=None),
    ]

    state = initial_collection_state(interrupts)

    assert state == {"pending": ["i-1", "i-2"], "answers": {}}
    assert build_collection_metadata(state) == {
        "event_type": METADATA_EVENT_TYPE,
        "event_payload": state,
    }


def test_collection_state_round_trips_through_a_message():
    metadata = build_collection_metadata({"pending": ["i-1"], "answers": {}})
    message = {"ts": "1.0", "metadata": metadata}

    assert parse_collection_state(message) == {"pending": ["i-1"], "answers": {}}


def test_collection_state_rejects_foreign_or_broken_messages():
    intact = {"pending": ["i-1"], "answers": {}}
    broken = [
        "not a dict",
        {},
        {"metadata": "not a dict"},
        {"metadata": {"event_type": "other", "event_payload": intact}},
        {"metadata": {"event_type": METADATA_EVENT_TYPE, "event_payload": "x"}},
        {
            "metadata": {
                "event_type": METADATA_EVENT_TYPE,
                "event_payload": {"answers": {}},
            }
        },
        {
            "metadata": {
                "event_type": METADATA_EVENT_TYPE,
                "event_payload": {"pending": [], "answers": {}},
            }
        },
        {
            "metadata": {
                "event_type": METADATA_EVENT_TYPE,
                "event_payload": {"pending": "i-1", "answers": {}},
            }
        },
        {
            "metadata": {
                "event_type": METADATA_EVENT_TYPE,
                "event_payload": {"pending": [1], "answers": {}},
            }
        },
        {
            "metadata": {
                "event_type": METADATA_EVENT_TYPE,
                "event_payload": {"pending": [""], "answers": {}},
            }
        },
        {
            "metadata": {
                "event_type": METADATA_EVENT_TYPE,
                "event_payload": {"pending": ["i-1"], "answers": "x"},
            }
        },
    ]

    for message in broken:
        assert parse_collection_state(message) is None, message


def test_record_answer_fills_the_state():
    state = {"pending": ["i-1", "i-2"], "answers": {}}

    updated = record_answer(state, interrupt_id="i-1", value="y", user_id="U1")

    assert updated == {
        "pending": ["i-1", "i-2"],
        "answers": {"i-1": {"value": "y", "user": "U1"}},
    }
    assert state["answers"] == {}  # the input state is not mutated


def test_record_answer_overwrites_an_earlier_answer():
    state = {"pending": ["i-1"], "answers": {"i-1": {"value": "y", "user": "U1"}}}

    updated = record_answer(state, interrupt_id="i-1", value="n", user_id="U2")

    assert updated is not None
    assert updated["answers"]["i-1"] == {"value": "n", "user": "U2"}


def test_record_answer_rejects_an_unknown_interrupt():
    state = {"pending": ["i-1"], "answers": {}}

    assert record_answer(state, interrupt_id="i-9", value="y", user_id="U1") is None


def test_is_fully_answered():
    partial = {"pending": ["i-1", "i-2"], "answers": {"i-1": {"value": "y"}}}
    full = {
        "pending": ["i-1", "i-2"],
        "answers": {"i-1": {"value": "y"}, "i-2": {"value": "n"}},
    }

    assert is_fully_answered(partial) is False
    assert is_fully_answered(full) is True


def test_interrupt_responses_follow_pending_order():
    state = {
        "pending": ["i-1", "i-2"],
        "answers": {
            "i-2": {"value": "n", "user": "U2"},
            "i-1": {"value": "y", "user": "U1"},
        },
    }

    responses = build_interrupt_responses(state)

    assert responses == {"i-1": "y", "i-2": "n"}
    assert list(responses) == ["i-1", "i-2"]


def test_interrupt_responses_tolerate_a_mangled_answer():
    state = {"pending": ["i-1", "i-2"], "answers": {"i-1": "y", "i-2": {"value": 1}}}

    assert build_interrupt_responses(state) == {"i-1": "", "i-2": ""}


# --- parse_button_value ---------------------------------------------------------


def test_button_value_round_trips():
    value = json.dumps({"iid": "i-1", "v": "approve"})

    assert parse_button_value(value) == ("i-1", "approve")


def test_malformed_button_values_are_rejected():
    malformed = [
        None,
        42,
        "not json",
        '"a string"',
        json.dumps({"v": "y"}),
        json.dumps({"iid": "", "v": "y"}),
        json.dumps({"iid": 1, "v": "y"}),
        json.dumps({"iid": "i-1"}),
        json.dumps({"iid": "i-1", "v": 1}),
    ]

    for value in malformed:
        assert parse_button_value(value) is None, value


def test_button_action_decodes_through_its_value():
    action = {
        "type": "button",
        "action_id": "welt_interrupt_0_0",
        "value": json.dumps({"iid": "i-1", "v": "approve"}),
    }

    assert parse_action_answer(action) == ("i-1", "approve")


def test_text_input_action_decodes_id_from_its_action_id():
    action = {
        "type": "plain_text_input",
        "action_id": "welt_interrupt_input_i-1",
        "value": "Tokyo",
    }

    assert parse_action_answer(action) == ("i-1", "Tokyo")


def test_malformed_actions_are_rejected():
    malformed = [
        "not a dict",
        {"type": "plain_text_input", "action_id": "other_input", "value": "x"},
        {
            "type": "plain_text_input",
            "action_id": "welt_interrupt_input_",
            "value": "x",
        },
        {"type": "plain_text_input", "action_id": "welt_interrupt_input_i-1"},
        {
            "type": "plain_text_input",
            "action_id": "welt_interrupt_input_i-1",
            "value": "",
        },
        {"type": "plain_text_input", "action_id": 42, "value": "x"},
        {"type": "button", "action_id": "welt_interrupt_0_0", "value": "not json"},
    ]

    for action in malformed:
        assert parse_action_answer(action) is None, action


# --- replace_answered_blocks ----------------------------------------------------


def _button(action_id: str, label: str) -> dict:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "value": "{}",
    }


def test_pressed_row_becomes_a_receipt_with_the_presser():
    blocks = [
        {"type": "markdown", "text": "First?"},
        {
            "type": "actions",
            "elements": [_button("welt_interrupt_0_0", "Deploy <now>")],
        },
        {"type": "markdown", "text": "Second?"},
        {"type": "actions", "elements": [_button("welt_interrupt_1_0", "Go")]},
    ]

    updated = replace_answered_blocks(
        blocks,
        action_id="welt_interrupt_0_0",
        presser_name="Takashi Iwamoto",
        answer="approve",
    )

    assert updated is not None
    assert updated[0] == blocks[0]
    assert updated[1] == {
        "type": "context",
        "elements": [
            {
                "type": "plain_text",
                "text": "“Deploy <now>” — answered by Takashi Iwamoto",
            }
        ],
    }
    assert updated[2] == blocks[2]
    assert updated[3] == blocks[3]


def test_replacement_without_the_pressed_button_returns_none():
    blocks = [
        {"type": "actions", "elements": [_button("welt_interrupt_0_0", "Go")]},
    ]

    assert (
        replace_answered_blocks(blocks, action_id="other", presser_name="x", answer="y")
        is None
    )
    assert (
        replace_answered_blocks(
            "not a list", action_id="a", presser_name="x", answer="y"
        )
        is None
    )


def test_answered_text_field_receipt_echoes_the_submitted_text():
    blocks = [
        {"type": "markdown", "text": "Which city?"},
        {
            "type": "input",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "City"},
            "element": {
                "type": "plain_text_input",
                "action_id": "welt_interrupt_input_i-1",
            },
        },
    ]

    updated = replace_answered_blocks(
        blocks,
        action_id="welt_interrupt_input_i-1",
        presser_name="Takashi",
        answer="Tokyo & <Yokohama>",
    )

    assert updated is not None
    assert updated[1] == {
        "type": "context",
        "elements": [
            {
                "type": "plain_text",
                "text": "“Tokyo & <Yokohama>” — answered by Takashi",
            }
        ],
    }


def test_input_block_with_a_different_action_id_is_kept():
    blocks = [
        {
            "type": "input",
            "element": {"type": "plain_text_input", "action_id": "other"},
        },
    ]

    assert (
        replace_answered_blocks(
            blocks, action_id="welt_interrupt_input_i-1", presser_name="x", answer="y"
        )
        is None
    )


def test_replacement_skips_unreadable_blocks_and_elements():
    blocks = [
        "not a dict",
        {"type": "actions", "elements": "not a list"},
        {"type": "actions", "elements": ["not a dict"]},
        {
            "type": "actions",
            "elements": [
                {"type": "button", "action_id": "welt_interrupt_0_0"},
            ],
        },
    ]

    updated = replace_answered_blocks(
        blocks, action_id="welt_interrupt_0_0", presser_name="x", answer="y"
    )

    assert updated is not None
    assert updated[:3] == blocks[:3]
    # The pressed button had no readable label, so the receipt gets a stand-in.
    assert updated[3]["elements"][0]["text"] == "“Selected” — answered by x"


def test_context_notice_is_appended_after_the_existing_blocks():
    blocks = [
        {"type": "markdown", "text": "Deploy?"},
        {
            "type": "context",
            "elements": [{"type": "plain_text", "text": "“Go” — answered by T"}],
        },
    ]

    updated = append_context_notice(blocks, ":warning: Could not resume.")

    assert updated[:2] == blocks
    assert updated[2] == {
        "type": "context",
        "elements": [{"type": "plain_text", "text": ":warning: Could not resume."}],
    }


def test_mixed_question_widgets_share_a_group_and_retire_together():
    reason = {
        "message": "Which city?",
        "options": [{"value": "tokyo", "label": "Tokyo"}],
        "input": {"label": "City"},
    }
    blocks = build_interrupt_blocks([Interrupt(id="i-1", name="q", reason=reason)])

    assert [block["type"] for block in blocks] == ["markdown", "actions", "input"]
    assert blocks[1]["block_id"] == "welt_interrupt_q_0_options"
    assert blocks[2]["block_id"] == "welt_interrupt_q_0_input"

    # Answered via button: the text field retires with the button row.
    by_button = replace_answered_blocks(
        blocks,
        action_id="welt_interrupt_0_0",
        presser_name="Takashi",
        answer="tokyo",
    )
    assert by_button is not None
    assert [block["type"] for block in by_button] == ["markdown", "context"]
    assert by_button[1]["elements"][0]["text"] == "“Tokyo” — answered by Takashi"

    # Answered via the field: the button row retires with it.
    by_text = replace_answered_blocks(
        blocks,
        action_id="welt_interrupt_input_i-1",
        presser_name="Takashi",
        answer="Osaka",
    )
    assert by_text is not None
    assert [block["type"] for block in by_text] == ["markdown", "context"]
    assert by_text[1]["elements"][0]["text"] == "“Osaka” — answered by Takashi"


def test_answering_one_question_keeps_the_other_questions_widgets():
    interrupts = [
        Interrupt(id="i-1", name="a", reason="First?"),
        Interrupt(id="i-2", name="b", reason="Second?"),
    ]
    blocks = build_interrupt_blocks(interrupts)
    assert [block["type"] for block in blocks] == ["markdown", "actions"] * 2

    updated = replace_answered_blocks(
        blocks,
        action_id="welt_interrupt_0_0",
        presser_name="Takashi",
        answer="y",
    )

    assert updated is not None
    assert [block["type"] for block in updated] == [
        "markdown",
        "context",
        "markdown",
        "actions",
    ]
    assert updated[2:] == blocks[2:]


def test_a_widget_id_without_a_suffix_retires_only_the_pressed_block():
    # A bare group id carries no widget suffix, so it groups with nothing;
    # the pressed block retires alone.
    blocks = [
        {
            "type": "actions",
            "block_id": "welt_interrupt_q_0",
            "elements": [_button("welt_interrupt_0_0", "Go")],
        },
        {
            "type": "actions",
            "block_id": "welt_interrupt_q_1",
            "elements": [_button("welt_interrupt_1_0", "Later")],
        },
    ]

    updated = replace_answered_blocks(
        blocks,
        action_id="welt_interrupt_0_0",
        presser_name="Takashi",
        answer="y",
    )

    assert updated is not None
    assert [block["type"] for block in updated] == ["context", "actions"]
    assert updated[1] == blocks[1]


# --- pick_display_name -----------------------------------------------------------


def test_display_name_prefers_the_profile():
    user = {
        "name": "iwamot",
        "real_name": "Takashi Iwamoto",
        "profile": {"display_name": "iwamot-display", "real_name": "Takashi Iwamoto"},
    }

    assert pick_display_name(user) == "iwamot-display"


def test_display_name_falls_back_through_real_names():
    assert (
        pick_display_name({"profile": {"display_name": "", "real_name": "Takashi"}})
        == "Takashi"
    )
    assert (
        pick_display_name({"real_name": "Takashi", "profile": {"display_name": ""}})
        == "Takashi"
    )
    assert pick_display_name({"name": "iwamot", "profile": "x"}) == "iwamot"


def test_display_name_of_an_unreadable_user_is_none():
    assert pick_display_name("not a dict") is None
    assert pick_display_name({"name": "", "real_name": 42}) is None
