from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from mediaflow.domain.asr import AsrProgress
from mediaflow.domain.progress import OperationProgress

from .project_lock import ProcessFileLock
from .runtime_paths import RuntimePaths


@contextmanager
def asr_inference_slot(
    paths: RuntimePaths,
    *,
    check_cancelled: Callable[[], None] | None = None,
    progress: AsrProgress | None = None,
) -> Iterator[None]:
    """Share one model slot across transcription, prewarm and editor processes."""
    lock = ProcessFileLock(paths.runtime_dir / "locks" / "asr-inference.lock")
    notified = False
    while True:
        if check_cancelled:
            check_cancelled()
        if lock.acquire():
            break
        if progress and not notified:
            progress(OperationProgress.indeterminate("asr_waiting_for_model"))
            notified = True
        threading.Event().wait(0.1)
    try:
        if check_cancelled:
            check_cancelled()
        yield
    finally:
        lock.release()
