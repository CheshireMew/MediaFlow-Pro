from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

DownloadProgress = Callable[[int, int], None]


class DownloadError(RuntimeError):
    pass


class DownloadTransferError(DownloadError):
    pass


class DownloadSizeError(DownloadError):
    def __init__(self, actual: int, expected: int):
        self.actual = actual
        self.expected = expected
        super().__init__(f"download is incomplete: {actual} / {expected}")


def download_with_resume(
    url: str,
    destination: Path,
    expected_size: int,
    *,
    progress: DownloadProgress | None = None,
    check_cancelled: Callable[[], None] | None = None,
    attempts: int = 4,
    timeout_seconds: float = 60,
    chunk_size: int = 1024 * 1024,
    user_agent: str = "MediaFlow Pro setup",
) -> None:
    """Download one file, resuming only when the server confirms the range."""

    if expected_size < 0:
        raise ValueError("Expected download size cannot be negative")
    if attempts < 1:
        raise ValueError("Download attempts must be positive")
    if chunk_size < 1:
        raise ValueError("Download chunk size must be positive")

    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        if check_cancelled:
            check_cancelled()
        current_size = destination.stat().st_size if destination.is_file() else 0
        if expected_size and current_size == expected_size:
            return

        resume_from = (
            current_size if current_size and (not expected_size or current_size < expected_size) else 0
        )
        headers = {"User-Agent": user_agent}
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout_seconds) as response:
                append = resume_from > 0 and getattr(response, "status", 200) == 206
                completed = resume_from if append else 0
                total = expected_size or _response_total(response, completed)
                with destination.open("ab" if append else "wb") as output:
                    while chunk := response.read(chunk_size):
                        if check_cancelled:
                            check_cancelled()
                        output.write(chunk)
                        completed += len(chunk)
                        if progress and total:
                            progress(min(completed, total), total)
        except (TimeoutError, URLError, OSError) as error:
            last_error = error
            if attempt == attempts:
                raise DownloadTransferError(str(error)) from error
            continue

        final_size = destination.stat().st_size if destination.is_file() else 0
        if not expected_size or final_size == expected_size:
            return
        if attempt < attempts:
            continue

    final_size = destination.stat().st_size if destination.is_file() else 0
    if last_error is not None:
        raise DownloadTransferError(str(last_error)) from last_error
    raise DownloadSizeError(final_size, expected_size)


def _response_total(response: object, completed_before_response: int) -> int:
    headers = getattr(response, "headers", {})
    content_range = str(headers.get("Content-Range") or "")
    match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+|\*)", content_range, flags=re.IGNORECASE)
    if match and match.group(1) != "*":
        return int(match.group(1))
    content_length = int(headers.get("Content-Length") or 0)
    return completed_before_response + content_length if content_length else 0
