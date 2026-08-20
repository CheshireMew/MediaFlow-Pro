from __future__ import annotations

from pathlib import Path

from mediaflow.application.subtitle_publication_storage import SubtitleOutputSnapshot
from mediaflow.atomic_file import atomic_write_bytes
from mediaflow.domain.model_base import new_id
from mediaflow.domain.storage_names import (
    content_addressed_child_path,
    require_windows_interop_path,
)


class LocalSubtitlePublicationStorage:
    """Transactional local-file adapter for observable subtitle publications."""

    def list_files(self, root: Path, pattern: str) -> tuple[Path, ...]:
        return tuple(root.rglob(pattern)) if root.is_dir() else ()

    def exists(self, path: Path) -> bool:
        return path.exists()

    def move(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)

    def snapshot(self, output: Path) -> SubtitleOutputSnapshot:
        if output.exists():
            if not output.is_file():
                raise RuntimeError(f"字幕发布目标不是普通文件：{output}")
            return SubtitleOutputSnapshot(
                path=output,
                existed=True,
                content=output.read_bytes(),
            )
        return SubtitleOutputSnapshot(path=output, existed=False, content=b"")

    def changed(self, snapshot: SubtitleOutputSnapshot) -> bool:
        try:
            if not snapshot.path.exists():
                return snapshot.existed
            if not snapshot.path.is_file():
                return True
            return not snapshot.existed or snapshot.path.read_bytes() != snapshot.content
        except OSError:
            return True

    def publish(self, output: Path, content: bytes) -> bool:
        destination = require_windows_interop_path(output)
        if destination.is_file() and destination.read_bytes() == content:
            return False
        atomic_write_bytes(destination, content)
        return True

    def rollback(
        self,
        snapshot: SubtitleOutputSnapshot,
        *,
        project_dir: Path,
        document_id: str,
        error: BaseException,
    ) -> None:
        output = snapshot.path
        archived: Path | None = None
        if output.exists():
            archived = content_addressed_child_path(
                project_dir / "archive" / "subtitle-publications",
                f"{document_id}:{new_id()}",
                namespace="sub-fail",
                suffix=output.suffix or ".srt",
            )
            try:
                self.move(output, archived)
            except OSError as archive_error:
                error.add_note(f"未能把失败的字幕发布移入归档：{archive_error}")
                archived = None
        if snapshot.existed:
            try:
                atomic_write_bytes(output, snapshot.content)
            except OSError as restore_error:
                error.add_note(f"字幕数据库已回滚，但旧 SRT 恢复失败：{restore_error}")
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
                self.move(output, fallback)
            except OSError as withdraw_error:
                error.add_note(f"字幕数据库已回滚，但未登记的 SRT 无法撤回：{withdraw_error}")
