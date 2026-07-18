from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.ports import HighlightServiceDocuments, JsonClientFactory
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.domain.timebase import reframe_frames
from mediaflow.domain.timeline import Clip

HighlightProgress = Callable[[float, str], None]


class HighlightService:
    DUPLICATE_OVERLAP_RATIO = 0.65

    def __init__(
        self,
        repository: HighlightServiceDocuments,
        client_factory: JsonClientFactory | None = None,
    ):
        self.repository = repository
        self.client_factory = client_factory

    def analyze_document(
        self,
        document_id: str,
        *,
        provider: LlmProviderSettings,
        maximum_candidates: int = 12,
        progress: HighlightProgress | None = None,
    ) -> list[HighlightCandidate]:
        document = self.repository.get_subtitle_document(document_id)
        media_asset_id = document.media_asset_id or document.asset_id
        media_asset = self.repository.get_asset(media_asset_id)
        if media_asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
            raise ValueError("字幕文档尚未关联视频或音频素材")
        segments = self.repository.list_subtitle_segments(document_id)
        if not segments:
            raise ValueError("Subtitle document is empty")
        if progress:
            progress(5.0, "highlight_analyzing")
        if self.client_factory is None:
            raise RuntimeError("Highlight analysis requires a JSON client factory")
        response = self.client_factory(provider).complete_json(
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
                    asset_id=media_asset_id,
                    document_id=document.id,
                    start_frame=start.start_frame,
                    end_frame=end.end_frame,
                    title=str(raw.get("title") or "精彩片段").strip(),
                    reason=str(raw.get("reason") or "").strip(),
                    score=max(0.0, min(1.0, float(raw.get("score") or 0.0))),
                )
            )
        if not candidates:
            raise RuntimeError("Highlight analysis returned no valid candidates")
        existing = self.repository.list_highlights(media_asset_id)
        additions = [
            candidate
            for candidate in candidates
            if not any(
                self._overlap_ratio(candidate, current) >= self.DUPLICATE_OVERLAP_RATIO
                for current in existing
            )
        ]
        if additions:
            self.repository.save_highlights(additions)
        if progress:
            progress(100.0, "highlight_completed")
        return [*existing, *additions]

    def add_manual_candidate(
        self,
        asset_id: str,
        *,
        start_frame: int,
        end_frame: int,
        title: str | None = None,
        document_id: str | None = None,
    ) -> HighlightCandidate:
        asset = self.repository.get_asset(asset_id)
        project = self.repository.get_project()
        candidates = self.repository.list_highlights(asset_id)
        candidate = HighlightCandidate(
            project_id=project.id,
            asset_id=asset.id,
            document_id=document_id,
            start_frame=int(start_frame),
            end_frame=int(end_frame),
            title=(title or f"手动片段 {len(candidates) + 1}").strip(),
            reason="手动创建",
            score=1.0,
            selected=True,
        )
        self._validate_asset_range(candidate)
        self.repository.save_highlights([candidate])
        return candidate

    def update_candidate(
        self,
        candidate_id: str,
        *,
        start_frame: int,
        end_frame: int,
        title: str,
    ) -> HighlightCandidate:
        candidate = HighlightCandidate.model_validate(
            self._candidate(candidate_id)
            .model_copy(
                update={
                    "start_frame": int(start_frame),
                    "end_frame": int(end_frame),
                    "title": title.strip(),
                }
            )
            .model_dump()
        )
        self._validate_asset_range(candidate)
        self.repository.save_highlights([candidate])
        if candidate.sequence_id:
            self._sync_sequence_clip(candidate)
        return candidate

    def set_selected(self, candidate_id: str, selected: bool) -> HighlightCandidate:
        candidate = self._candidate(candidate_id).model_copy(update={"selected": bool(selected)})
        self.repository.save_highlights([candidate])
        return candidate

    def delete_candidate(self, candidate_id: str) -> None:
        self._candidate(candidate_id)
        self.repository.delete_highlight(candidate_id)

    def create_short_sequence(self, candidate_id: str, *, name: str | None = None):
        with self.repository.transaction():
            return self._create_short_sequence(candidate_id, name=name)

    def _create_short_sequence(self, candidate_id: str, *, name: str | None = None):
        candidate = self._candidate(candidate_id)
        if self.repository.get_asset(candidate.asset_id).kind != AssetKind.VIDEO:
            raise ValueError("只有关联视频的高光候选可以创建短视频")
        if candidate.sequence_id:
            try:
                sequence = self.repository.get_sequence(candidate.sequence_id)
            except KeyError:
                candidate = candidate.model_copy(update={"sequence_id": None})
            else:
                self._sync_sequence_clip(candidate)
                return sequence
        sequence = self.repository.create_short_sequence(name or candidate.title)
        state = self.repository.load_timeline(sequence.id)
        video_track = next(track for track in state.tracks if track.kind == TrackKind.VIDEO)
        project = self.repository.get_project()
        main_profile = self.repository.get_sequence(project.main_sequence_id).profile
        short_profile = sequence.profile
        source_in = reframe_frames(candidate.start_frame, main_profile, short_profile)
        source_end = reframe_frames(candidate.end_frame, main_profile, short_profile)
        state.clips.append(
            Clip(
                track_id=video_track.id,
                asset_id=candidate.asset_id,
                timeline_start=0,
                source_in=source_in,
                duration=max(1, source_end - source_in),
            )
        )
        self.repository.save_timeline(state)
        if candidate.document_id:
            subtitle_track = next(track for track in state.tracks if track.kind == TrackKind.SUBTITLE)
            self.repository.place_subtitle_document(
                candidate.document_id,
                subtitle_track.id,
                follow_clips=True,
            )
        candidate = candidate.model_copy(update={"sequence_id": sequence.id})
        self.repository.save_highlights([candidate])
        return sequence

    def selected_candidates(self, asset_id: str | None = None) -> list[HighlightCandidate]:
        return [candidate for candidate in self.repository.list_highlights(asset_id) if candidate.selected]

    def _sync_sequence_clip(self, candidate: HighlightCandidate) -> None:
        if not candidate.sequence_id:
            return
        state = self.repository.load_timeline(candidate.sequence_id)
        clip = next(
            (
                item
                for item in state.clips
                if item.asset_id == candidate.asset_id and item.timeline_start == 0
            ),
            None,
        )
        if clip is None:
            return
        project = self.repository.get_project()
        source_profile = self.repository.get_sequence(project.main_sequence_id).profile
        destination_profile = state.sequence.profile
        source_in = reframe_frames(candidate.start_frame, source_profile, destination_profile)
        source_end = reframe_frames(candidate.end_frame, source_profile, destination_profile)
        updated = clip.model_copy(
            update={
                "timeline_start": 0,
                "source_in": source_in,
                "duration": max(1, source_end - source_in),
            }
        )
        state.clips = [updated if item.id == clip.id else item for item in state.clips]
        self.repository.save_timeline(state)

    def _validate_asset_range(self, candidate: HighlightCandidate) -> None:
        asset = self.repository.get_asset(candidate.asset_id)
        if asset.metadata.duration_frames > 0 and candidate.end_frame > asset.metadata.duration_frames:
            raise ValueError("高光候选区间超出了素材时长")

    def _candidate(self, candidate_id: str) -> HighlightCandidate:
        try:
            return next(item for item in self.repository.list_highlights() if item.id == candidate_id)
        except StopIteration as error:
            raise KeyError(candidate_id) from error

    @staticmethod
    def _overlap_ratio(left: HighlightCandidate, right: HighlightCandidate) -> float:
        intersection = max(
            0,
            min(left.end_frame, right.end_frame) - max(left.start_frame, right.start_frame),
        )
        shorter = min(
            left.end_frame - left.start_frame,
            right.end_frame - right.start_frame,
        )
        return intersection / shorter if shorter > 0 else 0.0
