from __future__ import annotations

import threading
from pathlib import Path

from .web_capture_engine import (
    WebCaptureEngine,
    get_web_capture_engine,
    release_web_capture_engine,
)

_PREWARM_LOCK = threading.Lock()
_PREWARMING: set[Path] = set()


def prewarm_web_capture_engine(executable: Path) -> bool:
    """Launch one pooled Chromium worker in the background, once per active request."""

    resolved = executable.resolve()
    with _PREWARM_LOCK:
        if resolved in _PREWARMING:
            return False
        _PREWARMING.add(resolved)

    def prewarm() -> None:
        engine: WebCaptureEngine | None = None
        try:
            engine = get_web_capture_engine(resolved)
            engine.prewarm(worker_count=1)
        except Exception:
            # Prewarming is opportunistic. The real render path still reports
            # launch or runtime failures through its normal capture evidence.
            pass
        finally:
            if engine is not None:
                release_web_capture_engine(resolved, engine)
            with _PREWARM_LOCK:
                _PREWARMING.discard(resolved)

    thread = threading.Thread(
        target=prewarm,
        name="mediaflow-web-capture-prewarm",
        daemon=True,
    )
    thread.start()
    return True
