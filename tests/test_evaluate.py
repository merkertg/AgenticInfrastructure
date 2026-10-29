"""Show that the evaluation checks pass sound results and catch corrupted ones."""

import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.domain.versions import derive_version_id
from scripts.evaluate import (
    UNKNOWN_TOKEN,
    Api,
    CaseResult,
    Observation,
    check_case,
    load_cases,
    resolve,
    summary,
)

CONTENT = "Fictional travel policy.\n\nThe travel limit is 500 credits per trip."
SPAN = (26, len(CONTENT))
CASE: dict[str, Any] = {
    "id": "grounded",
    "purpose": "test",
    "agent": "fin",
    "request": {"method": "POST", "path": "/memory/projects/finance/answers"},
    "expect": {
        "status": 200,
        "answer_status": "answered",
        "must_cite": ["travel-policy"],
        "documents_allowed": ["travel-policy", "expense-guide"],
        "phrases_present": ["500 credits"],
        "phrases_absent": ["Deployments happen"],
        "verify_references": True,
        "grounded_quotes": True,
    },
}


def _answer(**changes: Any) -> Observation:
    """Build a sound answer observation, optionally corrupting fields."""
    body: dict[str, Any] = {
        "status": "answered",
        "answer": "[1] The travel limit is 500 credits per trip.",
        "citations": [
            {
                "document_id": "travel-policy",
                "version_id": "v1",
                "version_number": 1,
                "source": "file:travel-policy.txt",
                "fragment": 2,
                "start": SPAN[0],
                "end": SPAN[1],
            }
        ],
        "provider": "offline-extractive-v1",
        "tool_calls": 1,
    }
    body.update(changes)
    return Observation(status=200, body=body, text=json.dumps(body))


def _reader(status: int = 200, content: str = CONTENT) -> Any:
    """Return a version reader that serves one fixed version."""

    def read(path: str) -> Observation:
        """Answer every version read with the same response."""
        body = {"content": content} if status == 200 else {"detail": "x"}
        return Observation(status=status, body=body, text=json.dumps(body))

    return read


def test_sound_answer_passes() -> None:
    """A correct, grounded answer meets every expectation."""
    assert check_case(CASE, _answer(), _reader()) == ()


@pytest.mark.parametrize(
    ("corruption", "problem"),
    [
        ({"status": "unanswered"}, "answer status unanswered"),
        (
            {"answer": "[1] Deployments happen on Tuesdays."},
            "forbidden phrase present: Deployments happen",
        ),
        (
            {"answer": "[1] The travel limit is 900 credits per trip."},
            "missing phrase: 500 credits",
        ),
        (
            {"answer": "The travel limit is 500 credits per trip."},
            "answer line without a citation marker",
        ),
        (
            {"answer": "[2] The travel limit is 500 credits per trip."},
            "answer line 2 is not quoted from its citation",
        ),
    ],
)
def test_corrupted_answers_fail(corruption: dict[str, Any], problem: str) -> None:
    """Wrong status, leaked or missing phrases and unquoted lines are caught."""
    assert problem in " | ".join(check_case(CASE, _answer(**corruption), _reader()))


def test_corrupted_references_fail() -> None:
    """Foreign documents, broken spans and unreadable citations are caught."""
    citation = _answer().body["citations"][0]
    foreign = _answer(citations=[{**citation, "document_id": "deploy-notes"}])
    problems = check_case(CASE, foreign, _reader())
    assert "document outside the allowed set: deploy-notes" in problems
    assert "required citation missing: travel-policy" in problems
    shifted = _answer(citations=[{**citation, "start": 0, "end": 10}])
    assert "answer line 1 is not quoted from its citation" in check_case(
        CASE, shifted, _reader()
    )
    beyond = _answer(citations=[{**citation, "end": len(CONTENT) + 5}])
    assert "reference travel-policy has an invalid span" in check_case(
        CASE, beyond, _reader()
    )
    assert "reference travel-policy is not readable" in check_case(
        CASE, _answer(), _reader(status=404)
    )


def test_context_text_must_equal_its_span() -> None:
    """A returned passage that differs from the stored span is caught."""
    case = {
        **CASE,
        "request": {"method": "POST", "path": "/memory/projects/finance/context"},
        "expect": {"status": 200, "verify_references": True},
    }
    item = {
        "document_id": "travel-policy",
        "version_id": "v1",
        "start": SPAN[0],
        "end": SPAN[1],
        "text": CONTENT[SPAN[0] : SPAN[1]],
    }
    sound = Observation(200, {"items": [item]}, "{}")
    assert check_case(case, sound, _reader()) == ()
    forged = Observation(
        200, {"items": [{**item, "text": "Limit is unlimited."}]}, "{}"
    )
    assert check_case(case, forged, _reader()) == (
        "reference travel-policy text differs from its span",
    )


def test_refusals_must_match_status_and_body() -> None:
    """A leaked 200 or a different refusal body fails a refusal case."""
    case = {
        **CASE,
        "expect": {
            "status": 404,
            "body": {"detail": "memory context not available"},
            "phrases_absent": ["500 credits"],
        },
    }
    refusal = Observation(404, {"detail": "memory context not available"}, "{}")
    assert check_case(case, refusal, _reader()) == ()
    leaked = _answer()
    problems = check_case(case, leaked, _reader())
    assert "status 200, expected 404" in problems
    assert "forbidden phrase present: 500 credits" in problems
    other = Observation(404, {"detail": "no such project"}, "{}")
    assert check_case(case, other, _reader()) == (
        "body differs from the expected body",
    )


def test_documents_include_and_exclude() -> None:
    """Missing expected documents and returned excluded ones are both caught."""
    case = {
        **CASE,
        "expect": {
            "status": 200,
            "documents_include": ["travel-policy"],
            "documents_exclude": ["hb-budget-draft"],
        },
    }
    body = {"items": [{"document_id": "hb-budget-draft"}]}
    assert check_case(case, Observation(200, body, "{}"), _reader()) == (
        "expected document missing: travel-policy",
        "excluded document returned: hb-budget-draft",
    )


def test_case_file_is_valid_and_placeholders_resolve() -> None:
    """The committed cases load, and version placeholders use the documented IDs."""
    cases = load_cases()
    assert len(cases) == len({case["id"] for case in cases}) >= 20
    assert resolve({"a": ["{version:alpha:travel-policy:1}"]}) == {
        "a": [derive_version_id("alpha", "travel-policy", 1)]
    }


@pytest.mark.parametrize(
    "case",
    [
        {"id": "x"},
        {**CASE, "agent": "admin"},
        {**CASE, "request": {"method": "DELETE", "path": "/x"}},
        {**CASE, "request": {"method": "GET", "path": "relative"}},
        {**CASE, "expect": {"status": "200"}},
        {**CASE, "expect": {"status": 200, "phrases_maybe": []}},
    ],
)
def test_malformed_case_files_are_rejected(tmp_path: Path, case: Any) -> None:
    """A typo in the case file fails loudly instead of passing silently."""
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [case]}))
    with pytest.raises(ValueError):
        load_cases(path)
    path.write_text(json.dumps({"cases": [CASE, CASE]}))
    with pytest.raises(ValueError):
        load_cases(path)


def test_summary_lists_only_failures() -> None:
    """The summary counts passes and names failed cases with their problems."""
    results = [
        CaseResult("a", 200, ()),
        CaseResult("b", 200, ("status 200, expected 404",)),
    ]
    assert summary(results) == {
        "cases": 2,
        "passed": 1,
        "failed": [
            {"id": "b", "status": 200, "problems": ("status 200, expected 404",)}
        ],
    }
    assert replace(results[1], problems=()).passed


@pytest.fixture
def echo_url() -> Iterator[str]:
    """Serve a loopback stub that echoes the Authorization header."""

    class Handler(BaseHTTPRequestHandler):
        """Return 404 with the received credential, as JSON."""

        def do_POST(self) -> None:
            """Echo the header and request body."""
            length = int(self.headers.get("Content-Length", "0"))
            body = json.dumps(
                {
                    "authorization": self.headers.get("Authorization"),
                    "received": json.loads(self.rfile.read(length) or b"null"),
                }
            ).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            """Discard request logs."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_api_sends_the_right_credential(echo_url: str) -> None:
    """Each agent sends its own token, none sends nothing, unknown sends a fake one."""
    api = Api(echo_url, {"fin": "aik1_fin"})
    assert "aik1_fin" not in repr(api)
    observed = api.request("fin", "POST", "/x", body={"query": "travel"})
    assert observed.status == 404
    assert observed.body == {
        "authorization": "Bearer aik1_fin",
        "received": {"query": "travel"},
    }
    assert api.request(None, "POST", "/x").body["authorization"] is None
    assert api.request("unknown", "POST", "/x").body["authorization"] == (
        f"Bearer {UNKNOWN_TOKEN}"
    )


def test_api_refuses_remote_plain_http() -> None:
    """Tokens are never sent over plain HTTP to a non-loopback host."""
    with pytest.raises(ValueError):
        Api("http://memory.alpha.example", {})
