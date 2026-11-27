from __future__ import annotations

from collections.abc import Iterable

from mediaflow.application.project_storage_ports import (
    AssetDocuments,
    ProjectMetadataDocuments,
    SequenceDocuments,
)
from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.application.timeline_clock import asset_in_timeline_clock
from mediaflow.application.timeline_diff import TimelineDiff
from mediaflow.application.timeline_entities import (
    clip_by_id,
    clip_index,
    track_by_id,
)
from mediaflow.application.timeline_ripple import RippleDeletePolicy
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.application.timeline_track_editing import ensure_linked_audio_track, insert_audio_track_after
from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind
from mediaflow.domain.timebase import (
    source_frame_at_timeline_offset,
    source_frames_for_timeline_frames,
    source_interval_for_timeline_interval,
)
from mediaflow.domain.timeline import (
    Clip,
    ClipAudio,
    TimelineState,
    Transition,
)
from mediaflow.domain.web_state import WebClipState


class TimelineClipEditing:
    def __init__(
        self,
        changes: TimelineChangeSession,
        assets: AssetDocuments,
        projects: ProjectMetadataDocuments,
        sequences: SequenceDocuments,
    ) -> None:
        self._changes = changes
        self.assets = assets
        self.projects = projects
        self.sequences = sequences

    def trim_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        source_in: int,
        duration: int,
    ) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={
                    "timeline_start": timeline_start,
                    "source_in": source_in,
                    "duration": duration,
                }
            )
            clips = {item.id: item for item in state.clips}
            state.transitions = [
                item for item in state.transitions if TimelineRules.transition_is_valid(item, clips)
            ]

        self._changes.commit_change("裁剪片段", mutate)
        return clip_by_id(self._changes.current, clip_id)

    def replace_clip_source(self, clip_id: str, asset_id: str) -> Clip:
        source = clip_by_id(self._changes.current, clip_id)
        track = track_by_id(self._changes.current, source.track_id)
        stored_asset = self.assets.get_asset(asset_id)
        if stored_asset.kind == AssetKind.WEB:
            raise ValueError("网页素材必须通过网页换版功能替换")
        asset = asset_in_timeline_clock(
            self.projects,
            self.sequences,
            stored_asset,
            self._changes.current.sequence,
        )
        if track.kind == TrackKind.AUDIO:
            media_kind = ClipMediaKind.AUDIO_ONLY
        elif track.kind == TrackKind.VIDEO:
            media_kind = (
                ClipMediaKind.LINKED_AV
                if source.media_kind == ClipMediaKind.LINKED_AV
                and asset.kind == AssetKind.VIDEO
                and asset.metadata.has_audio
                else ClipMediaKind.VIDEO_ONLY
            )
        else:
            raise ValueError("字幕轨道不能替换片段素材")
        TimelineRules.validate_clip_track(
            asset.kind,
            media_kind,
            track.kind,
            asset.metadata.has_audio,
        )

        source_in = source.source_in
        source_duration = asset.metadata.duration_frames
        if asset.kind == AssetKind.IMAGE:
            source_in = 0
        elif source_duration > 0:
            consumed = source_frames_for_timeline_frames(
                source.duration,
                source.speed_numerator,
                source.speed_denominator,
            )
            if source.speed_numerator > 0:
                source_in = min(source_in, max(0, source_duration - consumed))
            else:
                source_in = min(
                    source_duration - 1,
                    max(source_in, consumed - 1),
                )
        replacement = source.model_copy(
            update={
                "asset_id": asset.id,
                "source_in": source_in,
                "media_kind": media_kind,
                "transform_keyframes": [],
            }
        )
        maximum = replacement.maximum_timeline_duration(
            asset.kind,
            source_duration,
        )
        if maximum is not None and maximum < replacement.duration:
            if maximum <= 0:
                raise ValueError("替换素材没有可用的源帧")
            replacement = replacement.model_copy(update={"duration": maximum})

        def mutate(state: TimelineState) -> None:
            index = clip_index(state, clip_id)
            state.clips[index] = replacement
            state.web_states.pop(clip_id, None)
            if replacement.media_kind == ClipMediaKind.LINKED_AV:
                ensure_linked_audio_track(state, replacement.track_id)
            clips = {item.id: item for item in state.clips}
            state.transitions = [
                item for item in state.transitions if TimelineRules.transition_is_valid(item, clips)
            ]

        self._changes.commit_change("替换片段素材", mutate)
        return clip_by_id(self._changes.current, clip_id)

    def set_clip_audio(self, clip_id: str, audio: ClipAudio) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"audio": audio})

        self._changes.commit_change("调整片段音频", mutate)
        return clip_by_id(self._changes.current, clip_id)

    def set_clips_properties(
        self,
        clip_ids: Iterable[str],
        *,
        gain_db: float,
        pan: float,
        fade_in_frames: int,
        fade_out_frames: int,
        opacity: float,
    ) -> list[Clip]:
        selected_ids = list(dict.fromkeys(clip_ids))
        if len(selected_ids) < 2:
            raise ValueError("批量调整需要至少两个片段")
        selected = [clip_by_id(self._changes.current, clip_id) for clip_id in selected_ids]
        if fade_in_frames + fade_out_frames > min(clip.duration for clip in selected):
            raise ValueError("淡入与淡出总长度不能超过最短片段")
        audio = ClipAudio(
            gain_db=gain_db,
            pan=pan,
            fade_in_frames=fade_in_frames,
            fade_out_frames=fade_out_frames,
        )

        def mutate(state: TimelineState) -> None:
            for clip_id in selected_ids:
                index = clip_index(state, clip_id)
                clip = state.clips[index]
                state.clips[index] = clip.model_copy(
                    update={
                        "audio": audio,
                        "transform": clip.transform.model_copy(update={"opacity": opacity}),
                    }
                )

        self._changes.commit_change("批量调整片段", mutate)
        clips = {clip.id: clip for clip in self._changes.current.clips}
        return [clips[clip_id] for clip_id in selected_ids]

    def set_web_clip_state(
        self,
        web_state: WebClipState,
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        current = self._changes.current.web_states.get(web_state.clip_id)
        if current is None:
            raise KeyError(web_state.clip_id)
        if expected_revision is not None and current.revision != expected_revision:
            raise RuntimeError(
                f"Editable media revision conflict: expected {expected_revision}, current {current.revision}"
            )

        def mutate(state: TimelineState) -> None:
            state.web_states[web_state.clip_id] = web_state

        self._changes.commit_change("编辑网页图层", mutate)
        return self._changes.current.web_states[web_state.clip_id]

    def set_clip_speed(
        self,
        clip_id: str,
        *,
        speed_numerator: int,
        speed_denominator: int,
        pitch_compensation: bool,
    ) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = clip_index(state, clip_id)
            source = state.clips[index]
            source_in = source.source_in
            if (source.speed_numerator > 0) != (speed_numerator > 0):
                interval = source_interval_for_timeline_interval(
                    source.source_in,
                    0,
                    source.duration,
                    source.speed_numerator,
                    source.speed_denominator,
                    freeze_source_frame=source.freeze_source_frame,
                )
                source_in = interval[1] - 1 if speed_numerator < 0 else interval[0]
            state.clips[index] = source.model_copy(
                update={
                    "source_in": source_in,
                    "speed_numerator": speed_numerator,
                    "speed_denominator": speed_denominator,
                    "pitch_compensation": pitch_compensation,
                }
            )

        self._changes.commit_change("调整速度", mutate)
        return clip_by_id(self._changes.current, clip_id)

    def split_clip(self, clip_id: str, split_frame: int) -> tuple[Clip, Clip]:
        source = clip_by_id(self._changes.current, clip_id)
        if not source.timeline_start < split_frame < source.timeline_end:
            raise ValueError("Split frame must be inside the clip")
        left_duration = split_frame - source.timeline_start
        right_duration = source.duration - left_duration
        right_source_in = source_frame_at_timeline_offset(
            source.source_in,
            left_duration,
            source.speed_numerator,
            source.speed_denominator,
            freeze_source_frame=source.freeze_source_frame,
        )
        if right_source_in < 0:
            raise ValueError("Reverse split exceeds the available source range")
        left = source.model_copy(update={"duration": left_duration})
        right = source.model_copy(
            update={
                "id": Clip(
                    track_id=source.track_id,
                    asset_id=source.asset_id,
                    timeline_start=split_frame,
                    source_in=right_source_in,
                    duration=right_duration,
                    media_kind=source.media_kind,
                    speed_numerator=source.speed_numerator,
                    speed_denominator=source.speed_denominator,
                    pitch_compensation=source.pitch_compensation,
                    transform=source.transform,
                    audio=source.audio,
                ).id,
                "timeline_start": split_frame,
                "source_in": right_source_in,
                "duration": right_duration,
            }
        )

        def mutate(state: TimelineState) -> None:
            index = clip_index(state, clip_id)
            state.clips[index] = left
            state.clips.insert(index + 1, right)
            if clip_id in state.web_states:
                state.web_states[right.id] = state.web_states[clip_id].model_copy(
                    update={"clip_id": right.id, "revision": 0}
                )
            rebound: list[Transition] = []
            clips_by_id = {item.id: item for item in state.clips}
            for transition in state.transitions:
                candidate = (
                    transition.model_copy(update={"left_clip_id": right.id})
                    if transition.left_clip_id == clip_id
                    else transition
                )
                if TimelineRules.transition_is_valid(candidate, clips_by_id):
                    rebound.append(candidate)
            state.transitions = rebound

        self._changes.commit_change("分割片段", mutate)
        return clip_by_id(self._changes.current, left.id), clip_by_id(self._changes.current, right.id)

    def detach_clip_audio(self, clip_id: str) -> tuple[Clip, Clip]:
        source = clip_by_id(self._changes.current, clip_id)
        asset = self.assets.get_asset(source.asset_id)
        if source.media_kind != ClipMediaKind.LINKED_AV or not asset.metadata.has_audio:
            raise ValueError("所选片段没有可解除绑定的音频")
        source_track = track_by_id(self._changes.current, source.track_id)
        if source_track.kind != TrackKind.VIDEO:
            raise ValueError("只有视频轨道上的绑定视音频才能解除绑定")
        detached = Clip(
            track_id="",
            asset_id=source.asset_id,
            timeline_start=source.timeline_start,
            source_in=source.source_in,
            duration=source.duration,
            media_kind=ClipMediaKind.AUDIO_ONLY,
            speed_numerator=source.speed_numerator,
            speed_denominator=source.speed_denominator,
            pitch_compensation=source.pitch_compensation,
            audio=source.audio,
        )

        def mutate(state: TimelineState) -> None:
            video_track = next(track for track in state.tracks if track.id == source.track_id)
            audio_track_id = video_track.linked_audio_track_id
            audio_track = next(
                (track for track in state.tracks if track.id == audio_track_id),
                None,
            )
            if audio_track is None or not TimelineRules.interval_available(
                state,
                audio_track.id,
                source.timeline_start,
                source.duration,
            ):
                audio_track = insert_audio_track_after(state, video_track)
            source_index = clip_index(state, source.id)
            state.clips[source_index] = source.model_copy(update={"media_kind": ClipMediaKind.VIDEO_ONLY})
            state.clips.append(detached.model_copy(update={"track_id": audio_track.id}))
            if source.id in state.web_states:
                state.web_states[detached.id] = state.web_states[source.id].model_copy(
                    update={"clip_id": detached.id, "revision": 0}
                )

        self._changes.commit_change("解除视音频绑定", mutate)
        return clip_by_id(self._changes.current, source.id), clip_by_id(self._changes.current, detached.id)

    def delete_clip(self, clip_id: str, *, ripple: bool = False) -> None:
        self.delete_clips([clip_id], ripple=ripple)

    def delete_clips(self, clip_ids: Iterable[str], *, ripple: bool = False) -> None:
        selected_ids = set(clip_ids)
        if not selected_ids:
            return
        sources = [clip_by_id(self._changes.current, clip_id) for clip_id in selected_ids]
        source_track_ids = {clip.track_id for clip in sources}

        def mutate(state: TimelineState) -> None:
            state.clips = [clip for clip in state.clips if clip.id not in selected_ids]
            state.web_states = {
                clip_id: web_state
                for clip_id, web_state in state.web_states.items()
                if clip_id not in selected_ids
            }
            state.transitions = [
                transition
                for transition in state.transitions
                if transition.left_clip_id not in selected_ids
                and transition.right_clip_id not in selected_ids
            ]
            if ripple:
                TimelineDiff.apply_ripple(
                    self._changes.current,
                    state,
                    source_track_ids=source_track_ids,
                )

        label = "波纹删除多个片段" if ripple else "删除多个片段"
        if len(selected_ids) == 1:
            label = "波纹删除" if ripple else "删除片段"
        self._changes.commit_change(label, mutate)

    def preview_ripple_delete_intervals(
        self,
        intervals: Iterable[tuple[int, int]],
    ) -> TimelineState:
        preview = self._changes.current.model_copy(deep=True)
        normalized = sorted(
            {(int(start), int(end)) for start, end in intervals},
            reverse=True,
        )
        if not normalized:
            raise ValueError("删除计划必须至少包含一个时间范围")
        for start, end in normalized:
            RippleDeletePolicy.apply(preview, start, end)
        return preview

    def apply_ripple_delete_intervals(
        self,
        intervals: Iterable[tuple[int, int]],
        *,
        label: str = "按转录计划波纹剪辑",
    ) -> None:
        normalized = sorted(
            {(int(start), int(end)) for start, end in intervals},
            reverse=True,
        )
        if not normalized:
            raise ValueError("删除计划必须至少包含一个时间范围")

        def mutate(state: TimelineState) -> None:
            for start, end in normalized:
                RippleDeletePolicy.apply(state, start, end)

        self._changes.commit_change(label, mutate)
