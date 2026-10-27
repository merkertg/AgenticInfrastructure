"""Verify query rules, deterministic ranking, context budgets and scope checks."""

from dataclasses import replace

import pytest

from app.domain.models import Action, ReasonCode, ScopeRequest
from app.domain.policy import evaluate_scope
from app.domain.retrieval import (
    MAX_QUERY_TERMS,
    Candidate,
    query_terms,
    rank,
    select_context,
)
from app.domain.text import Fragment
from tests.domain.fixtures import AccessCase


def _candidate(document_id: str, text: str, ordinal: int = 1) -> Candidate:
    """Wrap fictional text as one fragment of version 1 of a document."""
    return Candidate(
        document_id=document_id,
        version_id=f"{document_id}-v1",
        version_number=1,
        source=f"fixture:{document_id}",
        fragment=Fragment(ordinal=ordinal, start=0, end=len(text), text=text),
    )


def test_query_terms_are_casefolded_distinct_and_unicode_aware() -> None:
    """Terms keep first-seen order and include Cyrillic words."""
    assert query_terms("Travel ТРАВЕЛ travel, отчёт!") == ("travel", "травел", "отчёт")


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "!!!",
        "line\nbreak",
        "x" * 257,
        " ".join(f"t{index}" for index in range(MAX_QUERY_TERMS + 1)),
    ],
)
def test_invalid_queries_are_rejected(query: str) -> None:
    """Empty, wordless, multiline, oversized and overlong queries fail."""
    with pytest.raises(ValueError):
        query_terms(query)


def test_ranking_is_deterministic_and_ties_follow_document_order() -> None:
    """Equal scores are ordered by document ID; zero scores are excluded."""
    candidates = (
        _candidate("b-doc", "travel budget"),
        _candidate("a-doc", "travel budget"),
        _candidate("c-doc", "unrelated text"),
    )
    first = rank(("travel",), candidates)
    assert first == rank(("travel",), tuple(reversed(candidates)))
    assert [entry.candidate.document_id for entry in first] == ["a-doc", "b-doc"]
    assert first[0].score == first[1].score > 0


def test_more_matching_terms_rank_higher() -> None:
    """A passage matching more query terms outranks a partial match."""
    ranked = rank(
        ("travel", "limit"),
        (
            _candidate("partial", "travel rules for staff"),
            _candidate("full", "travel limit for staff"),
        ),
    )
    assert [entry.candidate.document_id for entry in ranked] == ["full", "partial"]


def test_scores_depend_only_on_supplied_candidates() -> None:
    """Statistics come from the given set, so excluded text cannot shift scores."""
    authorized = (
        _candidate("allowed", "travel limit"),
        _candidate("other", "office hours"),
    )
    with_extra = (*authorized, _candidate("hidden", "travel travel travel"))
    assert (
        rank(("travel",), authorized)[0].score != rank(("travel",), with_extra)[0].score
    )
    assert rank(("travel",), authorized) == rank(("travel",), authorized)
    assert rank(("travel",), ()) == ()


def test_context_selection_respects_item_and_character_budgets() -> None:
    """Oversized passages are skipped and the item limit stops selection."""
    ranked = rank(
        ("travel",),
        (
            _candidate("a", "travel " * 300),
            _candidate("b", "travel limit"),
            _candidate("c", "travel note"),
            _candidate("d", "travel memo"),
        ),
    )
    selected = select_context(ranked, max_items=2, max_characters=100)
    assert len(selected) == 2
    assert all(len(entry.candidate.fragment.text) < 100 for entry in selected)
    with pytest.raises(ValueError):
        select_context(ranked, max_items=0)
    with pytest.raises(ValueError):
        select_context(ranked, max_items=11)


def _scope(case: AccessCase) -> ScopeRequest:
    """Derive a project-wide read request from the finance policy fixture."""
    request = case.request
    return ScopeRequest(
        request_id=request.request_id,
        company_id=request.company_id,
        project_id=request.project_id,
        employee_id=request.employee_id,
        agent_id=request.agent_id,
        action=Action.READ,
    )


def _evaluate(case: AccessCase, request: ScopeRequest) -> ReasonCode:
    """Evaluate project scope with the fixture's stored objects."""
    return evaluate_scope(
        request,
        company=case.company,
        project=case.project,
        employee=case.employee,
        agent=case.agent,
        delegation=case.delegation,
    )


def test_scope_allows_a_delegated_project(case: AccessCase) -> None:
    """An active employee, agent and read delegation allow a project search."""
    assert _evaluate(case, _scope(case)) is ReasonCode.ALLOWED


def test_scope_denies_other_projects_revocation_and_missing_actions(
    case: AccessCase,
) -> None:
    """Scope uses the same denial reasons as document access."""
    request = _scope(case)
    assert (
        _evaluate(case, replace(request, project_id="engineering"))
        is ReasonCode.PROJECT_MISMATCH
    )
    assert case.delegation is not None and case.agent is not None
    revoked = replace(case, delegation=replace(case.delegation, active=False))
    assert _evaluate(revoked, request) is ReasonCode.DELEGATION_UNAVAILABLE
    write_only = replace(
        case, delegation=replace(case.delegation, actions=frozenset({Action.WRITE}))
    )
    assert _evaluate(write_only, request) is ReasonCode.ACTION_NOT_DELEGATED
    inactive = replace(case, agent=replace(case.agent, active=False))
    assert _evaluate(inactive, request) is ReasonCode.AGENT_UNAVAILABLE
    assert (
        _evaluate(replace(case, company=None), request)
        is ReasonCode.COMPANY_UNAVAILABLE
    )


def test_scope_rejects_wrong_input_types(case: AccessCase) -> None:
    """Scope checks require a ScopeRequest and correctly typed snapshot entries."""
    with pytest.raises(TypeError):
        evaluate_scope(
            case.request,  # type: ignore[arg-type]
            company=case.company,
            project=case.project,
            employee=case.employee,
            agent=case.agent,
            delegation=case.delegation,
        )
    with pytest.raises(TypeError):
        evaluate_scope(
            _scope(case),
            company=case.project,  # type: ignore[arg-type]
            project=case.project,
            employee=case.employee,
            agent=case.agent,
            delegation=case.delegation,
        )
