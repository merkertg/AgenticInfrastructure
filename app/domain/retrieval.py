"""Rank already-authorized fragments deterministically and bound the context."""

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.domain.text import Fragment

MAX_QUERY_CHARACTERS = 256
MAX_QUERY_TERMS = 16
MAX_ITEMS = 10
MAX_CONTEXT_CHARACTERS = 4_000
_TERM = re.compile(r"\w+")
# Standard BM25 saturation and length normalization constants.
_K1 = 1.2
_B = 0.75


def terms(text: str) -> list[str]:
    """Split text into casefolded Unicode word terms."""
    return _TERM.findall(text.casefold())


def query_terms(query: str) -> tuple[str, ...]:
    """Validate a query and return its distinct terms in first-seen order."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if not 1 <= len(query.strip()) <= MAX_QUERY_CHARACTERS:
        raise ValueError(f"query must contain 1 to {MAX_QUERY_CHARACTERS} characters")
    if not query.isprintable():
        raise ValueError("query must be a single printable line")
    distinct = tuple(dict.fromkeys(terms(query)))
    if not distinct:
        raise ValueError("query must contain at least one word")
    if len(distinct) > MAX_QUERY_TERMS:
        raise ValueError(f"query must contain at most {MAX_QUERY_TERMS} terms")
    return distinct


@dataclass(frozen=True, slots=True)
class Candidate:
    """Pair one fragment with the authorized version it was cut from."""

    document_id: str
    version_id: str
    version_number: int
    source: str
    fragment: Fragment


@dataclass(frozen=True, slots=True)
class RankedFragment:
    """Attach a reproducible relevance score to a candidate."""

    candidate: Candidate
    score: float


def rank(
    query: tuple[str, ...], candidates: tuple[Candidate, ...]
) -> tuple[RankedFragment, ...]:
    """Score with BM25 statistics computed only from the supplied candidates."""
    if not candidates:
        return ()
    counted = [Counter(terms(item.fragment.text)) for item in candidates]
    lengths = [sum(counts.values()) for counts in counted]
    average = sum(lengths) / len(lengths) or 1.0
    frequency = {term: sum(1 for counts in counted if term in counts) for term in query}
    ranked: list[RankedFragment] = []
    for item, counts, length in zip(candidates, counted, lengths, strict=True):
        score = 0.0
        for term in query:
            occurrences = counts.get(term, 0)
            if not occurrences:
                continue
            documents = frequency[term]
            idf = math.log(1 + (len(candidates) - documents + 0.5) / (documents + 0.5))
            norm = _K1 * (1 - _B + _B * length / average)
            score += idf * occurrences * (_K1 + 1) / (occurrences + norm)
        if score > 0:
            ranked.append(RankedFragment(candidate=item, score=round(score, 6)))
    ranked.sort(
        key=lambda entry: (
            -entry.score,
            entry.candidate.document_id,
            entry.candidate.version_number,
            entry.candidate.fragment.ordinal,
        )
    )
    return tuple(ranked)


def select_context(
    ranked: tuple[RankedFragment, ...],
    *,
    max_items: int,
    max_characters: int = MAX_CONTEXT_CHARACTERS,
) -> tuple[RankedFragment, ...]:
    """Keep the best fragments in rank order that fit the item and size budgets."""
    if type(max_items) is not int or not 1 <= max_items <= MAX_ITEMS:
        raise ValueError(f"max_items must be an integer from 1 to {MAX_ITEMS}")
    selected: list[RankedFragment] = []
    used = 0
    for entry in ranked:
        if len(selected) == max_items:
            break
        size = len(entry.candidate.fragment.text)
        # A fragment that does not fit is skipped; a smaller later one may fit.
        if used + size > max_characters:
            continue
        selected.append(entry)
        used += size
    return tuple(selected)
