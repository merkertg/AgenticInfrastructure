"""Verify the plain-text intake format and located fragment boundaries."""

import pytest

from app.domain.text import (
    MAX_BYTES,
    MAX_CHARACTERS,
    MAX_FRAGMENT_CHARACTERS,
    TextRejected,
    TextRejection,
    decode_text,
    split_fragments,
)


def test_valid_text_keeps_content_and_normalizes_line_endings() -> None:
    """A byte-order mark is removed and CRLF/CR become LF; nothing else changes."""
    raw = "﻿Заголовок\r\nстрока\tс табуляцией\rконец".encode()
    assert decode_text(raw) == "Заголовок\nстрока\tс табуляцией\nконец"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"\xff\xfe broken", TextRejection.NOT_UTF8),
        (b"\xc3", TextRejection.NOT_UTF8),
        (b"", TextRejection.EMPTY),
        (b" \n\t\n", TextRejection.EMPTY),
        (b"\xef\xbb\xbf", TextRejection.EMPTY),
        (b"text\x00hidden", TextRejection.CONTROL_CHARACTERS),
        (b"bell\x07", TextRejection.CONTROL_CHARACTERS),
        (b"escape\x1b[31m", TextRejection.CONTROL_CHARACTERS),
        (b"a" * (MAX_BYTES + 1), TextRejection.TOO_LARGE),
        ("я".encode() * (MAX_CHARACTERS + 1), TextRejection.TOO_LARGE),
    ],
)
def test_corrupted_or_oversized_input_is_rejected_predictably(
    raw: bytes, reason: TextRejection
) -> None:
    """Each failure has a stable code and a message without the rejected text."""
    with pytest.raises(TextRejected) as rejected:
        decode_text(raw)
    assert rejected.value.reason is reason
    assert str(rejected.value) == reason.value


def test_limits_are_inclusive() -> None:
    """Exactly the maximum number of characters is still accepted."""
    assert len(decode_text(b"a" * MAX_CHARACTERS)) == MAX_CHARACTERS
    with pytest.raises(TypeError):
        decode_text("not bytes")  # type: ignore[arg-type]


def test_fragments_follow_blank_lines_and_reproduce_offsets() -> None:
    """Paragraphs become fragments whose offsets slice the original content."""
    content = "  First line\nsecond line  \n\n \t \nSecond paragraph.\n\n\nThird."
    fragments = split_fragments(content)
    assert [fragment.text for fragment in fragments] == [
        "First line\nsecond line",
        "Second paragraph.",
        "Third.",
    ]
    assert [fragment.ordinal for fragment in fragments] == [1, 2, 3]
    for fragment in fragments:
        assert content[fragment.start : fragment.end] == fragment.text


def test_long_paragraphs_are_cut_at_whitespace_within_the_limit() -> None:
    """A paragraph longer than the limit yields bounded consecutive fragments."""
    content = " ".join(f"word{index:04d}" for index in range(400))
    fragments = split_fragments(content)
    assert len(fragments) > 1
    assert all(len(fragment.text) <= MAX_FRAGMENT_CHARACTERS for fragment in fragments)
    assert all(not fragment.text.startswith(" ") for fragment in fragments)
    rebuilt = " ".join(fragment.text for fragment in fragments)
    assert rebuilt == content
    for fragment in fragments:
        assert content[fragment.start : fragment.end] == fragment.text


def test_text_without_whitespace_is_cut_at_the_hard_limit() -> None:
    """Unbroken text still produces bounded fragments covering every character."""
    content = "x" * (MAX_FRAGMENT_CHARACTERS * 2 + 5)
    fragments = split_fragments(content)
    assert [len(fragment.text) for fragment in fragments] == [
        MAX_FRAGMENT_CHARACTERS,
        MAX_FRAGMENT_CHARACTERS,
        5,
    ]


def test_fragment_text_is_hidden_from_repr() -> None:
    """Debug output of a fragment shows location only."""
    (fragment,) = split_fragments("Private fictional passage.")
    assert "Private" not in repr(fragment)
