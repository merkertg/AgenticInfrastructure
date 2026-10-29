"""Verify the memory tool's argument, question and answer rules without storage."""

import json

import pytest

from app.domain.retrieval import MAX_QUERY_CHARACTERS
from app.domain.tool import (
    DEFAULT_TOOL_ITEMS,
    MAX_ANSWER_CHARACTERS,
    MAX_ARGUMENT_CHARACTERS,
    MAX_QUESTION_CHARACTERS,
    MAX_TOOL_ITEMS,
    SEARCH_TOOL,
    SEARCH_TOOL_SPEC,
    ToolCallRejected,
    ToolRejection,
    answer_text_is_valid,
    check_question,
    parse_search_call,
)


def test_valid_call_keeps_query_and_defaults_item_count() -> None:
    """A call with only a query uses the documented default item count."""
    parsed = parse_search_call(SEARCH_TOOL, '{"query": "travel limit"}')
    assert (parsed.query, parsed.max_items) == ("travel limit", DEFAULT_TOOL_ITEMS)
    explicit = parse_search_call(SEARCH_TOOL, '{"query": "travel", "max_items": 5}')
    assert explicit.max_items == MAX_TOOL_ITEMS


@pytest.mark.parametrize(
    ("name", "arguments", "reason"),
    [
        ("grant_access", '{"query": "travel"}', ToolRejection.UNKNOWN_TOOL),
        ("Search_Memory", '{"query": "travel"}', ToolRejection.UNKNOWN_TOOL),
        (
            SEARCH_TOOL,
            json.dumps({"query": "x" * MAX_ARGUMENT_CHARACTERS}),
            ToolRejection.ARGUMENTS_TOO_LARGE,
        ),
        (SEARCH_TOOL, "{query: travel}", ToolRejection.INVALID_JSON),
        (SEARCH_TOOL, '{"query": "a", "query": "b"}', ToolRejection.INVALID_JSON),
        (SEARCH_TOOL, '{"query": "a", "max_items": NaN}', ToolRejection.INVALID_JSON),
        (SEARCH_TOOL, "[" * 500 + "]" * 500, ToolRejection.INVALID_ARGUMENTS),
        (SEARCH_TOOL, '["travel"]', ToolRejection.INVALID_ARGUMENTS),
        (SEARCH_TOOL, "{}", ToolRejection.INVALID_ARGUMENTS),
        (
            SEARCH_TOOL,
            '{"query": "travel", "project_id": "engineering"}',
            ToolRejection.INVALID_ARGUMENTS,
        ),
        (
            SEARCH_TOOL,
            '{"query": "travel", "employee_id": "eng-user"}',
            ToolRejection.INVALID_ARGUMENTS,
        ),
        (SEARCH_TOOL, '{"query": 5}', ToolRejection.INVALID_ARGUMENTS),
        (SEARCH_TOOL, '{"query": "!!!"}', ToolRejection.INVALID_ARGUMENTS),
        (SEARCH_TOOL, '{"query": "a\\nb"}', ToolRejection.INVALID_ARGUMENTS),
        (
            SEARCH_TOOL,
            json.dumps({"query": "x" * (MAX_QUERY_CHARACTERS + 1)}),
            ToolRejection.INVALID_ARGUMENTS,
        ),
        (
            SEARCH_TOOL,
            '{"query": "travel", "max_items": 6}',
            ToolRejection.INVALID_ARGUMENTS,
        ),
        (
            SEARCH_TOOL,
            '{"query": "travel", "max_items": 0}',
            ToolRejection.INVALID_ARGUMENTS,
        ),
        (
            SEARCH_TOOL,
            '{"query": "travel", "max_items": true}',
            ToolRejection.INVALID_ARGUMENTS,
        ),
        (
            SEARCH_TOOL,
            '{"query": "travel", "max_items": 2.0}',
            ToolRejection.INVALID_ARGUMENTS,
        ),
    ],
)
def test_invalid_calls_are_rejected_with_a_code_only(
    name: str, arguments: str, reason: ToolRejection
) -> None:
    """Unknown tools, malformed JSON, scope fields and bad values are refused."""
    with pytest.raises(ToolCallRejected) as rejected:
        parse_search_call(name, arguments)
    assert rejected.value.reason is reason
    assert str(rejected.value) == reason.value


def test_tool_spec_offers_no_scope_parameters() -> None:
    """The model-facing schema exposes only the query and the item count."""
    schema = json.loads(SEARCH_TOOL_SPEC.parameters)
    assert SEARCH_TOOL_SPEC.name == SEARCH_TOOL
    assert set(schema["properties"]) == {"query", "max_items"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["max_items"]["maximum"] == MAX_TOOL_ITEMS


@pytest.mark.parametrize(
    "question",
    ["", "   ", "???", "two\nlines", "x" * (MAX_QUESTION_CHARACTERS + 1)],
)
def test_invalid_questions_are_rejected(question: str) -> None:
    """Questions must be one bounded printable line with at least one word."""
    with pytest.raises(ValueError):
        check_question(question)


def test_question_rules_accept_cyrillic_and_reject_other_types() -> None:
    """Unicode words are accepted; non-string input is a type error."""
    assert check_question("Какой лимит на командировки?")
    with pytest.raises(TypeError):
        check_question(b"travel")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "valid"),
    [
        ("Travel limit is 500 credits.", True),
        ("Line one\n\tLine two", True),
        ("", False),
        ("   \n", False),
        ("bad\x00byte", False),
        ("x" * (MAX_ANSWER_CHARACTERS + 1), False),
        (42, False),
    ],
)
def test_answer_text_rules(text: object, valid: bool) -> None:
    """Answers are bounded, non-blank and free of control characters."""
    assert answer_text_is_valid(text) is valid  # type: ignore[arg-type]
