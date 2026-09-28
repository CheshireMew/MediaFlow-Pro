from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from mediaflow.atomic_file import atomic_write_bytes
from mediaflow.domain.model_base import new_id
from mediaflow.domain.review import (
    ReviewMarkupShape,
    ReviewPackageResult,
    ReviewSnapshot,
    ReviewThread,
)
from mediaflow.file_digest import sha256_file

_PACKAGE_SCHEMA = "mediaflow-review-package/v1"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ReviewPackageService:
    """Own durable review screenshots and portable review-package interchange."""

    def __init__(self, repository, timeline_provider) -> None:
        self.repository = repository
        self.project_dir = repository.project_dir
        self.timeline_provider = timeline_provider

    def attach_snapshot(
        self,
        sequence_id: str,
        thread_id: str,
        *,
        frame: int,
        rendered_path: str | Path,
        rendered_sha256: str,
        width: int,
        height: int,
    ) -> ReviewSnapshot:
        source = Path(rendered_path).resolve(strict=True)
        actual_sha256 = sha256_file(source)
        if actual_sha256 != rendered_sha256:
            raise ValueError("审阅截图的内容哈希与渲染结果不一致")
        snapshot = ReviewSnapshot(
            frame=frame,
            relative_path=f"sources/review/{thread_id}/{new_id()}.png",
            sha256=actual_sha256,
            width=width,
            height=height,
        )
        destination = self.project_dir / Path(snapshot.relative_path)
        atomic_write_bytes(destination, source.read_bytes())
        self.timeline_provider(sequence_id).add_review_snapshot(thread_id, snapshot)
        return snapshot

    def set_markup(
        self,
        sequence_id: str,
        thread_id: str,
        snapshot_id: str,
        markup: Sequence[ReviewMarkupShape | Mapping[str, Any]],
    ) -> ReviewThread:
        normalized = [ReviewMarkupShape.model_validate(item) for item in markup]
        return self.timeline_provider(sequence_id).set_review_snapshot_markup(
            thread_id, snapshot_id, normalized
        )

    def export_package(
        self,
        sequence_id: str,
        destination: str | Path,
        *,
        thread_ids: list[str] | None = None,
        overwrite: bool = False,
    ) -> ReviewPackageResult:
        output = Path(destination).resolve()
        if output.exists() and not overwrite:
            raise FileExistsError(output)
        state = self.timeline_provider(sequence_id).state
        selected_ids = set(thread_ids or [])
        threads = [
            item
            for item in state.review_threads
            if not selected_ids or item.id in selected_ids
        ]
        if selected_ids - {item.id for item in threads}:
            raise KeyError(sorted(selected_ids - {item.id for item in threads})[0])
        attachments: list[dict[str, object]] = []
        payloads: dict[str, bytes] = {}
        for thread in threads:
            for snapshot in thread.snapshots:
                source = (self.project_dir / Path(snapshot.relative_path)).resolve(strict=True)
                if self.project_dir.resolve() not in source.parents:
                    raise ValueError("审阅截图不在项目目录中")
                payload = source.read_bytes()
                if _sha256_bytes(payload) != snapshot.sha256:
                    raise ValueError(f"审阅截图已损坏：{snapshot.id}")
                archive_path = f"attachments/{snapshot.id}.png"
                payloads[archive_path] = payload
                attachments.append(
                    {
                        "snapshot_id": snapshot.id,
                        "archive_path": archive_path,
                        "sha256": snapshot.sha256,
                        "byte_count": len(payload),
                    }
                )
        project = self.repository.projects.get_project()
        manifest = {
            "schema": _PACKAGE_SCHEMA,
            "project": {
                "id": project.id,
                "name": project.name,
                "content_revision": self.repository.known_content_revision,
            },
            "sequence": {
                "id": state.sequence.id,
                "name": state.sequence.name,
                "timeline_revision": state.sequence.timeline_revision,
                "profile": state.sequence.profile.model_dump(mode="json"),
            },
            "threads": [
                item.model_dump(mode="json", exclude_computed_fields=True)
                for item in threads
            ],
            "attachments": attachments,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            )
            for archive_path, payload in payloads.items():
                archive.writestr(archive_path, payload)
        package = buffer.getvalue()
        atomic_write_bytes(output, package)
        return ReviewPackageResult(
            path=str(output),
            sha256=_sha256_bytes(package),
            byte_count=len(package),
            thread_count=len(threads),
            snapshot_count=len(attachments),
        )

    def import_package(
        self,
        sequence_id: str,
        source: str | Path,
    ) -> ReviewPackageResult:
        package_path = Path(source).resolve(strict=True)
        package_payload = package_path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(package_payload), "r") as archive:
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, json.JSONDecodeError) as error:
                raise ValueError("审阅包缺少有效的 manifest.json") from error
            if manifest.get("schema") != _PACKAGE_SCHEMA:
                raise ValueError("不支持的审阅包版本")
            attachment_entries = manifest.get("attachments")
            thread_entries = manifest.get("threads")
            if not isinstance(attachment_entries, list) or not isinstance(thread_entries, list):
                raise ValueError("审阅包结构不完整")
            attachments: dict[str, bytes] = {}
            for item in attachment_entries:
                snapshot_id = str(item.get("snapshot_id", ""))
                archive_path = str(item.get("archive_path", ""))
                pure_path = PurePosixPath(archive_path)
                if (
                    not snapshot_id
                    or not archive_path.startswith("attachments/")
                    or pure_path.is_absolute()
                    or ".." in pure_path.parts
                ):
                    raise ValueError("审阅包附件路径无效")
                payload = archive.read(archive_path)
                if len(payload) != int(item.get("byte_count", -1)):
                    raise ValueError(f"审阅包附件长度不匹配：{snapshot_id}")
                if _sha256_bytes(payload) != str(item.get("sha256", "")):
                    raise ValueError(f"审阅包附件哈希不匹配：{snapshot_id}")
                attachments[snapshot_id] = payload

        state = self.timeline_provider(sequence_id).state
        imported: list[ReviewThread] = []
        for raw_thread in thread_entries:
            source_thread = ReviewThread.model_validate(raw_thread)
            if not 0 <= source_thread.start_frame < max(1, state.duration_frames):
                raise ValueError("审阅批注起始帧超出目标序列")
            if source_thread.end_frame is not None and source_thread.end_frame > state.duration_frames:
                raise ValueError("审阅批注结束帧超出目标序列")
            thread_id = new_id()
            snapshots: list[ReviewSnapshot] = []
            for source_snapshot in source_thread.snapshots:
                if source_snapshot.id not in attachments:
                    raise ValueError(f"审阅包缺少截图附件：{source_snapshot.id}")
                snapshot_id = new_id()
                relative_path = f"sources/review/{thread_id}/{snapshot_id}.png"
                atomic_write_bytes(
                    self.project_dir / Path(relative_path),
                    attachments[source_snapshot.id],
                )
                snapshots.append(
                    source_snapshot.model_copy(
                        update={"id": snapshot_id, "relative_path": relative_path}
                    )
                )
            imported.append(
                source_thread.model_copy(
                    update={
                        "id": thread_id,
                        "sequence_id": sequence_id,
                        "clip_id": (
                            source_thread.clip_id
                            if source_thread.clip_id
                            and any(item.id == source_thread.clip_id for item in state.clips)
                            else None
                        ),
                        "project_revision": self.repository.known_content_revision,
                        "sequence_timeline_revision": state.sequence.timeline_revision,
                        "snapshots": snapshots,
                    }
                )
            )
        accepted = self.timeline_provider(sequence_id).import_review_threads(imported)
        return ReviewPackageResult(
            path=str(package_path),
            sha256=_sha256_bytes(package_payload),
            byte_count=len(package_payload),
            thread_count=len(accepted),
            snapshot_count=sum(len(item.snapshots) for item in accepted),
            imported_thread_ids=[item.id for item in accepted],
        )
