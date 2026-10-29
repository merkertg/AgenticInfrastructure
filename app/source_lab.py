"""Serve a fictional Handbook snapshot on loopback for the Stage 07 laboratory.

The server implements the read-only source contract the connector expects:
paginated listing, per-revision text and a bearer token. It deliberately does
not apply memory intake rules, so fixtures can contain text the sync rejects.
"""

import argparse
import hmac
import json
import os
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from app.connectors.handbook import check_token
from app.domain.strict_json import loads_strict

LAB_HOST = "127.0.0.1"
DEFAULT_PORT = 8105
_SNAPSHOT_KEYS = frozenset({"space", "page_size", "documents"})
_DOCUMENT_KEYS = frozenset({"id", "title", "revision", "readers", "content"})


@dataclass(frozen=True, slots=True)
class LabDocument:
    """Hold one fixture document as the source would describe and serve it."""

    id: str
    title: str
    revision: str
    readers: tuple[dict[str, Any], ...]
    content: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LabSnapshot:
    """Hold one fixture state of the source: its space, page size and documents."""

    space: str
    page_size: int
    documents: tuple[LabDocument, ...]


def load_snapshot(path: Path) -> LabSnapshot:
    """Read a fixture snapshot and check its shape, but not its content rules."""
    values = loads_strict(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or set(values) != _SNAPSHOT_KEYS:
        raise ValueError("snapshot needs exactly space, page_size and documents")
    page_size, documents = values["page_size"], values["documents"]
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer from 1 to 100")
    if not isinstance(values["space"], str) or not isinstance(documents, list):
        raise ValueError("space must be a string and documents a list")
    parsed: list[LabDocument] = []
    for entry in documents:
        if not isinstance(entry, dict) or set(entry) != _DOCUMENT_KEYS:
            raise ValueError(
                "each document needs id, title, revision, readers, content"
            )
        if not isinstance(entry["readers"], list) or not all(
            isinstance(reader, dict) for reader in entry["readers"]
        ):
            raise ValueError("readers must be a list of objects")
        if not all(
            isinstance(entry[key], str)
            for key in ("id", "title", "revision", "content")
        ):
            raise ValueError("id, title, revision and content must be strings")
        parsed.append(
            LabDocument(
                id=entry["id"],
                title=entry["title"],
                revision=entry["revision"],
                readers=tuple(entry["readers"]),
                content=entry["content"],
            )
        )
    if len({document.id for document in parsed}) != len(parsed):
        raise ValueError("document identifiers must be unique")
    return LabSnapshot(
        space=values["space"], page_size=page_size, documents=tuple(parsed)
    )


class LabServer(ThreadingHTTPServer):
    """Serve one snapshot on 127.0.0.1 to requests with the lab bearer token."""

    daemon_threads = True

    def __init__(self, snapshot: LabSnapshot, token: str, port: int = 0) -> None:
        """Bind to loopback only; port 0 picks a free port for tests."""
        self.snapshot = snapshot
        self._expected = f"Bearer {check_token(token)}".encode()
        super().__init__((LAB_HOST, port), _Handler)

    def authorized(self, header: str | None) -> bool:
        """Compare the Authorization header in constant time."""
        return header is not None and hmac.compare_digest(
            header.encode("utf-8", "replace"), self._expected
        )


class _Handler(BaseHTTPRequestHandler):
    """Answer the two read-only routes of the source contract."""

    server: LabServer

    def do_GET(self) -> None:
        """Serve a listing page or one revision's text, or refuse."""
        if not self.server.authorized(self.headers.get("Authorization")):
            self._send(401, "application/json", b'{"error":"unauthorized"}')
            return
        parts = urlsplit(self.path)
        segments = [unquote(segment) for segment in parts.path.split("/")]
        query = parse_qs(parts.query)
        snapshot = self.server.snapshot
        listing = ["", "v1", "spaces", snapshot.space, "documents"]
        if segments == listing:
            self._page(query.get("cursor", [None])[0])
        elif (
            len(segments) == 7 and segments[:5] == listing and segments[6] == "content"
        ):
            self._content(segments[5], query.get("revision", [""])[0])
        else:
            self._send(404, "application/json", b'{"error":"not_found"}')

    def _page(self, cursor: str | None) -> None:
        """Return the page that starts at cursor 'pN', with the next cursor."""
        snapshot = self.server.snapshot
        index = 0
        if cursor is not None:
            if not cursor.startswith("p") or not cursor[1:].isdigit():
                self._send(400, "application/json", b'{"error":"bad_cursor"}')
                return
            index = int(cursor[1:])
        start = index * snapshot.page_size
        documents = snapshot.documents[start : start + snapshot.page_size]
        more = start + snapshot.page_size < len(snapshot.documents)
        body = {
            "documents": [
                {
                    "id": document.id,
                    "title": document.title,
                    "revision": document.revision,
                    "readers": list(document.readers),
                }
                for document in documents
            ],
            "next_cursor": f"p{index + 1}" if more else None,
        }
        self._send(200, "application/json", json.dumps(body).encode())

    def _content(self, document_id: str, revision: str) -> None:
        """Return the text of the current revision; other revisions are not kept."""
        for document in self.server.snapshot.documents:
            if document.id == document_id and document.revision == revision:
                self._send(
                    200, "text/plain; charset=utf-8", document.content.encode("utf-8")
                )
                return
        self._send(404, "application/json", b'{"error":"not_found"}')

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        """Write a complete response with its length."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Log method, path and status only; headers, including the token, never."""
        sys.stderr.write(
            f"lab source: {self.command} {urlsplit(self.path).path} {code}\n"
        )


def main(argv: list[str] | None = None) -> int:
    """Serve the snapshot until interrupted; the token comes from SOURCE_TOKEN."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="fixture snapshot JSON file")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="loopback port")
    args = parser.parse_args(argv)
    try:
        token = check_token(os.environ.get("SOURCE_TOKEN", ""))
        snapshot = load_snapshot(args.snapshot)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    server = LabServer(snapshot, token, args.port)
    print(
        json.dumps(
            {
                "listening": f"http://{LAB_HOST}:{server.server_address[1]}",
                "space": snapshot.space,
                "documents": len(snapshot.documents),
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
