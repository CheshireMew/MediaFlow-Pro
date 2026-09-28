from __future__ import annotations

import shutil
from pathlib import Path

from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.model_base import now_ms
from mediaflow.file_digest import sha256_file


class VoiceoverTakeStorage:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir.resolve()
        self.root = self.project_dir / "sources" / "voiceover"

    def store(self, cue_id: str, take_id: str, source_value: str | Path) -> Path:
        source = Path(source_value).expanduser().resolve(strict=True)
        if not source.is_file() or source.is_symlink():
            raise ValueError("旁白 take 必须是普通音频文件")
        suffix = source.suffix.casefold() or ".wav"
        destination = self.root / cue_id / f"{take_id}{suffix}"
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = unique_temporary_sibling(destination, label="voiceover")
        try:
            shutil.copy2(source, staging)
            if source.stat().st_size != staging.stat().st_size:
                raise RuntimeError("旁白 take 复制后的文件大小不一致")
            if sha256_file(source) != sha256_file(staging):
                raise RuntimeError("旁白 take 复制后的哈希校验失败")
            staging.replace(destination)
            return destination
        except BaseException:
            self.archive_failed(staging, take_id)
            raise

    def archive_uncommitted(self, path: Path, take_id: str) -> Path | None:
        return self.archive_failed(path, take_id)

    def archive_failed(self, path: Path, take_id: str) -> Path | None:
        if not path.exists():
            return None
        archive = (
            self.project_dir
            / "archive"
            / "failed-voiceover-takes"
            / f"{take_id}-{now_ms()}{path.suffix}"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        path.replace(archive)
        return archive
