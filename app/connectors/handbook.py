"""Read the fictional Handbook source over HTTP(S): one host, GET only, no redirects.

The adapter builds every request path itself from validated identifiers. It
never follows a redirect or a URL from a payload, ignores proxy variables and
sends the read-only token only to the configured host.
"""

import http.client
import re
import socket
import ssl
import threading
import time
from queue import Empty, Queue
from urllib.parse import quote, urlencode, urlsplit

from app.application.source_sync import SyncFailed, SyncFailure
from app.domain.source import MAX_PAGE_BYTES, SourceSettings
from app.domain.text import MAX_BYTES

USER_AGENT = "agentic-infrastructure-sync/1"
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")
_CHUNK = 16_384
# Bound even resolver calls that the operating system cannot cancel promptly.
_REQUEST_SLOTS = threading.BoundedSemaphore(4)


def check_token(value: str) -> str:
    """Require a bearer token of safe characters; the message never echoes it."""
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError("source token must be 16 to 512 URL-safe characters")
    return value


class HandbookClient:
    """Fetch listing pages and texts of one space with a read-only bearer token."""

    __slots__ = ("_https", "_host", "_port", "_path", "_space", "_token", "_context")

    def __init__(self, settings: SourceSettings, token: str) -> None:
        """Bind the client to the validated settings; nothing connects yet."""
        if not isinstance(settings, SourceSettings):
            raise TypeError("settings must be SourceSettings")
        parts = urlsplit(settings.base_url)
        assert parts.hostname is not None
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port
        self._path = parts.path
        self._space = quote(settings.space, safe="")
        self._token = check_token(token)
        self._context = ssl.create_default_context() if self._https else None

    def __repr__(self) -> str:
        """Show the target without the token."""
        return f"HandbookClient(host={self._host!r}, space={self._space!r})"

    def list_page(self, cursor: str | None, *, timeout: float) -> bytes:
        """Return one raw listing page, at most one byte over the page limit."""
        query = "" if cursor is None else "?" + urlencode({"cursor": cursor})
        return self._get(
            f"{self._path}/v1/spaces/{self._space}/documents{query}",
            "application/json",
            MAX_PAGE_BYTES,
            timeout,
        )

    def fetch_content(
        self, external_id: str, revision: str, *, timeout: float
    ) -> bytes:
        """Return one raw document text, at most one byte over the intake limit."""
        document = quote(external_id, safe="")
        query = urlencode({"revision": revision})
        return self._get(
            f"{self._path}/v1/spaces/{self._space}/documents/{document}/content?{query}",
            "text/plain",
            MAX_BYTES,
            timeout,
        )

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        """Open a connection to the configured host only, without proxies."""
        if self._https:
            return http.client.HTTPSConnection(
                self._host, self._port, timeout=timeout, context=self._context
            )
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout)

    def _get(self, target: str, media_type: str, limit: int, timeout: float) -> bytes:
        """Bound the caller's wait across DNS, connection, headers and body.

        A daemon worker lets the caller leave even if DNS or headers stall.
        Timed-out sockets are shut down; at most four network workers may linger
        in uninterruptible OS calls, and cancellation prevents a later request.
        """
        if not isinstance(timeout, int | float) or not 0 < timeout <= 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        ends = time.monotonic() + timeout
        if not _REQUEST_SLOTS.acquire(timeout=timeout):
            raise SyncFailed(SyncFailure.TIMEOUT)
        if time.monotonic() >= ends:
            _REQUEST_SLOTS.release()
            raise SyncFailed(SyncFailure.TIMEOUT)
        connection = self._connection(timeout)
        cancelled = threading.Event()
        result: Queue[bytes | SyncFailure] = Queue(maxsize=1)
        connected_sockets: list[socket.socket] = []

        def exchange() -> None:
            """Run one bounded read and release its worker slot in every outcome."""
            try:
                connection.connect()
                connected = connection.sock
                if connected is not None:
                    # getresponse() can detach a Connection: close socket.
                    connected_sockets.append(connected)
                if cancelled.is_set() or time.monotonic() >= ends:
                    raise SyncFailed(SyncFailure.TIMEOUT)
                result.put(self._exchange(connection, target, media_type, limit, ends))
            except SyncFailed as failure:
                result.put(failure.failure)
            except TimeoutError:
                result.put(SyncFailure.TIMEOUT)
            except Exception:
                # No transport exception or untrusted response is ever echoed.
                result.put(SyncFailure.UNAVAILABLE)
            finally:
                connection.close()
                _REQUEST_SLOTS.release()

        worker = threading.Thread(target=exchange, daemon=True)
        try:
            worker.start()
        except Exception:
            connection.close()
            _REQUEST_SLOTS.release()
            raise SyncFailed(SyncFailure.UNAVAILABLE) from None
        try:
            value = result.get(timeout=max(0, ends - time.monotonic()))
        except Empty:
            cancelled.set()
            for connected in connected_sockets:
                try:
                    connected.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            raise SyncFailed(SyncFailure.TIMEOUT) from None
        if isinstance(value, SyncFailure):
            raise SyncFailed(value)
        if time.monotonic() >= ends:
            raise SyncFailed(SyncFailure.TIMEOUT)
        return value

    def _exchange(
        self,
        connection: http.client.HTTPConnection,
        target: str,
        media_type: str,
        limit: int,
        ends: float,
    ) -> bytes:
        """Send the fixed GET and read a validated response inside a worker."""
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": media_type,
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": USER_AGENT,
                },
            )
            with connection.getresponse() as response:
                if response.status in {401, 403}:
                    raise SyncFailed(SyncFailure.UNAUTHORIZED)
                if response.status == 429 or response.status >= 500:
                    raise SyncFailed(SyncFailure.UNAVAILABLE)
                content_type = response.getheader("Content-Type", "")
                if (
                    response.status != 200
                    or content_type.split(";")[0].strip().lower() != media_type
                ):
                    raise SyncFailed(SyncFailure.UNEXPECTED_RESPONSE)
                return _read_bounded(response, limit, ends)
        except TimeoutError:
            raise SyncFailed(SyncFailure.TIMEOUT) from None
        except (OSError, http.client.HTTPException):
            raise SyncFailed(SyncFailure.UNAVAILABLE) from None


def _read_bounded(response: http.client.HTTPResponse, limit: int, ends: float) -> bytes:
    """Read at most limit + 1 bytes and stop once the request deadline passes.

    Each read waits at most the socket timeout, so a slow sender cannot hold a
    request much longer than its budget.
    """
    chunks: list[bytes] = []
    size = 0
    while size <= limit:
        if time.monotonic() >= ends:
            raise SyncFailed(SyncFailure.TIMEOUT)
        chunk = response.read1(min(_CHUNK, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)
