from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import BinaryIO


class ProcessFileLock:
    """Exclusive byte-range lock whose ownership is released by the operating system."""

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
            if sys.platform == "win32":
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
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None

    def __enter__(self) -> ProcessFileLock:
        if not self.acquire():
            raise RuntimeError(f"File lock is already owned: {self.lock_path}")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
