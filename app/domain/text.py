"""Validate the single plain-text intake format and split text into located passages."""

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

MAX_BYTES = 65_536
MAX_CHARACTERS = 20_000
MAX_FRAGMENT_CHARACTERS = 800
_BOM = b"\xef\xbb\xbf"
# A paragraph is a maximal run of lines that contain non-whitespace characters.
_PARAGRAPH = re.compile(r"(?:^[^\n]*\S[^\n]*(?:\n|\Z))+", re.MULTILINE)


class TextRejection(StrEnum):
    """Name each predictable intake failure without echoing the rejected text."""

    TOO_LARGE = "too_large"
    NOT_UTF8 = "not_utf8"
    CONTROL_CHARACTERS = "control_characters"
    EMPTY = "empty"


class TextRejected(ValueError):
    """Carry a rejection code; the message never contains document content."""

    def __init__(self, reason: TextRejection) -> None:
        """Keep the machine-readable reason alongside the exception text."""
        super().__init__(reason.value)
        self.reason = reason


def decode_text(raw: bytes) -> str:
    """Decode strict UTF-8, normalize line endings and enforce size and characters."""
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_BYTES:
        raise TextRejected(TextRejection.TOO_LARGE)
    if raw.startswith(_BOM):
        raw = raw[len(_BOM) :]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise TextRejected(TextRejection.NOT_UTF8) from None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(
        unicodedata.category(character) == "Cc" and character not in "\n\t"
        for character in text
    ):
        raise TextRejected(TextRejection.CONTROL_CHARACTERS)
    if not text.strip():
        raise TextRejected(TextRejection.EMPTY)
    if len(text) > MAX_CHARACTERS:
        raise TextRejected(TextRejection.TOO_LARGE)
    return text


@dataclass(frozen=True, slots=True)
class Fragment:
    """Locate one passage by 1-based ordinal and code-point offsets in its version."""

    ordinal: int
    start: int
    end: int
    text: str = field(repr=False)


def _trimmed(content: str, start: int, end: int) -> tuple[int, int]:
    """Move span edges inward past surrounding whitespace."""
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return start, end


def _bounded_spans(content: str, start: int, end: int) -> list[tuple[int, int]]:
    """Cut one paragraph into spans of at most the fragment limit, at whitespace."""
    spans: list[tuple[int, int]] = []
    while end - start > MAX_FRAGMENT_CHARACTERS:
        limit = start + MAX_FRAGMENT_CHARACTERS
        cut = max(content.rfind(" ", start, limit), content.rfind("\n", start, limit))
        # Without usable whitespace in the second half, cut at the hard limit.
        if cut <= start + MAX_FRAGMENT_CHARACTERS // 2:
            cut = limit
        piece = _trimmed(content, start, cut)
        if piece[0] < piece[1]:
            spans.append(piece)
        start, end = _trimmed(content, cut, end)
    if start < end:
        spans.append((start, end))
    return spans


def split_fragments(content: str) -> tuple[Fragment, ...]:
    """Split at blank lines and bound long paragraphs; offsets reproduce each text."""
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    spans: list[tuple[int, int]] = []
    for match in _PARAGRAPH.finditer(content):
        start, end = _trimmed(content, match.start(), match.end())
        spans.extend(_bounded_spans(content, start, end))
    return tuple(
        Fragment(ordinal=index, start=start, end=end, text=content[start:end])
        for index, (start, end) in enumerate(spans, start=1)
    )
