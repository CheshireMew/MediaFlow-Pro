from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from mediaflow.application.ports import SubtitlePublicationDocuments
from mediaflow.atomic_file import atomic_write_bytes
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


@dataclass(frozen=True, slots=True)
class _OutputSnapshot:
    path: Path
    existed: bool
    content: bytes


class SubtitlePublicationService:
    """Commit subtitle records and their observable SRT as one user-visible change."""

    def __init__(self, repository: SubtitlePublicationDocuments):
        self.repository = repository

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
        _write_document_srt(self.repository, document_id, output)
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
                generated_root.rglob("sub-*.srt")
                if generated_root.is_dir()
                else ()
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
                _snapshot_output(output) for output in outputs
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
                    if _snapshot_changed(snapshot):
                        _rollback_document_srt(
                            self.repository,
                            document_id,
                            snapshot.path,
                            previous_exists=snapshot.existed,
                            previous_content=snapshot.content,
                            error=error,
                        )
                for original, archived in reversed(
                    stale_publications
                ):
                    if not archived.exists():
                        continue
                    original.parent.mkdir(parents=True, exist_ok=True)
                    archived.replace(original)

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
                )
            for original, archived in stale_publications:
                archived.parent.mkdir(parents=True, exist_ok=True)
                original.replace(archived)
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
            snapshot = _snapshot_output(output)

            def rollback(error: BaseException) -> None:
                if not _snapshot_changed(snapshot):
                    return
                _rollback_document_srt(
                    self.repository,
                    document_id,
                    output,
                    previous_exists=snapshot.existed,
                    previous_content=snapshot.content,
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
                _snapshot_output(output) for output in outputs
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
                    if _snapshot_changed(snapshot):
                        _rollback_document_srt(
                            self.repository,
                            current_document_id,
                            snapshot.path,
                            previous_exists=snapshot.existed,
                            previous_content=snapshot.content,
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
                )
        return result, outputs


def _snapshot_output(output: Path) -> _OutputSnapshot:
    if output.exists():
        if not output.is_file():
            raise RuntimeError(f"字幕发布目标不是普通文件：{output}")
        return _OutputSnapshot(
            path=output,
            existed=True,
            content=output.read_bytes(),
        )
    return _OutputSnapshot(path=output, existed=False, content=b"")


def _snapshot_changed(snapshot: _OutputSnapshot) -> bool:
    try:
        if not snapshot.path.exists():
            return snapshot.existed
        if not snapshot.path.is_file():
            return True
        return (
            not snapshot.existed
            or snapshot.path.read_bytes() != snapshot.content
        )
    except OSError:
        return True


def _write_document_srt(
    repository: SubtitlePublicationDocuments,
    document_id: str,
    output: Path,
) -> bool:
    document = repository.subtitles.get_subtitle_document(document_id)
    segments = repository.subtitles.list_subtitle_segments(document.id)
    project = repository.catalog.get_project()
    profile = repository.catalog.get_sequence(project.main_sequence_id).profile
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
    destination = require_windows_interop_path(output)
    if destination.is_file() and destination.read_bytes() == encoded:
        return False
    atomic_write_bytes(destination, encoded)
    return True


def _rollback_document_srt(
    repository: SubtitlePublicationDocuments,
    document_id: str,
    output: Path,
    *,
    previous_exists: bool,
    previous_content: bytes,
    error: BaseException,
) -> None:
    archived: Path | None = None
    if output.exists():
        archive_root = repository.project_dir / "archive" / "subtitle-publications"
        archived = content_addressed_child_path(
            archive_root,
            f"{document_id}:{new_id()}",
            namespace="sub-fail",
            suffix=output.suffix or ".srt",
        )
        try:
            archived.parent.mkdir(parents=True, exist_ok=True)
            output.replace(archived)
        except OSError as archive_error:
            error.add_note(
                "未能把失败的字幕发布移入归档："
                f"{archive_error}"
            )
            archived = None
    if previous_exists:
        try:
            atomic_write_bytes(output, previous_content)
        except OSError as restore_error:
            error.add_note(
                "字幕数据库已回滚，但旧 SRT 恢复失败："
                f"{restore_error}"
            )
    elif output.exists() and archived is None:
        fallback = require_windows_interop_path(
            content_addressed_child_path(
                output.parent,
                f"failed-subtitle-publication:{document_id}:{new_id()}",
                namespace="sub-fail",
                suffix=output.suffix or ".srt",
            )
        )
        try:
            output.replace(fallback)
        except OSError as withdraw_error:
            error.add_note(
                "字幕数据库已回滚，但未登记的 SRT 无法撤回："
                f"{withdraw_error}"
            )
