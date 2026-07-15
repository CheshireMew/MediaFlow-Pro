from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from mediaflow.domain.models import SubtitleDocument, SubtitleSegment
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames
from mediaflow.infrastructure.asr_engine import FasterWhisperEngine, FasterWhisperProcessEngine
from mediaflow.infrastructure.project_repository import ProjectRepository


class SubtitleService:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    def transcribe_asset(
        self,
        asset_id: str,
        engine: FasterWhisperEngine | FasterWhisperProcessEngine,
        *,
        language: str | None = None,
        progress=None,
    ) -> SubtitleDocument:
        asset = self.repository.get_asset(asset_id)
        source = self.repository.resolve_asset_path(asset)
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        result = engine.transcribe(source, language=language, progress=progress)
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language=result.language,
            is_source=True,
        )
        segments = [
            SubtitleSegment(
                document_id=document.id,
                start_frame=seconds_to_frames(
                    segment.start_seconds,
                    profile.fps_numerator,
                    profile.fps_denominator,
                ),
                end_frame=max(
                    1,
                    seconds_to_frames(
                        segment.end_seconds,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                ),
                text=segment.text,
                confidence=segment.confidence,
            )
            for segment in result.segments
        ]
        if not segments:
            raise RuntimeError("ASR completed without subtitle segments")
        self.repository.create_subtitle_document(document, segments)
        self.write_document_srt(document.id)
        return document

    def write_document_srt(self, document_id: str, destination: str | Path | None = None) -> Path:
        document = self.repository.get_subtitle_document(document_id)
        segments = self.repository.list_subtitle_segments(document_id)
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        if destination is None:
            output_dir = self.repository.project_dir / "generated" / "subtitles" / document.asset_id
            output_dir.mkdir(parents=True, exist_ok=True)
            destination = output_dir / f"{document.language}-{document.id[:8]}.srt"
        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for index, segment in enumerate(segments, start=1):
            start = frames_to_seconds(segment.start_frame, profile.fps_numerator, profile.fps_denominator)
            end = frames_to_seconds(segment.end_frame, profile.fps_numerator, profile.fps_denominator)
            lines.extend(
                [
                    str(index),
                    f"{self._srt_time(start)} --> {self._srt_time(end)}",
                    segment.text,
                    "",
                ]
            )
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text("\n".join(lines), encoding="utf-8-sig")
        temporary.replace(output)
        return output

    @staticmethod
    def _srt_time(value: Fraction) -> str:
        total_ms = round(float(value) * 1000)
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
