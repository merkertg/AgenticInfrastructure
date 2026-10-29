"""Run a model against one server-scoped memory tool within fixed limits."""

import json
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.application.context import ContextItem, ContextOutcome, ContextResult
from app.domain.models import ReasonCode
from app.domain.tool import (
    MAX_CITATIONS,
    SEARCH_TOOL_SPEC,
    Citation,
    ToolCallRejected,
    ToolSpec,
    answer_text_is_valid,
    check_question,
    parse_search_call,
)

logger = logging.getLogger(__name__)
_CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
TOOLS = (SEARCH_TOOL_SPEC,)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Carry one model-requested call; the server validates its raw JSON arguments."""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolExchange:
    """Pair a call with the JSON result the model receives for it."""

    call: ToolCall
    result: str


@dataclass(frozen=True, slots=True)
class DraftAnswer:
    """Hold proposed answer text and the passages the model claims it relies on."""

    text: str = field(repr=False)
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """Return either tool calls or one final answer from a provider step."""

    calls: tuple[ToolCall, ...] = ()
    answer: DraftAnswer | None = None


class ModelProvider(Protocol):
    """Separate the bounded loop from any particular model or SDK."""

    name: str

    def next_turn(
        self,
        question: str,
        exchanges: tuple[ToolExchange, ...],
        tools: tuple[ToolSpec, ...],
        *,
        remaining_seconds: float,
    ) -> ModelTurn:
        """Propose tool calls or an answer; never receives scope or credentials."""
        ...


SearchTool = Callable[[str, int], ContextResult]


class RunOutcome(StrEnum):
    """Name how a run ended; only the first two return answer data to the caller."""

    ANSWERED = "answered"
    UNANSWERED = "unanswered"
    DENIED = "denied"
    EVIDENCE_REVOKED = "evidence_revoked"
    PROVIDER_ERROR = "provider_error"
    INVALID_TURN = "invalid_turn"
    INVALID_ANSWER = "invalid_answer"
    INVALID_CITATION = "invalid_citation"
    TOOL_CALL_LIMIT = "tool_call_limit"
    STEP_LIMIT = "step_limit"
    TIME_LIMIT = "time_limit"
    TRANSCRIPT_LIMIT = "transcript_limit"


@dataclass(frozen=True, slots=True)
class RunLimits:
    """Bound model steps, tool calls, wall time and the text a model may receive."""

    max_steps: int = 4
    max_tool_calls: int = 3
    timeout_seconds: float = 15.0
    max_transcript_characters: int = 24_000

    def __post_init__(self) -> None:
        """Reject limits outside the documented safe ranges."""
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 8:
            raise ValueError("max_steps must be an integer from 1 to 8")
        if type(self.max_tool_calls) is not int or not 1 <= self.max_tool_calls <= 10:
            raise ValueError("max_tool_calls must be an integer from 1 to 10")
        if (
            not isinstance(self.timeout_seconds, int | float)
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds must be finite, above 0 and at most 120")
        if (
            type(self.max_transcript_characters) is not int
            or not 1_000 <= self.max_transcript_characters <= 64_000
        ):
            raise ValueError(
                "max_transcript_characters must be an integer from 1000 to 64000"
            )


@dataclass(frozen=True, slots=True)
class AgentRun:
    """Summarize a run; answer and citations are set only for an answered run."""

    outcome: RunOutcome
    provider: str
    steps: int = 0
    tool_calls: int = 0
    searches: tuple[str, ...] = ()
    reason: ReasonCode = ReasonCode.ALLOWED
    answer: str | None = field(default=None, repr=False)
    citations: tuple[ContextItem, ...] = ()


def _tool_result(result: ContextResult) -> str:
    """Serialize an allowed search for the model with its references and text."""
    return json.dumps(
        {
            "status": result.outcome.value,
            "truncated": result.truncated,
            "items": [
                {
                    "document_id": item.document_id,
                    "version_id": item.version_id,
                    "version_number": item.version_number,
                    "source": item.source,
                    "fragment": item.fragment,
                    "text": item.text,
                }
                for item in result.items
            ],
        },
        ensure_ascii=False,
    )


def _transcript_size(question: str, exchanges: list[ToolExchange]) -> int:
    """Count every character the next provider step would receive."""
    return len(question) + sum(
        len(item.call.call_id)
        + len(item.call.name)
        + len(item.call.arguments)
        + len(item.result)
        for item in exchanges
    )


def _valid_turn(turn: object, seen: set[str]) -> bool:
    """Require exactly one of calls or answer, well-typed values and fresh call IDs."""
    if not isinstance(turn, ModelTurn) or not isinstance(turn.calls, tuple):
        return False
    if bool(turn.calls) == (turn.answer is not None):
        return False
    if turn.answer is not None:
        answer = turn.answer
        return (
            isinstance(answer, DraftAnswer)
            and isinstance(answer.citations, tuple)
            and all(
                isinstance(item, Citation)
                and isinstance(item.document_id, str)
                and isinstance(item.version_id, str)
                and type(item.fragment) is int
                for item in answer.citations
            )
        )
    batch: set[str] = set()
    for call in turn.calls:
        if (
            not isinstance(call, ToolCall)
            or not isinstance(call.call_id, str)
            or not isinstance(call.name, str)
            or not isinstance(call.arguments, str)
            or _CALL_ID.fullmatch(call.call_id) is None
            or call.call_id in seen
            or call.call_id in batch
        ):
            return False
        batch.add(call.call_id)
    return True


def run_agent(
    question: str,
    *,
    provider: ModelProvider,
    search: SearchTool,
    limits: RunLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AgentRun:
    """Let a provider call the scoped search tool, then accept a grounded answer.

    The search callable is already bound by the server to one authenticated agent
    and project. A scope denial ends the run without telling the model anything.
    Answers may cite only references returned by this run's searches.
    """
    check_question(question)
    limits = limits if limits is not None else RunLimits()
    deadline = clock() + limits.timeout_seconds
    exchanges: list[ToolExchange] = []
    evidence: dict[Citation, ContextItem] = {}
    searches: list[str] = []
    seen: set[str] = set()
    steps = 0

    def finish(
        outcome: RunOutcome,
        *,
        reason: ReasonCode = ReasonCode.ALLOWED,
        answer: str | None = None,
        citations: tuple[ContextItem, ...] = (),
    ) -> AgentRun:
        """Close the run with the counters gathered so far."""
        return AgentRun(
            outcome=outcome,
            provider=provider.name,
            steps=steps,
            tool_calls=len(seen),
            searches=tuple(searches),
            reason=reason,
            answer=answer,
            citations=citations,
        )

    for _ in range(limits.max_steps):
        if clock() >= deadline:
            return finish(RunOutcome.TIME_LIMIT)
        if _transcript_size(question, exchanges) > limits.max_transcript_characters:
            return finish(RunOutcome.TRANSCRIPT_LIMIT)
        steps += 1
        try:
            turn = provider.next_turn(
                question,
                tuple(exchanges),
                TOOLS,
                remaining_seconds=deadline - clock(),
            )
        except Exception as error:
            # The type alone is logged: provider messages may quote passages.
            logger.warning("model provider failure: %s", type(error).__name__)
            return finish(RunOutcome.PROVIDER_ERROR)
        if clock() >= deadline:
            return finish(RunOutcome.TIME_LIMIT)
        if not _valid_turn(turn, seen):
            return finish(RunOutcome.INVALID_TURN)
        if turn.answer is not None:
            answer = turn.answer
            if not answer_text_is_valid(answer.text):
                return finish(RunOutcome.INVALID_ANSWER)
            cited = answer.citations
            if (
                len(cited) > MAX_CITATIONS
                or len(set(cited)) != len(cited)
                or any(item not in evidence for item in cited)
            ):
                return finish(RunOutcome.INVALID_CITATION)
            if not cited:
                # Uncited text is never returned: it may not come from memory.
                return finish(RunOutcome.UNANSWERED)
            return finish(
                RunOutcome.ANSWERED,
                answer=answer.text,
                citations=tuple(evidence[item] for item in cited),
            )
        if len(seen) + len(turn.calls) > limits.max_tool_calls:
            return finish(RunOutcome.TOOL_CALL_LIMIT)
        for call in turn.calls:
            seen.add(call.call_id)
            try:
                arguments = parse_search_call(call.name, call.arguments)
            except ToolCallRejected as rejection:
                exchanges.append(
                    ToolExchange(call, json.dumps({"error": rejection.reason.value}))
                )
                continue
            if clock() >= deadline:
                return finish(RunOutcome.TIME_LIMIT)
            result = search(arguments.query, arguments.max_items)
            searches.append(result.request.request_id)
            if result.outcome is ContextOutcome.DENIED:
                return finish(RunOutcome.DENIED, reason=result.reason)
            for item in result.items:
                evidence[Citation(item.document_id, item.version_id, item.fragment)] = (
                    item
                )
            exchanges.append(ToolExchange(call, _tool_result(result)))
    return finish(RunOutcome.STEP_LIMIT)
