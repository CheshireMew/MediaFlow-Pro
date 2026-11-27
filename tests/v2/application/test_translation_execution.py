from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Condition, Event

import pytest

from mediaflow.application.translation_execution import TranslationBatchExecutor
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.subtitles import SubtitleSegment


def _segments() -> list[SubtitleSegment]:
    return [
        SubtitleSegment(
            document_id="source",
            start_frame=index * 25,
            end_frame=(index + 1) * 25,
            text=f"Line {index}",
        )
        for index in range(25)
    ]


def test_parallel_translation_returns_source_order_and_reports_completed_batches() -> None:
    executor = TranslationBatchExecutor()
    segments = _segments()
    batches = executor.build(segments, "standard")
    releases = [Event() for _ in batches]
    condition = Condition()
    started: set[int] = set()
    progress: list[OperationProgress] = []

    def worker(batch):
        with condition:
            started.add(batch.index)
            condition.notify_all()
        if not releases[batch.index].wait(10):
            raise TimeoutError("Test did not release the provider call")
        return list(batch.segments)

    def report(value: OperationProgress) -> None:
        with condition:
            progress.append(value)
            condition.notify_all()

    with ThreadPoolExecutor(max_workers=1) as runner:
        result = runner.submit(
            executor.run,
            batches,
            worker,
            progress=report,
            message="Translating",
            check_cancelled=None,
        )
        try:
            with condition:
                assert condition.wait_for(lambda: len(started) == len(batches), timeout=5)
            # Make the provider finish in the reverse order of the subtitle document.
            for completed, index in enumerate(reversed(range(len(batches))), start=1):
                releases[index].set()
                with condition:
                    assert condition.wait_for(
                        lambda expected=completed + 1: len(progress) == expected, timeout=5
                    )
            assert result.result(timeout=5) == segments
            assert [value.completed for value in progress] == [0, 1, 2, 3]
            assert all(value.total == 3 and value.unit == "items" for value in progress)
        finally:
            for release in releases:
                release.set()


def test_translation_cancellation_waits_for_every_running_provider_call() -> None:
    executor = TranslationBatchExecutor()
    batches = executor.build(_segments(), "standard")
    release = Event()
    cancelled = Event()
    cancellation_observed = Event()
    condition = Condition()
    started: set[int] = set()
    finished: set[int] = set()

    def worker(batch):
        with condition:
            started.add(batch.index)
            condition.notify_all()
        if not release.wait(10):
            raise TimeoutError("Test did not release the provider call")
        with condition:
            finished.add(batch.index)
        return list(batch.segments)

    def check_cancelled() -> None:
        if cancelled.is_set():
            cancellation_observed.set()
            raise RuntimeError("Translation cancelled")

    with ThreadPoolExecutor(max_workers=1) as runner:
        result = runner.submit(
            executor.run,
            batches,
            worker,
            progress=None,
            message="Translating",
            check_cancelled=check_cancelled,
        )
        try:
            with condition:
                assert condition.wait_for(lambda: len(started) == len(batches), timeout=5)
            cancelled.set()
            assert cancellation_observed.wait(5)
            with pytest.raises(TimeoutError):
                result.result(timeout=0.1)
            assert finished == set()
            release.set()
            with pytest.raises(RuntimeError, match="Translation cancelled"):
                result.result(timeout=5)
            assert finished == set(range(len(batches)))
        finally:
            release.set()
