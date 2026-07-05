from __future__ import annotations

import pytest

from app.slack_file_logic import (
    MAX_BYTES_BY_MODALITY,
    MAX_SLOTS_BY_MODALITY,
    FileToFetch,
    expected_content_types,
    parse_file_input_modalities,
    select_files_to_fetch,
)

ALL_MODALITIES = ("image", "document", "video")


def _select(replies: list[dict], **overrides: object) -> list[FileToFetch]:
    kwargs: dict = {
        "bot_user_id": "U_BOT",
        "allowed_modalities": ALL_MODALITIES,
        "max_slots_by_modality": MAX_SLOTS_BY_MODALITY,
        "max_bytes_by_modality": MAX_BYTES_BY_MODALITY,
    }
    kwargs.update(overrides)
    return select_files_to_fetch(replies, **kwargs)


def _file(
    file_id: str, mimetype: str, name: str | None = None, size: int = 1024
) -> dict:
    return {
        "id": file_id,
        "mimetype": mimetype,
        "url_private": f"https://files.slack.com/{file_id}",
        "name": name if name is not None else f"{file_id}.bin",
        "size": size,
    }


# --- parse_file_input_modalities ----------------------------------------------


def test_parse_file_input_modalities_splits_and_normalizes():
    assert parse_file_input_modalities("image, DOCUMENT ,video,,image") == (
        "image",
        "document",
        "video",
    )


def test_parse_file_input_modalities_empty_means_disabled():
    assert parse_file_input_modalities("") == ()
    assert parse_file_input_modalities(" , ") == ()


def test_parse_file_input_modalities_rejects_unknown_modality():
    with pytest.raises(ValueError, match="unsupported modality 'png'"):
        parse_file_input_modalities("image,png")


# --- expected_content_types --------------------------------------------------


def test_expected_content_types_are_the_mapped_mime_types():
    assert expected_content_types("png") == ["image/png"]
    assert expected_content_types("mp4") == ["video/mp4"]


def test_expected_content_types_for_pdf_allow_generic_binary():
    assert expected_content_types("pdf") == ["application/pdf", "binary/octet-stream"]


# --- select_files_to_fetch ---------------------------------------------------


def test_image_is_selected_with_format_from_mimetype():
    replies = [{"user": "U1", "files": [_file("F1", "image/jpeg", "F1.jpg")]}]

    result = _select(replies)

    assert result == [
        FileToFetch(
            file_id="F1",
            url="https://files.slack.com/F1",
            modality="image",
            format="jpeg",
            name="F1.jpg",
        )
    ]


def test_document_formats_are_selected():
    replies = [
        {
            "user": "U1",
            "files": [
                _file("F1", "application/pdf"),
                _file(
                    "F2",
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document",
                ),
                _file("F3", "text/plain"),
            ],
        }
    ]

    result = _select(replies)

    assert [(s.file_id, s.modality, s.format) for s in result] == [
        ("F1", "document", "pdf"),
        ("F2", "document", "docx"),
        ("F3", "document", "txt"),
    ]


def test_video_is_selected():
    replies = [{"user": "U1", "files": [_file("F1", "video/mp4")]}]

    result = _select(replies)

    assert [(s.modality, s.format) for s in result] == [("video", "mp4")]


def test_shared_mpeg_mime_type_resolves_to_the_first_listed_format():
    replies = [{"user": "U1", "files": [_file("F1", "video/mpeg")]}]

    assert _select(replies)[0].format == "mpeg"


def test_no_allowed_modalities_select_nothing():
    replies = [
        {
            "user": "U1",
            "files": [_file("F1", "image/png"), _file("F2", "application/pdf")],
        }
    ]

    assert _select(replies, allowed_modalities=()) == []


def test_file_with_disallowed_modality_is_skipped():
    replies = [
        {
            "user": "U1",
            "files": [_file("F1", "image/png"), _file("F2", "application/pdf")],
        }
    ]

    result = _select(replies, allowed_modalities=("document",))

    assert [s.file_id for s in result] == ["F2"]


def test_unsupported_mimetype_is_skipped():
    replies = [{"user": "U1", "files": [_file("F1", "image/bmp")]}]

    assert _select(replies) == []


def test_file_missing_id_or_url_is_skipped():
    replies = [
        {"user": "U1", "files": [{"mimetype": "image/png", "url_private": "u"}]},
        {"user": "U1", "files": [{"id": "F1", "mimetype": "image/png"}]},
    ]

    assert _select(replies) == []


def test_missing_name_becomes_none():
    replies = [
        {
            "user": "U1",
            "files": [
                {
                    "id": "F1",
                    "mimetype": "image/png",
                    "url_private": "u",
                    "name": 1,
                    "size": 1024,
                }
            ],
        }
    ]

    assert _select(replies)[0].name is None


def test_bot_posts_are_excluded():
    replies = [
        {"user": "U_BOT", "files": [_file("F1", "image/png")]},
        {"user": "U1", "bot_id": "B1", "files": [_file("F2", "image/png")]},
    ]

    assert _select(replies) == []


def test_slots_prefer_recent_replies():
    replies = [
        {"user": "U1", "files": [_file("F_old", "application/pdf")]},
        {"user": "U1", "files": [_file("F_mid", "application/pdf")]},
        {"user": "U1", "files": [_file("F_new", "application/pdf")]},
    ]

    result = _select(replies, max_slots_by_modality={"document": 2})

    assert [selection.file_id for selection in result] == ["F_new", "F_mid"]


def test_slots_are_tracked_per_modality():
    replies = [
        {
            "user": "U1",
            "files": [_file("F1", "application/pdf"), _file("F2", "image/png")],
        },
        {
            "user": "U1",
            "files": [_file("F3", "application/pdf"), _file("F4", "image/png")],
        },
    ]

    result = _select(replies, max_slots_by_modality={"document": 1, "image": 20})

    modalities = {selection.file_id: selection.modality for selection in result}
    assert modalities == {"F3": "document", "F2": "image", "F4": "image"}


def test_video_slot_limit_is_one():
    replies = [
        {"user": "U1", "files": [_file("F_old", "video/mp4")]},
        {"user": "U1", "files": [_file("F_new", "video/webm")]},
    ]

    result = _select(replies)

    assert [selection.file_id for selection in result] == ["F_new"]


def test_file_at_the_size_limit_is_selected():
    limit = MAX_BYTES_BY_MODALITY["image"]
    replies = [{"user": "U1", "files": [_file("F1", "image/png", size=limit)]}]

    assert [s.file_id for s in _select(replies)] == ["F1"]


def test_oversized_file_is_skipped():
    replies = [
        {
            "user": "U1",
            "files": [
                _file("F1", "image/png", size=MAX_BYTES_BY_MODALITY["image"] + 1),
                _file(
                    "F2",
                    "application/pdf",
                    size=MAX_BYTES_BY_MODALITY["document"] + 1,
                ),
                _file("F3", "video/mp4", size=MAX_BYTES_BY_MODALITY["video"] + 1),
            ],
        }
    ]

    assert _select(replies) == []


def test_oversized_file_does_not_consume_a_slot():
    replies = [
        {"user": "U1", "files": [_file("F_old", "application/pdf")]},
        {
            "user": "U1",
            "files": [
                _file(
                    "F_new",
                    "application/pdf",
                    size=MAX_BYTES_BY_MODALITY["document"] + 1,
                )
            ],
        },
    ]

    result = _select(replies, max_slots_by_modality={"document": 1})

    assert [s.file_id for s in result] == ["F_old"]


def test_file_with_missing_or_invalid_size_is_skipped():
    file_without_size = _file("F1", "image/png")
    del file_without_size["size"]
    file_with_str_size = _file("F2", "image/png")
    file_with_str_size["size"] = "1024"
    replies = [{"user": "U1", "files": [file_without_size, file_with_str_size]}]

    assert _select(replies) == []


def test_replies_without_files_are_skipped():
    assert _select([{"user": "U1", "text": "hi"}, {"user": "U1", "files": "x"}]) == []


def test_non_dict_file_entry_is_skipped():
    assert _select([{"user": "U1", "files": ["x"]}]) == []
