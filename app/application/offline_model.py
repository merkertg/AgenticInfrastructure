"""Answer deterministically by quoting retrieved passages, without a language model."""

import json
import re

from app.application.agent import DraftAnswer, ModelTurn, ToolCall, ToolExchange
from app.domain.retrieval import MAX_QUERY_CHARACTERS, MAX_QUERY_TERMS, terms
from app.domain.tool import DEFAULT_TOOL_ITEMS, SEARCH_TOOL, Citation, ToolSpec

OFFLINE_PROVIDER = "offline-extractive-v1"
MAX_QUOTE_CHARACTERS = 300
NO_EVIDENCE = "No authorized evidence answers this question."
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def search_query(question: str) -> str:
    """Build a valid query from the question's first distinct words."""
    words = list(dict.fromkeys(terms(question)))[:MAX_QUERY_TERMS]
    if not words:
        raise ValueError("question must contain at least one word")
    query = ""
    for word in words:
        candidate = f"{query} {word}".strip()
        if len(candidate) > MAX_QUERY_CHARACTERS:
            break
        query = candidate
    # A single word longer than the query limit is shortened instead of dropped.
    return query or words[0][:MAX_QUERY_CHARACTERS]


def first_sentence(text: str) -> str:
    """Quote the first sentence of the first line, shortened to a fixed length."""
    line = text.split("\n", 1)[0].strip()
    match = _SENTENCE_END.search(line)
    sentence = line[: match.start()] if match else line
    if len(sentence) > MAX_QUOTE_CHARACTERS:
        sentence = sentence[: MAX_QUOTE_CHARACTERS - 1].rstrip() + "…"
    return sentence


class OfflineProvider:
    """Search once with the question's words, then quote and cite the results.

    It never follows instructions inside passages: it only copies their first
    sentence. Equal questions and memory contents give equal answers.
    """

    name = OFFLINE_PROVIDER

    def next_turn(
        self,
        question: str,
        exchanges: tuple[ToolExchange, ...],
        tools: tuple[ToolSpec, ...],
        *,
        remaining_seconds: float,
    ) -> ModelTurn:
        """Request one search first; answer from its passages on the next step."""
        if not exchanges:
            if not any(tool.name == SEARCH_TOOL for tool in tools):
                return ModelTurn(answer=DraftAnswer(text=NO_EVIDENCE, citations=()))
            arguments = {
                "query": search_query(question),
                "max_items": DEFAULT_TOOL_ITEMS,
            }
            return ModelTurn(
                calls=(
                    ToolCall(
                        call_id="offline-search-1",
                        name=SEARCH_TOOL,
                        arguments=json.dumps(arguments, ensure_ascii=False),
                    ),
                )
            )
        result = json.loads(exchanges[-1].result)
        items = result.get("items", []) if isinstance(result, dict) else []
        lines: list[str] = []
        citations: list[Citation] = []
        for index, item in enumerate(items[:DEFAULT_TOOL_ITEMS], start=1):
            lines.append(f"[{index}] {first_sentence(item['text'])}")
            citations.append(
                Citation(
                    document_id=item["document_id"],
                    version_id=item["version_id"],
                    fragment=item["fragment"],
                )
            )
        if not citations:
            return ModelTurn(answer=DraftAnswer(text=NO_EVIDENCE, citations=()))
        return ModelTurn(
            answer=DraftAnswer(text="\n".join(lines), citations=tuple(citations))
        )
