from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.subtitles import SubtitleSegment
from mediaflow.domain.translation import TranslationMode

TranslationProgress = Callable[[OperationProgress], None]


@dataclass(frozen=True, slots=True)
class TranslationBatch:
    index: int
    segments: tuple[SubtitleSegment, ...]
    context_before: tuple[SubtitleSegment, ...] = ()


def check_translation_cancelled(check_cancelled: Callable[[], None] | None) -> None:
    if check_cancelled:
        check_cancelled()


class TranslationBatchExecutor:
    BATCH_SIZE = 10
    CONTEXT_OVERLAP = 3
    MAX_CONCURRENCY = 3

    def build(
        self,
        segments: list[SubtitleSegment],
        mode: TranslationMode,
    ) -> list[TranslationBatch]:
        batches: list[TranslationBatch] = []
        for index, offset in enumerate(range(0, len(segments), self.BATCH_SIZE)):
            context = (
                segments[max(0, offset - self.CONTEXT_OVERLAP) : offset] if mode != "intelligent" else []
            )
            batches.append(
                TranslationBatch(
                    index=index,
                    segments=tuple(segments[offset : offset + self.BATCH_SIZE]),
                    context_before=tuple(context),
                )
            )
        return batches

    def run(
        self,
        batches: list[TranslationBatch],
        worker: Callable[[TranslationBatch], list[SubtitleSegment]],
        *,
        progress: TranslationProgress | None,
        message: str,
        check_cancelled: Callable[[], None] | None,
    ) -> list[SubtitleSegment]:
        results: dict[int, list[SubtitleSegment]] = {}
        completed = 0

        def store(batch: TranslationBatch, value: list[SubtitleSegment]) -> None:
            nonlocal completed
            results[batch.index] = value
            completed += 1
            if progress:
                progress(
                    OperationProgress.determinate(
                        message,
                        completed=completed,
                        total=len(batches),
                        unit="items",
                    )
                )

        if progress:
            progress(
                OperationProgress.determinate(
                    message,
                    completed=0,
                    total=len(batches),
                    unit="items",
                )
            )
        if len(batches) == 1:
            check_translation_cancelled(check_cancelled)
            store(batches[0], worker(batches[0]))
        else:
            executor = ThreadPoolExecutor(
                max_workers=min(self.MAX_CONCURRENCY, len(batches)),
                thread_name_prefix="mediaflow-translation",
            )
            pending: dict[Future[list[SubtitleSegment]], TranslationBatch] = {
                executor.submit(worker, batch): batch for batch in batches
            }
            try:
                while pending:
                    check_translation_cancelled(check_cancelled)
                    done, _ = wait(
                        tuple(pending),
                        timeout=0.05,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        batch = pending.pop(future)
                        store(batch, future.result())
            except Exception:
                for future in pending:
                    future.cancel()
                raise
            finally:
                # A task is not paused, cancelled, or failed until every provider
                # call has actually stopped. Returning while worker threads are
                # still spending quota makes the persisted task state untruthful.
                executor.shutdown(wait=True, cancel_futures=True)
        return [segment for index in range(len(batches)) for segment in results[index]]
