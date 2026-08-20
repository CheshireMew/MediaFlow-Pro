from __future__ import annotations

from pathlib import Path

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.media_association import related_media_paths
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile


class LocalSubtitleFileStore:
    """Local-filesystem adapter for subtitle import and SRT publication."""

    def read(
        self,
        path: str | Path,
        *,
        fps_numerator: int,
        fps_denominator: int,
    ) -> list[SubtitleCue]:
        source = self.resolve_existing_file(path)
        content = SubtitleFile.decode(source.read_bytes())
        parser = (
            SubtitleFile.parse_ass
            if source.suffix.lower() in {".ass", ".ssa"}
            else SubtitleFile.parse_srt
        )
        return parser(
            content,
            fps_numerator=fps_numerator,
            fps_denominator=fps_denominator,
        )

    def write_srt(
        self,
        path: str | Path,
        cues: list[SubtitleCue],
        *,
        fps_numerator: int,
        fps_denominator: int,
    ) -> Path:
        output = Path(path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        content = SubtitleFile.dumps_srt(
            cues,
            fps_numerator=fps_numerator,
            fps_denominator=fps_denominator,
        )
        atomic_write_text(output, content, encoding="utf-8-sig")
        return output
    def resolve_existing_file(self, path: str | Path) -> Path:
        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise FileNotFoundError(source)
        return source

    def canonical_path(self, path: str | Path) -> Path:
        return Path(path).resolve()

    def related_media_candidates(self, subtitle_path: Path) -> list[Path]:
        return [candidate.resolve() for candidate in related_media_paths(subtitle_path)]

    def existing_related_media(self, subtitle_path: Path) -> list[Path]:
        matches: list[Path] = []
        for resolved in self.related_media_candidates(subtitle_path):
            if resolved.is_file() and resolved.stat().st_size > 0:
                matches.append(resolved)
        return matches
