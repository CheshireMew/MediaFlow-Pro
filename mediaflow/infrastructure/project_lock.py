from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class ProjectWriteLock:
    """Process-scoped project lock; the OS releases it on abnormal termination."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._stream: BinaryIO | None = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None

    def __enter__(self) -> ProjectWriteLock:
        if not self.acquire():
            raise RuntimeError(f"Project is already open for writing: {self.lock_path.parent.parent}")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
