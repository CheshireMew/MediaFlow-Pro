from __future__ import annotations

from collections.abc import Callable, Iterable

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.application.timeline_clock import asset_in_timeline_clock
from mediaflow.application.timeline_diff import TimelineDiff
from mediaflow.application.timeline_marker_editing import TimelineMarkerRangeEditing
from mediaflow.application.timeline_ripple import RippleDeletePolicy
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.application.timeline_snapping import snap_frame
from mediaflow.application.timeline_structure_editing import TimelineStructureEditing
from mediaflow.application.timeline_track_editing import TimelineTrackEditing
from mediaflow.application.timeline_visual_editing import TimelineVisualEditing
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    TrackKind,
    VisualEffectKind,
)
from mediaflow.domain.model_base import new_id
from mediaflow.domain.timebase import (
    source_frame_at_timeline_offset,
    source_frames_for_timeline_frames,
    source_interval_for_timeline_interval,
)
from mediaflow.domain.timeline import (
    Clip,
    ClipAddRequest,
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    FreezeClipAddRequest,
    TimelineMarker,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
    default_clip_media_kind,
)
from mediaflow.domain.visual_effects import ClipVisualEffect
from mediaflow.domain.web_state import WebClipState


class TimelineEditor(
    TimelineTrackEditing,
    TimelineStructureEditing,
    TimelineMarkerRangeEditing,
):
    """Frame-accurate editing commands with a persisted command stack."""

    def __init__(
        self,
        repository: TimelineEditorDocuments,
        sequence_id: str,
        history: ProjectEditHistory | None = None,
    ):
        self.repository = repository
        self.sequence_id = sequence_id
        self._changes = TimelineChangeSession(repository, sequence_id, history)
        self.history = self._changes.history
        self._visual_editing = TimelineVisualEditing(
            repository,
            lambda: self.state,
            self._commit,
        )

    @property
    def state(self) -> TimelineState:
        return self._changes.snapshot

    @property
    def duration_frames(self) -> int:
        """Expose the cached timeline duration through the shared editor surface."""

        return self._changes.snapshot.duration_frames

    @property
    def can_undo(self) -> bool:
        return self._changes.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._changes.history.can_redo

    def reload(self) -> TimelineState:
        return self._changes.reload()

    def restore_snapshot(
        self,
        source: TimelineState,
        destination: TimelineState,
    ) -> TimelineState:
        """Apply a previously captured change while preserving unrelated edits."""
        return self._changes.restore_snapshot(source, destination)

    def add_clip(
        self,
        *,
        track_id: str,
        asset_id: str,
        timeline_start: int,
        source_in: int,
        duration: int,
        speed_numerator: int = 1,
        speed_denominator: int = 1,
        pitch_compensation: bool = True,
    ) -> Clip:
        return self.add_clips(
            [
                ClipAddRequest(
                    track_id=track_id,
                    asset_id=asset_id,
                    timeline_start=timeline_start,
                    source_in=source_in,
                    duration=duration,
                    speed_numerator=speed_numerator,
                    speed_denominator=speed_denominator,
                    pitch_compensation=pitch_compensation,
                )
            ]
        )[0]

    def add_clips(self, specifications: Iterable[ClipAddRequest]) -> list[Clip]:
        requested = list(specifications)
        if not requested:
            raise ValueError("At least one clip is required")
        prepared: list[tuple[Clip, AssetKind, str | None]] = []
        for specification in requested:
            track_id = specification.track_id
            asset_id = specification.asset_id
            track = self._track(track_id)
            asset = self.repository.assets.get_asset(asset_id)
            media_kind = default_clip_media_kind(
                asset.kind,
                has_audio=asset.metadata.has_audio,
            )
            TimelineRules.validate_clip_track(
                asset.kind,
                media_kind,
                track.kind,
                asset.metadata.has_audio,
            )
            clip = Clip(
                track_id=track_id,
                asset_id=asset_id,
                timeline_start=specification.timeline_start,
                source_in=specification.source_in,
                duration=specification.duration,
                media_kind=media_kind,
                speed_numerator=specification.speed_numerator,
                speed_denominator=specification.speed_denominator,
                pitch_compensation=specification.pitch_compensation,
            )
            web_source_hash = (
                self.repository.web.get_web_asset_spec(asset.id).source_hash
                if asset.kind == AssetKind.WEB
                else None
            )
            prepared.append((clip, asset.kind, web_source_hash))

        def mutate(state: TimelineState) -> None:
            for clip, asset_kind, web_source_hash in prepared:
                if clip.media_kind == ClipMediaKind.LINKED_AV:
                    self._ensure_linked_audio_track(state, clip.track_id)
                state.clips.append(clip)
                if asset_kind == AssetKind.WEB:
                    assert web_source_hash is not None
                    state.web_states[clip.id] = WebClipState(
                        clip_id=clip.id,
                        source_hash=web_source_hash,
                    )

        self._commit(
            "批量添加片段" if len(prepared) > 1 else "添加片段",
            mutate,
        )
        return [clip for clip, _, _ in prepared]

    def add_freeze_clip(self, request: FreezeClipAddRequest) -> Clip:
        track = self._track(request.track_id)
        asset = self.repository.assets.get_asset(request.asset_id)
        if track.kind != TrackKind.VIDEO:
            raise ValueError("Freeze clips require a video track")
        if asset.kind not in {AssetKind.VIDEO, AssetKind.WEB}:
            raise ValueError("Freeze clips require a video or rendered web source")
        clip = Clip(
            track_id=request.track_id,
            asset_id=request.asset_id,
            timeline_start=request.timeline_start,
            source_in=request.source_frame,
            duration=request.duration,
            media_kind=ClipMediaKind.VIDEO_ONLY,
            freeze_source_frame=request.source_frame,
        )
        clip.validate_source_range(asset.kind, asset.metadata.duration_frames)
        web_source_hash = (
            self.repository.web.get_web_asset_spec(asset.id).source_hash
            if asset.kind == AssetKind.WEB
            else None
        )

        def mutate(state: TimelineState) -> None:
            state.clips.append(clip)
            if web_source_hash is not None:
                state.web_states[clip.id] = WebClipState(
                    clip_id=clip.id,
                    source_hash=web_source_hash,
                )

        self._commit("添加定格片段", mutate)
        return self._clip(clip.id)

    def replace_contents(
        self,
        destination: TimelineState,
        *,
        label: str = "导入可携带时间线",
    ) -> TimelineState:
        if destination.sequence.id != self.sequence_id:
            raise ValueError("Timeline replacement belongs to another sequence")
        if destination.sequence.profile != self._changes.current.sequence.profile:
            raise ValueError("Set the sequence profile before replacing timeline contents")

        def mutate(state: TimelineState) -> None:
            state.tracks = list(destination.tracks)
            state.clips = list(destination.clips)
            state.compounds = list(destination.compounds)
            state.transitions = list(destination.transitions)
            state.markers = list(destination.markers)
            state.ranges = list(destination.ranges)
            state.web_states = dict(destination.web_states)

        self._commit(label, mutate, allow_locked_changes=True)
        return self.state

    def move_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        track_id: str | None = None,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> Clip:
        source = self._clip(clip_id)
        return self._move_clips(
            [clip_id],
            primary_clip_id=clip_id,
            timeline_start=timeline_start,
            track_id=track_id or source.track_id,
            snap_targets=snap_targets,
            snap_tolerance_frames=snap_tolerance_frames,
            label="移动片段",
        )[0]

    def preview_move_clips(
        self,
        clip_ids: Iterable[str],
        *,
        primary_clip_id: str,
        timeline_start: int,
        track_id: str,
    ) -> list[Clip]:
        selected_ids = list(dict.fromkeys(clip_ids))
        candidate = TimelineChangeSession.copy_state(self._changes.current)
        self._move_clips_in_state(
            candidate,
            selected_ids,
            primary_clip_id=primary_clip_id,
            timeline_start=timeline_start,
            track_id=track_id,
        )
        self._changes.validate_preview(candidate)
        clips = {clip.id: clip for clip in candidate.clips}
        return [clips[clip_id] for clip_id in selected_ids]

    def move_clips(
        self,
        clip_ids: Iterable[str],
        *,
        primary_clip_id: str,
        timeline_start: int,
        track_id: str,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> list[Clip]:
        return self._move_clips(
            clip_ids,
            primary_clip_id=primary_clip_id,
            timeline_start=timeline_start,
            track_id=track_id,
            snap_targets=snap_targets,
            snap_tolerance_frames=snap_tolerance_frames,
            label="移动多个片段",
        )

    def _move_clips(
        self,
        clip_ids: Iterable[str],
        *,
        primary_clip_id: str,
        timeline_start: int,
        track_id: str,
        snap_targets: Iterable[int],
        snap_tolerance_frames: int,
        label: str,
    ) -> list[Clip]:
        selected_ids = list(dict.fromkeys(clip_ids))
        if primary_clip_id not in selected_ids:
            raise ValueError("Primary clip must be part of the selection")
        snapped_start = self.snap_frame(
            timeline_start,
            snap_targets,
            snap_tolerance_frames,
        )

        def mutate(state: TimelineState) -> None:
            self._move_clips_in_state(
                state,
                selected_ids,
                primary_clip_id=primary_clip_id,
                timeline_start=snapped_start,
                track_id=track_id,
            )

        self._changes.commit_clip_change(label, set(selected_ids), mutate)
        return [self._clip(clip_id) for clip_id in selected_ids]

    def _move_clips_in_state(
        self,
        state: TimelineState,
        selected_ids: list[str],
        *,
        primary_clip_id: str,
        timeline_start: int,
        track_id: str,
    ) -> None:
        if primary_clip_id not in selected_ids:
            raise ValueError("Primary clip must be part of the selection")
        clips_by_id = {clip.id: clip for clip in state.clips}
        try:
            selected = [clips_by_id[clip_id] for clip_id in selected_ids]
            primary = clips_by_id[primary_clip_id]
        except KeyError as error:
            raise KeyError(str(error.args[0])) from error
        tracks = sorted(state.tracks, key=lambda item: item.position)
        track_positions = {item.id: index for index, item in enumerate(tracks)}
        if track_id not in track_positions:
            raise KeyError(track_id)
        frame_delta = timeline_start - primary.timeline_start
        track_delta = track_positions[track_id] - track_positions[primary.track_id]
        updates: dict[str, tuple[str, int]] = {}
        for clip in selected:
            destination_position = track_positions[clip.track_id] + track_delta
            if not 0 <= destination_position < len(tracks):
                raise ValueError("Selected clips cannot move outside the timeline tracks")
            destination = tracks[destination_position]
            asset = self.repository.assets.get_asset(clip.asset_id)
            TimelineRules.validate_clip_track(
                asset.kind,
                clip.media_kind,
                destination.kind,
                asset.metadata.has_audio,
            )
            next_start = clip.timeline_start + frame_delta
            if next_start < 0:
                raise ValueError("Selected clips cannot move before the timeline start")
            updates[clip.id] = (destination.id, next_start)
        for clip in selected:
            destination_id = updates[clip.id][0]
            if clip.media_kind == ClipMediaKind.LINKED_AV:
                self._ensure_linked_audio_track(state, destination_id)
        state.clips[:] = [
            clip.model_copy(
                update={
                    "track_id": updates[clip.id][0],
                    "timeline_start": updates[clip.id][1],
                }
            )
            if clip.id in updates
            else clip
            for clip in state.clips
        ]
        if state.transitions:
            moved_clips = {item.id: item for item in state.clips}
            state.transitions[:] = [
                item
                for item in state.transitions
                if TimelineRules.transition_is_valid(item, moved_clips)
            ]

    def copy_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        track_id: str | None = None,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> Clip:
        source = self._clip(clip_id)
        destination = track_id or source.track_id
        track = self._track(destination)
        asset = self.repository.assets.get_asset(source.asset_id)
        TimelineRules.validate_clip_track(
            asset.kind,
            source.media_kind,
            track.kind,
            asset.metadata.has_audio,
        )
        snapped = self.snap_frame(timeline_start, snap_targets, snap_tolerance_frames)
        copied = source.model_copy(
            update={"id": new_id(), "track_id": destination, "timeline_start": snapped}
        )

        def mutate(state: TimelineState) -> None:
            if copied.media_kind == ClipMediaKind.LINKED_AV:
                self._ensure_linked_audio_track(state, destination)
            state.clips.append(copied)
            if clip_id in state.web_states:
                state.web_states[copied.id] = state.web_states[clip_id].model_copy(
                    update={"clip_id": copied.id, "revision": 0}
                )

        self._commit("创建片段副本", mutate)
        return self._clip(copied.id)

    def trim_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        source_in: int,
        duration: int,
    ) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
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

        self._commit("裁剪片段", mutate)
        return self._clip(clip_id)

    def replace_clip_source(self, clip_id: str, asset_id: str) -> Clip:
        source = self._clip(clip_id)
        track = self._track(source.track_id)
        stored_asset = self.repository.assets.get_asset(asset_id)
        if stored_asset.kind == AssetKind.WEB:
            raise ValueError("网页素材必须通过网页换版功能替换")
        asset = asset_in_timeline_clock(
            self.repository.projects,
            self.repository.sequences,
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
            index = self._clip_index(state, clip_id)
            state.clips[index] = replacement
            state.web_states.pop(clip_id, None)
            if replacement.media_kind == ClipMediaKind.LINKED_AV:
                self._ensure_linked_audio_track(state, replacement.track_id)
            clips = {item.id: item for item in state.clips}
            state.transitions = [
                item for item in state.transitions if TimelineRules.transition_is_valid(item, clips)
            ]

        self._commit("替换片段素材", mutate)
        return self._clip(clip_id)

    def set_clip_transform(self, clip_id: str, transform: ClipTransform) -> Clip:
        return self._visual_editing.set_transform(clip_id, transform)

    def add_clip_visual_effect(
        self,
        clip_id: str,
        kind: VisualEffectKind,
        *,
        resource_asset_id: str | None = None,
    ) -> ClipVisualEffect:
        return self._visual_editing.add_effect(
            clip_id,
            kind,
            resource_asset_id=resource_asset_id,
        )

    def update_clip_visual_effect(
        self,
        clip_id: str,
        effect_id: str,
        *,
        enabled: bool,
        parameters: dict[str, float],
    ) -> ClipVisualEffect:
        return self._visual_editing.update_effect(
            clip_id,
            effect_id,
            enabled=enabled,
            parameters=parameters,
        )

    def move_clip_visual_effect(
        self,
        clip_id: str,
        effect_id: str,
        position: int,
    ) -> ClipVisualEffect:
        return self._visual_editing.move_effect(clip_id, effect_id, position)

    def remove_clip_visual_effect(self, clip_id: str, effect_id: str) -> None:
        self._visual_editing.remove_effect(clip_id, effect_id)

    def set_clip_transform_keyframes(
        self,
        clip_id: str,
        keyframes: list[ClipTransformKeyframe],
        *,
        expected_clip: Clip | None = None,
    ) -> Clip:
        return self._visual_editing.set_transform_keyframes(
            clip_id,
            keyframes,
            expected_clip=expected_clip,
        )

    def set_clip_audio(self, clip_id: str, audio: ClipAudio) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"audio": audio})

        self._commit("调整片段音频", mutate)
        return self._clip(clip_id)

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
        selected = [self._clip(clip_id) for clip_id in selected_ids]
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
                index = self._clip_index(state, clip_id)
                clip = state.clips[index]
                state.clips[index] = clip.model_copy(
                    update={
                        "audio": audio,
                        "transform": clip.transform.model_copy(update={"opacity": opacity}),
                    }
                )

        self._commit("批量调整片段", mutate)
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

        self._commit("编辑网页图层", mutate)
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
            index = self._clip_index(state, clip_id)
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

        self._commit("调整速度", mutate)
        return self._clip(clip_id)

    def split_clip(self, clip_id: str, split_frame: int) -> tuple[Clip, Clip]:
        source = self._clip(clip_id)
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
            index = self._clip_index(state, clip_id)
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

        self._commit("分割片段", mutate)
        return self._clip(left.id), self._clip(right.id)

    def detach_clip_audio(self, clip_id: str) -> tuple[Clip, Clip]:
        source = self._clip(clip_id)
        asset = self.repository.assets.get_asset(source.asset_id)
        if source.media_kind != ClipMediaKind.LINKED_AV or not asset.metadata.has_audio:
            raise ValueError("所选片段没有可解除绑定的音频")
        source_track = self._track(source.track_id)
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
                audio_track = self._insert_audio_track_after(state, video_track)
            source_index = self._clip_index(state, source.id)
            state.clips[source_index] = source.model_copy(update={"media_kind": ClipMediaKind.VIDEO_ONLY})
            state.clips.append(detached.model_copy(update={"track_id": audio_track.id}))
            if source.id in state.web_states:
                state.web_states[detached.id] = state.web_states[source.id].model_copy(
                    update={"clip_id": detached.id, "revision": 0}
                )

        self._commit("解除视音频绑定", mutate)
        return self._clip(source.id), self._clip(detached.id)

    def delete_clip(self, clip_id: str, *, ripple: bool = False) -> None:
        self.delete_clips([clip_id], ripple=ripple)

    def delete_clips(self, clip_ids: Iterable[str], *, ripple: bool = False) -> None:
        selected_ids = set(clip_ids)
        if not selected_ids:
            return
        sources = [self._clip(clip_id) for clip_id in selected_ids]
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
        self._commit(label, mutate)

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

        self._commit(label, mutate)

    def undo(self) -> TimelineState:
        return self._changes.undo()

    def redo(self) -> TimelineState:
        return self._changes.redo()

    snap_frame = staticmethod(snap_frame)

    def _commit(
        self,
        label: str,
        mutate: Callable[[TimelineState], None],
        *,
        allow_locked_changes: bool = False,
    ) -> None:
        self._changes.commit_change(
            label,
            mutate,
            allow_locked_changes=allow_locked_changes,
        )

    def _track(self, track_id: str) -> Track:
        try:
            return next(track for track in self._changes.current.tracks if track.id == track_id)
        except StopIteration as error:
            raise KeyError(track_id) from error

    def _clip(self, clip_id: str) -> Clip:
        try:
            return next(clip for clip in self._changes.current.clips if clip.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error

    def _transition(self, transition_id: str) -> Transition:
        try:
            return next(item for item in self._changes.current.transitions if item.id == transition_id)
        except StopIteration as error:
            raise KeyError(transition_id) from error

    def _marker(self, marker_id: str) -> TimelineMarker:
        try:
            return next(item for item in self._changes.current.markers if item.id == marker_id)
        except StopIteration as error:
            raise KeyError(marker_id) from error

    def _range(self, range_id: str) -> TimelineRange:
        try:
            return next(item for item in self._changes.current.ranges if item.id == range_id)
        except StopIteration as error:
            raise KeyError(range_id) from error

    @staticmethod
    def _clip_index(state: TimelineState, clip_id: str) -> int:
        try:
            return next(index for index, clip in enumerate(state.clips) if clip.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error

    def _insert_audio_track_after(self, state: TimelineState, video_track: Track) -> Track:
        count = sum(track.kind == TrackKind.AUDIO for track in state.tracks) + 1
        video_index = next(index for index, track in enumerate(state.tracks) if track.id == video_track.id)
        audio_track = Track(
            sequence_id=self.sequence_id,
            name=f"{TimelineRules.track_label(TrackKind.AUDIO)} {count}",
            kind=TrackKind.AUDIO,
            position=video_index + 1,
            audio_bus_id=video_track.audio_bus_id,
        )
        state.tracks.insert(video_index + 1, audio_track)
        TimelineRules.renumber_tracks(state)
        return audio_track

    def _ensure_linked_audio_track(self, state: TimelineState, video_track_id: str) -> Track:
        video_index = next(index for index, track in enumerate(state.tracks) if track.id == video_track_id)
        video_track = state.tracks[video_index]
        linked = next(
            (
                track
                for track in state.tracks
                if track.id == video_track.linked_audio_track_id and track.kind == TrackKind.AUDIO
            ),
            None,
        )
        if linked is not None:
            return linked
        linked = self._insert_audio_track_after(state, video_track)
        video_index = next(index for index, track in enumerate(state.tracks) if track.id == video_track_id)
        state.tracks[video_index] = state.tracks[video_index].model_copy(
            update={"linked_audio_track_id": linked.id}
        )
        return linked
