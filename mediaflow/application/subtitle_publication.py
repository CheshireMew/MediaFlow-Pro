from __future__ import annotations

from pathlib import Path

from mediaflow.application.ports import SubtitlePublicationDocuments
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile


class SubtitlePublicationService:
    """Materialize persisted subtitle documents as observable SRT artifacts."""

    def __init__(self, repository: SubtitlePublicationDocuments):
        self.repository = repository

    def write_document_srt(self, document_id: str, destination: str | Path | None = None) -> Path:
        document = self.repository.get_subtitle_document(document_id)
        segments = self.repository.list_subtitle_segments(document_id)
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        if destination is None:
            output_dir = (
                self.repository.project_dir
                / "generated"
                / "subtitles"
                / document.asset_id
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            destination = output_dir / f"{document.language}-{document.id[:8]}.srt"
        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        return SubtitleFile.write_srt(
            output,
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
