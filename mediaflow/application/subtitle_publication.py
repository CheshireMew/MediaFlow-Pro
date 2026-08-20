from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from mediaflow.application.ports import SubtitlePublicationDocuments
from mediaflow.application.subtitle_publication_storage import SubtitlePublicationStorage
from mediaflow.domain.model_base import new_id
from mediaflow.domain.storage_names import (
    content_addressed_child_path,
    require_windows_interop_path,
)
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SubtitleDocumentPublication:
    document: SubtitleDocument
    segments: tuple[SubtitleSegment, ...]


class SubtitlePublicationService:
    """Commit subtitle records and their observable SRT as one user-visible change."""

    def __init__(
        self,
        repository: SubtitlePublicationDocuments,
        storage: SubtitlePublicationStorage,
    ):
        self.repository = repository
        self.storage = storage

    def document_srt_path(
        self,
        document_id: str,
        destination: str | Path | None = None,
    ) -> Path:
        if destination is not None:
            return require_windows_interop_path(destination)
        return require_windows_interop_path(
            content_addressed_child_path(
                self.repository.project_dir / "generated" / "subtitles",
                f"subtitle-document:{document_id}",
                namespace="sub",
                suffix=".srt",
            )
        )

    def write_document_srt(
        self,
        document_id: str,
        destination: str | Path | None = None,
    ) -> Path:
        output = self.document_srt_path(document_id, destination)
        if self.repository.read_only:
            try:
                output.relative_to(self.repository.project_dir.resolve())
            except ValueError:
                pass
            else:
                raise PermissionError(
                    "只读项目不能写入项目目录；请选择项目外的导出位置"
                )
        _write_document_srt(self.repository, document_id, output, self.storage)
        return output

    def reconcile_document_srts(self) -> tuple[Path, ...]:
        if self.repository.read_only:
            return ()
        documents = tuple(
            self.repository.subtitles.list_subtitle_documents()
        )
        document_ids = tuple(document.id for document in documents)
        outputs = tuple(
            self.document_srt_path(document_id)
            for document_id in document_ids
        )
        expected = {output.resolve() for output in outputs}
        generated_root = (
            self.repository.project_dir
            / "generated"
            / "subtitles"
        )
        stale = tuple(
            path
            for path in (
                self.storage.list_files(generated_root, "sub-*.srt")
            )
            if path.resolve() not in expected
        )
        stale_publications = tuple(
            (
                path,
                content_addressed_child_path(
                    self.repository.project_dir
                    / "archive"
                    / "subtitle-publications",
                    (
                        "stale:"
                        f"{path.relative_to(generated_root).as_posix()}:"
                        f"{new_id()}"
                    ),
                    namespace="sub-stale",
                    suffix=".srt",
                ),
            )
            for path in stale
        )
        with self.repository.transaction():
            snapshots = tuple(
                self.storage.snapshot(output) for output in outputs
            )

            def rollback(error: BaseException) -> None:
                for document_id, snapshot in reversed(
                    tuple(
                        zip(
                            document_ids,
                            snapshots,
                            strict=True,
                        )
                    )
                ):
                    if self.storage.changed(snapshot):
                        self.storage.rollback(
                            snapshot,
                            project_dir=self.repository.project_dir,
                            document_id=document_id,
                            error=error,
                        )
                for original, archived in reversed(
                    stale_publications
                ):
                    if not self.storage.exists(archived):
                        continue
                    self.storage.move(archived, original)

            self.repository.enlist_transaction_publication(
                on_commit=lambda: None,
                on_rollback=rollback,
            )
            for document_id, output in zip(
                document_ids,
                outputs,
                strict=True,
            ):
                _write_document_srt(
                    self.repository,
                    document_id,
                    output,
                    self.storage,
                )
            for original, archived in stale_publications:
                self.storage.move(original, archived)
        return outputs

    def commit_document_change(
        self,
        document_id: str,
        change: Callable[[], T],
        *,
        destination: str | Path | None = None,
        prepare_output: Callable[[Path], None] | None = None,
        after_write: Callable[[Path, T], None] | None = None,
    ) -> tuple[T, Path]:
        """Publish one database mutation and its SRT, restoring both on failure."""

        output = self.document_srt_path(document_id, destination)
        with self.repository.transaction():
            snapshot = self.storage.snapshot(output)

            def rollback(error: BaseException) -> None:
                if not self.storage.changed(snapshot):
                    return
                self.storage.rollback(
                    snapshot,
                    project_dir=self.repository.project_dir,
                    document_id=document_id,
                    error=error,
                )

            self.repository.enlist_transaction_publication(
                on_commit=lambda: None,
                on_rollback=rollback,
            )
            if prepare_output is not None:
                prepare_output(output)
            result = change()
            _write_document_srt(
                self.repository,
                document_id,
                output,
                self.storage,
            )
            if after_write is not None:
                after_write(output, result)
        return result, output

    def commit_prepared_documents(
        self,
        change: Callable[[], T],
        publications: Iterable[SubtitleDocumentPublication],
    ) -> tuple[T, tuple[Path, ...]]:
        """Commit one producer change and every derived SRT as a single unit."""

        prepared = tuple(publications)
        document_ids = [item.document.id for item in prepared]
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("一次字幕发布不能包含重复文档")
        outputs = tuple(
            self.document_srt_path(document_id)
            for document_id in document_ids
        )
        with self.repository.transaction():
            snapshots = tuple(
                self.storage.snapshot(output) for output in outputs
            )

            def rollback(error: BaseException) -> None:
                for current_document_id, snapshot in reversed(
                    tuple(
                        zip(
                            document_ids,
                            snapshots,
                            strict=True,
                        )
                    )
                ):
                    if self.storage.changed(snapshot):
                        self.storage.rollback(
                            snapshot,
                            project_dir=self.repository.project_dir,
                            document_id=current_document_id,
                            error=error,
                        )

            self.repository.enlist_transaction_publication(
                on_commit=lambda: None,
                on_rollback=rollback,
            )
            result = change()
            for item, output in zip(
                prepared,
                outputs,
                strict=True,
            ):
                self.repository.subtitles.create_subtitle_document(
                    item.document,
                    list(item.segments),
                )
                _write_document_srt(
                    self.repository,
                    item.document.id,
                    output,
                    self.storage,
                )
        return result, outputs


def _write_document_srt(
    repository: SubtitlePublicationDocuments,
    document_id: str,
    output: Path,
    storage: SubtitlePublicationStorage,
) -> bool:
    document = repository.subtitles.get_subtitle_document(document_id)
    segments = repository.subtitles.list_subtitle_segments(document.id)
    project = repository.projects.get_project()
    profile = repository.sequences.get_sequence(project.main_sequence_id).profile
    content = SubtitleFile.dumps_srt(
        [
            SubtitleCue(
                start_frame=segment.start_frame,
                end_frame=segment.end_frame,
                text=segment.text,
            )
            for segment in segments
        ],
        fps_numerator=profile.fps_numerator,
        fps_denominator=profile.fps_denominator,
    )
    encoded = content.encode("utf-8-sig")
    return storage.publish(require_windows_interop_path(output), encoded)
