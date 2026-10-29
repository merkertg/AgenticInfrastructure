"""Check the HTTP connector and lab source on loopback; nothing leaves the machine."""

import http.client
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.application.source_sync import SyncFailed, SyncFailure, collect_snapshot
from app.connectors import handbook
from app.connectors.handbook import HandbookClient, check_token
from app.domain.source import MAX_PAGE_BYTES, SourceSettings, parse_listing_page
from app.domain.text import MAX_BYTES, TextRejection
from app.source_lab import LabServer, load_snapshot

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "lab-token-0123456789abcdef"
SETTINGS = SourceSettings(
    id="handbook",
    company_id="alpha",
    project_id="finance",
    base_url="http://127.0.0.1:1",
    space="alpha-handbook",
    document_prefix="hb-",
    max_age_seconds=900,
)


def _serve(server: ThreadingHTTPServer) -> Iterator[str]:
    """Run a server in a thread and yield its base URL."""
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


@pytest.fixture
def lab_url() -> Iterator[str]:
    """Serve the first fixture snapshot of the lab source."""
    snapshot = load_snapshot(ROOT / "fixtures/source/handbook-1.json")
    yield from _serve(LabServer(snapshot, TOKEN))


def _client(base_url: str, token: str = TOKEN) -> HandbookClient:
    """Build a client for one loopback base URL."""
    return HandbookClient(replace(SETTINGS, base_url=base_url), token)


Responder = Callable[[BaseHTTPRequestHandler], None]


@pytest.fixture
def scripted() -> Iterator[Callable[[Responder], str]]:
    """Start loopback servers whose GET handler is a test function."""
    servers: list[Iterator[str]] = []

    def start(respond: Responder) -> str:
        """Serve one responder and return its base URL."""

        class Handler(BaseHTTPRequestHandler):
            """Delegate GET to the responder and keep the test output quiet."""

            def do_GET(self) -> None:
                """Answer with the scripted response."""
                try:
                    respond(self)
                except (BrokenPipeError, ConnectionResetError):
                    # A timeout intentionally disconnects before the response.
                    pass

            def log_message(self, format: str, *args: Any) -> None:
                """Discard request logs."""

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        running = _serve(server)
        url = next(running)
        servers.append(running)
        return url

    yield start
    for running in servers:
        next(running, None)


def _reply(
    status: int,
    body: bytes = b"",
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> Responder:
    """Build a responder that sends one fixed response."""

    def respond(handler: BaseHTTPRequestHandler) -> None:
        """Send the status, headers and body."""
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    return respond


def test_lab_listing_and_texts_round_trip(lab_url: str) -> None:
    """The connector reads every page of the lab source and its texts."""
    client = _client(lab_url)
    snapshot = collect_snapshot(
        client, lambda entry: True, deadline=time.monotonic() + 30
    )
    assert [entry.external_id for entry in snapshot.documents] == [
        "travel-desk",
        "office-access",
        "vendor-review",
        "partner-note",
    ]
    office = snapshot.documents[1]
    assert office.principals == frozenset({"u-fin", "u-eng"})
    assert office.ignored_readers == 1
    assert str(snapshot.contents["travel-desk"]).startswith("Fictional Alpha travel")


def test_lab_second_state_includes_a_rejected_text(tmp_path: Path) -> None:
    """The second fixture serves a control character that intake rejects."""
    snapshot = load_snapshot(ROOT / "fixtures/source/handbook-2.json")
    for url in _serve(LabServer(snapshot, TOKEN)):
        result = collect_snapshot(
            _client(url), lambda entry: True, deadline=time.monotonic() + 30
        )
        assert result.contents["budget-draft"] is TextRejection.CONTROL_CHARACTERS
        assert "vendor-review" not in result.contents


def test_wrong_token_is_unauthorized(lab_url: str) -> None:
    """The lab source refuses other tokens, which the connector reports."""
    with pytest.raises(SyncFailed) as raised:
        _client(lab_url, "other-token-0123456789").list_page(None, timeout=5)
    assert raised.value.failure is SyncFailure.UNAUTHORIZED


def test_unknown_revision_is_unexpected(lab_url: str) -> None:
    """A text request for a revision the source no longer has is not accepted."""
    with pytest.raises(SyncFailed) as raised:
        _client(lab_url).fetch_content("travel-desk", "r9", timeout=5)
    assert raised.value.failure is SyncFailure.UNEXPECTED_RESPONSE


def test_requests_use_fixed_paths_and_send_the_token(
    scripted: Callable[[Responder], str],
) -> None:
    """Paths are built from quoted identifiers; the token goes in the header."""
    seen: list[tuple[str, str | None, str | None]] = []
    page = json.dumps({"documents": [], "next_cursor": None}).encode()

    def respond(handler: BaseHTTPRequestHandler) -> None:
        """Record the request and answer with an empty page or a text."""
        seen.append(
            (
                handler.path,
                handler.headers.get("Authorization"),
                handler.headers.get("Accept"),
            )
        )
        if handler.path.endswith("/documents") or "cursor=" in handler.path:
            _reply(200, page)(handler)
        else:
            _reply(200, b"Text.", "text/plain; charset=utf-8")(handler)

    url = scripted(respond)
    client = HandbookClient(replace(SETTINGS, base_url=url + "/base"), TOKEN)
    assert client.list_page(None, timeout=5) == page
    client.list_page("a+b/c=", timeout=5)
    assert client.fetch_content("travel", "r:1", timeout=5) == b"Text."
    assert seen == [
        (
            "/base/v1/spaces/alpha-handbook/documents",
            f"Bearer {TOKEN}",
            "application/json",
        ),
        (
            "/base/v1/spaces/alpha-handbook/documents?cursor=a%2Bb%2Fc%3D",
            f"Bearer {TOKEN}",
            "application/json",
        ),
        (
            "/base/v1/spaces/alpha-handbook/documents/travel/content?revision=r%3A1",
            f"Bearer {TOKEN}",
            "text/plain",
        ),
    ]


@pytest.mark.parametrize(
    "responder, failure",
    [
        (_reply(401), SyncFailure.UNAUTHORIZED),
        (_reply(403), SyncFailure.UNAUTHORIZED),
        (_reply(429), SyncFailure.UNAVAILABLE),
        (_reply(503), SyncFailure.UNAVAILABLE),
        (_reply(404), SyncFailure.UNEXPECTED_RESPONSE),
        (
            _reply(302, headers={"Location": "https://elsewhere.example/steal"}),
            SyncFailure.UNEXPECTED_RESPONSE,
        ),
        (_reply(200, b"{}", "text/html"), SyncFailure.UNEXPECTED_RESPONSE),
    ],
)
def test_http_failures_map_to_codes(
    scripted: Callable[[Responder], str], responder: Responder, failure: SyncFailure
) -> None:
    """Statuses, redirects and wrong media types fail without being followed."""
    with pytest.raises(SyncFailed) as raised:
        _client(scripted(responder)).list_page(None, timeout=5)
    assert raised.value.failure is failure
    assert TOKEN not in str(raised.value)


def test_bodies_are_read_only_to_one_byte_past_the_limit(
    scripted: Callable[[Responder], str],
) -> None:
    """Large bodies are cut so the caller can reject them without reading all."""
    url = scripted(_reply(200, b" " * (MAX_PAGE_BYTES * 2)))
    assert len(_client(url).list_page(None, timeout=5)) == MAX_PAGE_BYTES + 1
    text = scripted(_reply(200, b"x" * (MAX_BYTES * 2), "text/plain"))
    assert len(_client(text).fetch_content("a", "r1", timeout=5)) == MAX_BYTES + 1


def test_slow_source_times_out(scripted: Callable[[Responder], str]) -> None:
    """A source that does not answer within the request budget times out."""

    def stall(handler: BaseHTTPRequestHandler) -> None:
        """Wait longer than the client timeout before answering."""
        time.sleep(1.0)
        _reply(200, b"{}")(handler)

    with pytest.raises(SyncFailed) as raised:
        _client(scripted(stall)).list_page(None, timeout=0.2)
    assert raised.value.failure is SyncFailure.TIMEOUT


def test_slow_body_stops_at_the_deadline(scripted: Callable[[Responder], str]) -> None:
    """A sender that trickles bytes cannot hold the request past its budget."""

    def trickle(handler: BaseHTTPRequestHandler) -> None:
        """Send the body in small delayed pieces."""
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", "100")
        handler.end_headers()
        for _ in range(100):
            handler.wfile.write(b" ")
            handler.wfile.flush()
            time.sleep(0.05)

    started = time.monotonic()
    with pytest.raises(SyncFailed) as raised:
        _client(scripted(trickle)).list_page(None, timeout=0.5)
    assert raised.value.failure is SyncFailure.TIMEOUT
    assert time.monotonic() - started < 2.0


def test_trickled_headers_stop_at_the_deadline(
    scripted: Callable[[Responder], str],
) -> None:
    """Continuously arriving header bytes cannot extend the absolute deadline."""

    def trickle_headers(handler: BaseHTTPRequestHandler) -> None:
        """Send an unfinished status line slower than the absolute time budget."""
        for character in b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}":
            handler.wfile.write(bytes([character]))
            handler.wfile.flush()
            time.sleep(0.05)

    client = _client(scripted(trickle_headers))
    started = time.monotonic()
    with pytest.raises(SyncFailed) as raised:
        client.list_page(None, timeout=0.2)
    assert raised.value.failure is SyncFailure.TIMEOUT
    assert time.monotonic() - started < 1.0


def test_cancelled_connection_never_sends_a_late_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed resolver can finish later without sending the source token."""
    release = threading.Event()
    closed = threading.Event()
    requests: list[str] = []

    class DelayedConnection(http.client.HTTPConnection):
        """Simulate a blocking resolver followed by a connection."""

        def connect(self) -> None:
            """Wait until the test releases this OS-like blocking call."""
            release.wait(2)

        def request(self, *args: Any, **kwargs: Any) -> None:
            """Record a forbidden request after the caller has timed out."""
            requests.append("sent")

        def close(self) -> None:
            """Mark the worker as cleaned up."""
            closed.set()

    connection = DelayedConnection("localhost")
    monkeypatch.setattr(HandbookClient, "_connection", lambda self, timeout: connection)
    try:
        with pytest.raises(SyncFailed) as raised:
            _client("http://127.0.0.1:1").list_page(None, timeout=0.05)
        assert raised.value.failure is SyncFailure.TIMEOUT
    finally:
        release.set()
    assert closed.wait(1)
    assert requests == []


def test_network_worker_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An occupied worker budget refuses another request within its deadline."""
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(handbook, "_REQUEST_SLOTS", slots)
    started = time.monotonic()
    try:
        with pytest.raises(SyncFailed) as raised:
            _client("http://127.0.0.1:1").list_page(None, timeout=0.05)
        assert raised.value.failure is SyncFailure.TIMEOUT
        assert time.monotonic() - started < 1
    finally:
        slots.release()


def test_cancelled_detached_chunked_response_releases_its_worker(
    scripted: Callable[[Responder], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation shuts down a detached response blocked on a chunk-size line."""
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(handbook, "_REQUEST_SLOTS", slots)
    stop = threading.Event()

    def trickle_chunk_size(handler: BaseHTTPRequestHandler) -> None:
        """Detach the response, then trickle an unfinished chunk extension."""
        try:
            handler.wfile.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n1;"
            )
            handler.wfile.flush()
            while not stop.wait(0.02):
                handler.wfile.write(b"x")
                handler.wfile.flush()
        except OSError:
            pass

    client = _client(scripted(trickle_chunk_size))
    started = time.monotonic()
    try:
        with pytest.raises(SyncFailed) as raised:
            client.list_page(None, timeout=0.2)
        assert raised.value.failure is SyncFailure.TIMEOUT
        assert time.monotonic() - started < 1
        assert slots.acquire(timeout=0.5)
        slots.release()
    finally:
        stop.set()


def test_unreachable_source_is_unavailable() -> None:
    """A closed port is reported as an unavailable source."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(SyncFailed) as raised:
        _client(f"http://127.0.0.1:{port}").list_page(None, timeout=1)
    assert raised.value.failure is SyncFailure.UNAVAILABLE


def test_tokens_are_validated_and_hidden() -> None:
    """Malformed tokens are refused, and the client repr never shows the token."""
    for value in ("short", "has space in it 0123", "x" * 513, ""):
        with pytest.raises(ValueError) as raised:
            check_token(value)
        assert value not in str(raised.value) or not value
    client = _client("http://127.0.0.1:8105")
    assert TOKEN not in repr(client)
    assert not hasattr(client, "__dict__")
    with pytest.raises(TypeError):
        HandbookClient("http://127.0.0.1:8105", TOKEN)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        client.list_page(None, timeout=0)


def test_lab_snapshot_shape_is_checked(tmp_path: Path) -> None:
    """The lab refuses malformed fixture files before serving them."""
    path = tmp_path / "snapshot.json"
    for value in (
        {"space": "s", "page_size": 0, "documents": []},
        {"space": "s", "page_size": 2, "documents": [{"id": "a"}]},
        {"space": "s", "page_size": 2},
        {
            "space": "s",
            "page_size": 2,
            "documents": [
                {
                    "id": "a",
                    "title": "A",
                    "revision": "r",
                    "readers": [],
                    "content": "x",
                }
            ]
            * 2,
        },
    ):
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            load_snapshot(path)


def test_lab_pages_follow_the_contract(lab_url: str) -> None:
    """Each lab page parses with the connector's strict listing rules."""
    client = _client(lab_url)
    first = parse_listing_page(client.list_page(None, timeout=5))
    second = parse_listing_page(client.list_page(first.next_cursor, timeout=5))
    assert first.next_cursor == "p1" and second.next_cursor is None
    assert len(first.documents) == len(second.documents) == 2
