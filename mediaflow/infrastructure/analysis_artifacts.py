from __future__ import annotations

from pathlib import Path

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.model_base import new_id
from mediaflow.domain.storage_names import content_addressed_child_path


def write_download_analysis(path: Path, plan: DownloadPlan) -> Path:
    return atomic_write_text(path, plan.model_dump_json(indent=2))


def archive_failed_visual_analysis(
    sources: tuple[Path, ...],
    archive_root: Path,
    task_id: str,
) -> list[str]:
    errors: list[str] = []
    for source in sources:
        if not source.is_file():
            continue
        archive_path = content_addressed_child_path(
            archive_root,
            f"visual-analysis:{task_id}:{source.name}:{new_id()}",
            namespace="va",
            suffix=".json",
        )
        try:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            source.replace(archive_path)
        except OSError as error:
            errors.append(str(error))
    return errors
