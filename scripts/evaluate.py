"""Run independent expected-result cases against a running memory API.

Cases in evaluations/cases.json describe only externally visible behavior: the
status, which documents may appear, phrases that must never appear and whether
every returned reference reproduces text the same agent may read. The checks
are pure functions, so tests can show that a deliberately corrupted result fails.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.source import check_base_url
from app.domain.versions import derive_version_id

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evaluations" / "cases.json"
# Well-formed but never issued: it must be refused like any unknown credential.
UNKNOWN_TOKEN = "aik1_" + "A" * 43
AGENTS = frozenset({"fin", "eng", "beta", "unknown"})
_PLACEHOLDER = re.compile(r"\{version:([^:{}]+):([^:{}]+):([0-9]+)\}")
_PROJECT = re.compile(r"^/memory/projects/([^/]+)/")
_QUOTE = re.compile(r"\[([0-9]+)\] (.+)")
_REQUEST_KEYS = frozenset({"method", "path", "json", "headers"})
_EXPECT_KEYS = frozenset(
    {
        "status",
        "body",
        "phrases_present",
        "phrases_absent",
        "documents_include",
        "documents_exclude",
        "documents_allowed",
        "answer_status",
        "must_cite",
        "verify_references",
        "grounded_quotes",
    }
)


@dataclass(frozen=True, slots=True)
class Observation:
    """Hold one HTTP response: status, parsed JSON (or None) and raw text."""

    status: int
    body: Any
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Record one case's outcome; problems never include response bodies."""

    case_id: str
    status: int
    problems: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Tell whether every expectation of the case held."""
        return not self.problems


Reader = Callable[[str], Observation]


def resolve(value: Any) -> Any:
    """Replace {version:COMPANY:DOCUMENT:NUMBER} with the documented version ID."""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(
            lambda match: derive_version_id(
                match.group(1), match.group(2), int(match.group(3))
            ),
            value,
        )
    if isinstance(value, list):
        return [resolve(item) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item) for key, item in value.items()}
    return value


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    """Read and shape-check the case file so a typo cannot silently pass."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("the case file needs a non-empty cases list")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "id",
            "purpose",
            "agent",
            "request",
            "expect",
        }:
            raise ValueError("each case needs id, purpose, agent, request and expect")
        case_id, request, expect = case["id"], case["request"], case["expect"]
        if not isinstance(case_id, str) or case_id in seen:
            raise ValueError(f"case IDs must be unique strings: {case_id!r}")
        seen.add(case_id)
        if case["agent"] is not None and case["agent"] not in AGENTS:
            raise ValueError(f"{case_id}: unknown agent")
        if (
            not isinstance(request, dict)
            or not set(request) <= _REQUEST_KEYS
            or request.get("method") not in {"GET", "POST"}
            or not str(request.get("path", "")).startswith("/")
        ):
            raise ValueError(f"{case_id}: invalid request")
        if (
            not isinstance(expect, dict)
            or not set(expect) <= _EXPECT_KEYS
            or type(expect.get("status")) is not int
        ):
            raise ValueError(f"{case_id}: invalid expectations")
    return cases


def _references(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return context items or answer citations from a response body."""
    for key in ("items", "citations"):
        values = body.get(key)
        if isinstance(values, list):
            return [value for value in values if isinstance(value, dict)]
    return []


def _span(
    reference: dict[str, Any], project: str, read: Reader
) -> tuple[str | None, str | None]:
    """Re-read a referenced version; return its cited span or a problem."""
    path = (
        f"/memory/projects/{project}/documents/{reference.get('document_id')}"
        f"/versions/{reference.get('version_id')}"
    )
    observation = read(path)
    if observation.status != 200 or not isinstance(observation.body, dict):
        return None, f"reference {reference.get('document_id')} is not readable"
    content = observation.body.get("content")
    start, end = reference.get("start"), reference.get("end")
    if (
        not isinstance(content, str)
        or type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(content)
    ):
        return None, f"reference {reference.get('document_id')} has an invalid span"
    return content[start:end], None


def check_case(
    case: dict[str, Any], observation: Observation, read: Reader
) -> tuple[str, ...]:
    """Compare one observed response with the case's expectations."""
    expect = case["expect"]
    problems: list[str] = []
    if observation.status != expect["status"]:
        problems.append(f"status {observation.status}, expected {expect['status']}")
    if "body" in expect and observation.body != expect["body"]:
        problems.append("body differs from the expected body")
    text = observation.text.casefold()
    problems += [
        f"missing phrase: {phrase}"
        for phrase in expect.get("phrases_present", [])
        if phrase.casefold() not in text
    ]
    problems += [
        f"forbidden phrase present: {phrase}"
        for phrase in expect.get("phrases_absent", [])
        if phrase.casefold() in text
    ]
    body = observation.body
    if observation.status != 200 or not isinstance(body, dict):
        return tuple(problems)
    references = _references(body)
    documents = [str(reference.get("document_id")) for reference in references]
    problems += [
        f"expected document missing: {name}"
        for name in expect.get("documents_include", [])
        if name not in documents
    ]
    problems += [
        f"excluded document returned: {name}"
        for name in expect.get("documents_exclude", [])
        if name in documents
    ]
    if "documents_allowed" in expect:
        problems += [
            f"document outside the allowed set: {name}"
            for name in sorted(set(documents) - set(expect["documents_allowed"]))
        ]
    if "answer_status" in expect and body.get("status") != expect["answer_status"]:
        problems.append(
            f"answer status {body.get('status')}, expected {expect['answer_status']}"
        )
    problems += [
        f"required citation missing: {name}"
        for name in expect.get("must_cite", [])
        if name not in documents
    ]
    spans: list[str | None] = []
    if expect.get("verify_references") or expect.get("grounded_quotes"):
        match = _PROJECT.match(case["request"]["path"])
        project = match.group(1) if match else ""
        for reference in references:
            span, problem = _span(reference, project, read)
            spans.append(span)
            if problem is not None:
                problems.append(problem)
            elif "text" in reference and reference["text"] != span:
                name = reference.get("document_id")
                problems.append(f"reference {name} text differs from its span")
    if expect.get("grounded_quotes"):
        problems += _ungrounded(body.get("answer"), spans)
    return tuple(problems)


def _ungrounded(answer: object, spans: list[str | None]) -> list[str]:
    """Require every answer line to quote the span of the citation it names.

    This checks the deterministic offline provider, which answers by quoting;
    a generative provider would need a different groundedness measure.
    """
    if not isinstance(answer, str) or not answer:
        return ["answer text is missing"]
    problems: list[str] = []
    for line in answer.splitlines():
        match = _QUOTE.fullmatch(line)
        if match is None:
            problems.append("answer line without a citation marker")
            continue
        index = int(match.group(1)) - 1
        span = spans[index] if 0 <= index < len(spans) else None
        if span is None or match.group(2) not in span:
            problems.append(f"answer line {index + 1} is not quoted from its citation")
    return problems


class Api:
    """Send case requests to one base URL with the token of each lab agent."""

    def __init__(
        self, base_url: str, tokens: Mapping[str, str], timeout: float = 20.0
    ) -> None:
        """Accept HTTPS or loopback HTTP only, so tokens reach no other host."""
        self.base_url = check_base_url(base_url)
        self._tokens = dict(tokens)
        self._timeout = timeout
        # Environment proxies are ignored: lab requests stay on this machine.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __repr__(self) -> str:
        """Show the target without tokens."""
        return f"Api(base_url={self.base_url!r})"

    def request(
        self,
        agent: str | None,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Observation:
        """Send one request as the agent (None sends no credential)."""
        values = {"Accept": "application/json", **(headers or {})}
        if agent == "unknown":
            values["Authorization"] = f"Bearer {UNKNOWN_TOKEN}"
        elif agent is not None:
            values["Authorization"] = f"Bearer {self._tokens[agent]}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            values["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=values, method=method
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        text = raw.decode("utf-8", "replace")
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        return Observation(status=status, body=parsed, text=text)


def _reader(api: Api, agent: str | None) -> Reader:
    """Read referenced versions as one agent, each path at most once."""
    cache: dict[str, Observation] = {}

    def read(path: str) -> Observation:
        """Return the cached or freshly read version response."""
        if path not in cache:
            cache[path] = api.request(agent, "GET", path)
        return cache[path]

    return read


def run_cases(api: Api, cases: list[dict[str, Any]]) -> list[CaseResult]:
    """Run every case and check it; references are re-read as the same agent."""
    results: list[CaseResult] = []
    for case in cases:
        resolved = resolve(case)
        request, agent = resolved["request"], resolved["agent"]
        observation = api.request(
            agent,
            request["method"],
            request["path"],
            body=request.get("json"),
            headers=request.get("headers"),
        )
        results.append(
            CaseResult(
                case_id=case["id"],
                status=observation.status,
                problems=check_case(resolved, observation, _reader(api, agent)),
            )
        )
    return results


def summary(results: list[CaseResult]) -> dict[str, Any]:
    """Summarize results without tokens or response bodies."""
    return {
        "cases": len(results),
        "passed": sum(result.passed for result in results),
        "failed": [
            {"id": result.case_id, "status": result.status, "problems": result.problems}
            for result in results
            if not result.passed
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Evaluate a running API; tokens come from a local JSON file, never arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8005")
    parser.add_argument(
        "--tokens",
        type=Path,
        required=True,
        help='JSON file {"fin": TOKEN, "eng": TOKEN, "beta": TOKEN} outside Git',
    )
    parser.add_argument("--cases", type=Path, default=CASES)
    args = parser.parse_args(argv)
    try:
        tokens = json.loads(args.tokens.read_text(encoding="utf-8"))
        if not isinstance(tokens, dict) or not {"fin", "eng", "beta"} <= set(tokens):
            raise ValueError("the token file needs fin, eng and beta")
        api = Api(args.base_url, tokens)
        cases = load_cases(args.cases)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    report = summary(run_cases(api, cases))
    json.dump(report, sys.stdout, indent=2)
    print()
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
