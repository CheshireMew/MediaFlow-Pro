from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mediaflow.infrastructure.resumable_download import download_with_resume


@contextmanager
def _download_server(payload: bytes, *, honor_ranges: bool = True, response_limit: int = 0):
    requests: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            range_header = self.headers.get("Range")
            requests.append(range_header)
            start = 0
            if honor_ranges and range_header:
                start = int(range_header.removeprefix("bytes=").removesuffix("-"))
                self.send_response(206)
            else:
                self.send_response(200)
            body = payload[start:]
            if response_limit:
                body = body[:response_limit]
            if honor_ranges and range_header:
                end = start + len(body) - 1
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/archive", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_resumable_download_continues_real_partial_file_and_reports_full_progress(
    tmp_path: Path,
) -> None:
    payload = b"one shared resumable download boundary"
    destination = tmp_path / "downloads" / "archive.bin"
    destination.parent.mkdir()
    destination.write_bytes(payload[:11])
    progress: list[tuple[int, int]] = []

    with _download_server(payload) as (url, requests):
        download_with_resume(
            url,
            destination,
            len(payload),
            progress=lambda completed, total: progress.append((completed, total)),
            chunk_size=4,
        )

    assert requests == ["bytes=11-"]
    assert destination.read_bytes() == payload
    assert progress[-1] == (len(payload), len(payload))


def test_resumable_download_restarts_when_server_ignores_range_or_file_is_oversized(
    tmp_path: Path,
) -> None:
    payload = b"authoritative archive"
    destination = tmp_path / "archive.bin"
    destination.write_bytes(b"stale partial")

    with _download_server(payload, honor_ranges=False) as (url, requests):
        download_with_resume(url, destination, len(payload))

    assert requests == [f"bytes={len(b'stale partial')}-"]
    assert destination.read_bytes() == payload

    destination.write_bytes(payload + b"oversized")
    with _download_server(payload) as (url, requests):
        download_with_resume(url, destination, len(payload))

    assert requests == [None]
    assert destination.read_bytes() == payload


def test_resumable_download_retries_short_responses_from_the_written_boundary(
    tmp_path: Path,
) -> None:
    payload = b"0123456789"
    destination = tmp_path / "archive.bin"

    with _download_server(payload, response_limit=3) as (url, requests):
        download_with_resume(url, destination, len(payload), attempts=4)

    assert requests == [None, "bytes=3-", "bytes=6-", "bytes=9-"]
    assert destination.read_bytes() == payload
