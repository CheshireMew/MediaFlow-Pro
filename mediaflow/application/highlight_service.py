from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.models import HighlightCandidate
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.project_repository import ProjectRepository

HighlightProgress = Callable[[float, str], None]


class HighlightService:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    def analyze_document(
        self,
        document_id: str,
        *,
        provider: LlmProviderSettings,
        maximum_candidates: int = 12,
        progress: HighlightProgress | None = None,
    ) -> list[HighlightCandidate]:
        document = self.repository.get_subtitle_document(document_id)
        segments = self.repository.list_subtitle_segments(document_id)
        if not segments:
            raise ValueError("Subtitle document is empty")
        if progress:
            progress(5.0, "highlight_analyzing")
        response = OpenAIJsonClient(provider).complete_json(
            system=(
                "Select self-contained highlight ranges from a transcript. "
                "Use existing segment ids and keep ranges in chronological order. "
                'Return only JSON: {"candidates":[{"start_id":"...",'
                '"end_id":"...","title":"...","reason":"...",'
                '"score":0.0}]}'
            ),
            payload={
                "maximum_candidates": maximum_candidates,
                "segments": [{"id": item.id, "text": item.text} for item in segments],
            },
        )
        raw_candidates = response.get("candidates")
        if not isinstance(raw_candidates, list):
            raise RuntimeError("Highlight response is missing candidates")
        by_id = {segment.id: segment for segment in segments}
        index_by_id = {segment.id: index for index, segment in enumerate(segments)}
        project = self.repository.get_project()
        candidates: list[HighlightCandidate] = []
        for raw in raw_candidates[:maximum_candidates]:
            if not isinstance(raw, dict):
                continue
            start_id = str(raw.get("start_id") or "")
            end_id = str(raw.get("end_id") or "")
            if start_id not in by_id or end_id not in by_id:
                raise RuntimeError("Highlight response references an unknown segment")
            if index_by_id[end_id] < index_by_id[start_id]:
                raise RuntimeError("Highlight response contains a reversed range")
            start = by_id[start_id]
            end = by_id[end_id]
            candidates.append(
                HighlightCandidate(
                    project_id=project.id,
                    asset_id=document.asset_id,
                    start_frame=start.start_frame,
                    end_frame=end.end_frame,
                    title=str(raw.get("title") or "精彩片段").strip(),
                    reason=str(raw.get("reason") or "").strip(),
                    score=max(0.0, min(1.0, float(raw.get("score") or 0.0))),
                )
            )
        if not candidates:
            raise RuntimeError("Highlight analysis returned no valid candidates")
        self.repository.save_highlights(candidates)
        if progress:
            progress(100.0, "highlight_completed")
        return candidates

    def create_short_sequence(self, candidate_id: str, *, name: str | None = None):
        try:
            candidate = next(item for item in self.repository.list_highlights() if item.id == candidate_id)
        except StopIteration as error:
            raise KeyError(candidate_id) from error
        sequence = self.repository.create_short_sequence(name or candidate.title)
        editor = TimelineEditor(self.repository, sequence.id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        project = self.repository.get_project()
        main_profile = self.repository.get_sequence(project.main_sequence_id).profile
        short_profile = sequence.profile
        source_in = seconds_to_frames(
            frames_to_seconds(
                candidate.start_frame,
                main_profile.fps_numerator,
                main_profile.fps_denominator,
            ),
            short_profile.fps_numerator,
            short_profile.fps_denominator,
        )
        source_end = seconds_to_frames(
            frames_to_seconds(
                candidate.end_frame,
                main_profile.fps_numerator,
                main_profile.fps_denominator,
            ),
            short_profile.fps_numerator,
            short_profile.fps_denominator,
        )
        editor.add_clip(
            track_id=video_track.id,
            asset_id=candidate.asset_id,
            timeline_start=0,
            source_in=source_in,
            duration=max(1, source_end - source_in),
        )
        return sequence
