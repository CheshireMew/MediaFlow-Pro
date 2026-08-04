from __future__ import annotations

from collections.abc import Callable, Iterable

from mediaflow.application.edit_history import (
    ProjectEditAction,
    ProjectEditCommand,
    ProjectEditHistory,
)
from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.application.timeline_clock import (
    asset_in_timeline_clock,
    project_frame_profile,
    reframe_timeline_clock,
)
from mediaflow.application.timeline_diff import TimelineDiff
from mediaflow.application.timeline_merge import TimelineMergePolicy
from mediaflow.application.timeline_ripple import RippleDeletePolicy
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.application.timeline_snapping import snap_frame
from mediaflow.application.timeline_validator import TimelineValidator
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.frame_clock import MainFrameClockSnapshot
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import ProjectProfile, SequenceInOut
from mediaflow.domain.timebase import source_frames_for_timeline_frames
from mediaflow.domain.timeline import (
    Clip,
    ClipAddRequest,
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    CompoundClip,
    TimelineMarker,
    TimelineMergeConflict,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
    default_clip_media_kind,
)
from mediaflow.domain.timeline_history import (
    TIMELINE_HISTORY_MODE,
    compact_timeline_change,
)
from mediaflow.domain.visual_effects import ClipVisualEffect, new_visual_effect
from mediaflow.domain.web_media import WebClipState


class TimelineEditor:
    """Frame-accurate editing commands with a persisted command stack."""

    def __init__(
        self,
        repository: TimelineEditorDocuments,
        sequence_id: str,
        history: ProjectEditHistory | None = None,
    ):
        self.repository = repository
        self.sequence_id = sequence_id
        self._state = repository.timeline.load_timeline(sequence_id)
        self._validator = TimelineValidator(repository)
        self.history = history or ProjectEditHistory()
        self._history_action_kind = f"timeline.restore:{self.sequence_id}"
        self.history.register_handler(
            self._history_action_kind,
            self._apply_history_action,
        )

    @property
    def state(self) -> TimelineState:
        return self._snapshot(self._state)

    @property
    def can_undo(self) -> bool:
        return self.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self.history.can_redo

    def reload(self) -> TimelineState:
        self._state = self.repository.timeline.load_timeline(self.sequence_id)
        return self.state

    def restore_snapshot(
        self,
        source: TimelineState,
        destination: TimelineState,
    ) -> TimelineState:
        """Apply a previously captured change while preserving unrelated edits."""
        if source.sequence.id != self.sequence_id or destination.sequence.id != self.sequence_id:
            raise ValueError("Timeline snapshot belongs to another sequence")
        self._state = self._apply_change(source, destination)
        return self.state

    def add_track(
        self,
        kind: TrackKind,
        name: str | None = None,
        *,
        audio_bus_id: str | None = None,
        position: int | None = None,
    ) -> Track:
        insert_position = len(self._state.tracks) if position is None else position
        if not 0 <= insert_position <= len(self._state.tracks):
            raise ValueError("Track position is outside the timeline")
        count = sum(track.kind == kind for track in self._state.tracks) + 1
        track = Track(
            sequence_id=self.sequence_id,
            name=name or f"{TimelineRules.track_label(kind)} {count}",
            kind=kind,
            position=insert_position,
            audio_bus_id=audio_bus_id,
        )

        def mutate(state: TimelineState) -> None:
            state.tracks.insert(insert_position, track)
            TimelineRules.renumber_tracks(state)

        self._commit("添加轨道", mutate)
        return track

    def set_track_state(
        self,
        track_id: str,
        *,
        enabled: bool,
        locked: bool,
        muted: bool,
        solo: bool,
        audio_bus_id: str | None = None,
    ) -> Track:
        source = self._track(track_id)
        if source.kind == TrackKind.SUBTITLE and audio_bus_id is not None:
            raise ValueError("Subtitle tracks cannot route to an audio bus")
        if audio_bus_id is not None:
            buses = {bus.id for bus in self.repository.audio.list_audio_buses(self.sequence_id)}
            if audio_bus_id not in buses:
                raise ValueError("Track audio bus does not belong to this sequence")

        def mutate(state: TimelineState) -> None:
            index = next(index for index, track in enumerate(state.tracks) if track.id == track_id)
            state.tracks[index] = state.tracks[index].model_copy(
                update={
                    "enabled": enabled,
                    "locked": locked,
                    "muted": muted,
                    "solo": solo,
                    "audio_bus_id": audio_bus_id,
                }
            )

        self._commit("调整轨道", mutate)
        return self._track(track_id)

    def set_primary_dialogue_track(self, track_id: str) -> Track:
        source = self._track(track_id)
        if source.kind != TrackKind.AUDIO:
            raise ValueError("主要对白只能指定到音频轨")

        def mutate(state: TimelineState) -> None:
            state.tracks = [
                track.model_copy(update={"primary_dialogue": track.id == track_id})
                if track.kind == TrackKind.AUDIO
                else track
                for track in state.tracks
            ]

        self._commit("指定主要对白轨", mutate)
        return self._track(track_id)

    def move_track(self, track_id: str, position: int) -> Track:
        if not 0 <= position < len(self._state.tracks):
            raise ValueError("Track position is outside the timeline")

        def mutate(state: TimelineState) -> None:
            source_index = next(index for index, track in enumerate(state.tracks) if track.id == track_id)
            track = state.tracks.pop(source_index)
            state.tracks.insert(position, track)
            state.tracks = [
                item.model_copy(update={"position": index}) for index, item in enumerate(state.tracks)
            ]

        self._commit("排序轨道", mutate)
        return self._track(track_id)

    def set_sequence_profile(self, profile: ProjectProfile) -> TimelineState:
        old_profile = self._state.sequence.profile
        if profile == old_profile and self._state.sequence.profile_confirmed:
            return self.state
        project = self.repository.catalog.get_project()
        is_main_sequence = self.sequence_id == project.main_sequence_id
        source_snapshot = (
            self.repository.timeline.capture_main_frame_clock(self.sequence_id)
            if is_main_sequence
            else None
        )
        source_state = (
            source_snapshot.timeline
            if source_snapshot is not None
            else self._state
        )
        if source_snapshot is not None:
            session_state = self._snapshot(self._state)
            session_state.sequence = session_state.sequence.model_copy(
                update={"timeline_revision": 0}
            )
            if session_state != source_snapshot.timeline:
                raise TimelineMergeConflict(
                    "main frame clock snapshot",
                    self.sequence_id,
                )

        change = reframe_timeline_clock(
            source_state,
            self.repository.catalog.list_assets(),
            profile,
            asset_source_profile=project_frame_profile(self.repository.catalog),
            invalidate_proxies=is_main_sequence,
        )
        if source_snapshot is not None:
            self._validator.validate(
                change.state,
                baseline=source_state,
                allow_locked_changes=True,
                assets={asset.id: asset for asset in change.assets},
            )
            destination_snapshot = self.repository.timeline.change_main_frame_clock(
                source_snapshot,
                change.state,
                list(change.assets),
                old_profile=old_profile,
            )
            self._state = self.repository.timeline.load_timeline(self.sequence_id)

            self.history.push(
                ProjectEditCommand(
                    label="修改序列配置",
                    undo_actions=[
                        ProjectEditAction(
                            kind=self._history_action_kind,
                            payload={
                                "mode": "frame_clock",
                                "source": destination_snapshot.model_dump(
                                    mode="json", exclude_computed_fields=True
                                ),
                                "destination": source_snapshot.model_dump(
                                    mode="json", exclude_computed_fields=True
                                ),
                            },
                        )
                    ],
                    redo_actions=[
                        ProjectEditAction(
                            kind=self._history_action_kind,
                            payload={
                                "mode": "frame_clock",
                                "source": source_snapshot.model_dump(
                                    mode="json", exclude_computed_fields=True
                                ),
                                "destination": destination_snapshot.model_dump(
                                    mode="json", exclude_computed_fields=True
                                ),
                            },
                        )
                    ],
                )
            )
            return self.state

        def mutate(state: TimelineState) -> None:
            state.sequence = change.state.sequence
            state.clips = list(change.state.clips)
            state.compounds = list(change.state.compounds)
            state.transitions = list(change.state.transitions)
            state.markers = list(change.state.markers)
            state.ranges = list(change.state.ranges)

        self._commit("修改序列配置", mutate, allow_locked_changes=True)
        return self.state

    def set_sequence_in_out(self, in_frame: int, out_frame: int) -> TimelineState:
        duration = self._state.duration_frames
        if duration <= 0:
            raise ValueError("Sequence has no media to define in and out points")
        bounds = SequenceInOut(
            in_frame=max(0, min(duration - 1, in_frame)),
            out_frame=max(1, min(duration, out_frame)),
        )

        def mutate(state: TimelineState) -> None:
            state.sequence = state.sequence.model_copy(update={"in_out": bounds})

        self._commit("设置序列入出点", mutate, allow_locked_changes=True)
        return self.state

    def clear_sequence_in_out(self) -> TimelineState:
        def mutate(state: TimelineState) -> None:
            state.sequence = state.sequence.model_copy(update={"in_out": None})

        self._commit("清除序列入出点", mutate, allow_locked_changes=True)
        return self.state

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
            asset = self.repository.catalog.get_asset(asset_id)
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
        candidate = self._snapshot(self._state)
        self._move_clips_in_state(
            candidate,
            selected_ids,
            primary_clip_id=primary_clip_id,
            timeline_start=timeline_start,
            track_id=track_id,
        )
        self._validator.validate(candidate, baseline=self._state)
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

        self._commit(label, mutate)
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
            asset = self.repository.catalog.get_asset(clip.asset_id)
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
        state.clips = [
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
        moved_clips = {item.id: item for item in state.clips}
        state.transitions = [
            item for item in state.transitions if TimelineRules.transition_is_valid(item, moved_clips)
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
        asset = self.repository.catalog.get_asset(source.asset_id)
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
        stored_asset = self.repository.catalog.get_asset(asset_id)
        if stored_asset.kind == AssetKind.WEB:
            raise ValueError("网页素材必须通过网页换版功能替换")
        asset = asset_in_timeline_clock(
            self.repository.catalog,
            stored_asset,
            self._state.sequence,
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
                item
                for item in state.transitions
                if TimelineRules.transition_is_valid(item, clips)
            ]

        self._commit("替换片段素材", mutate)
        return self._clip(clip_id)

    def set_clip_transform(self, clip_id: str, transform: ClipTransform) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"transform": transform})

        self._commit("调整画面", mutate)
        return self._clip(clip_id)

    def add_clip_visual_effect(
        self,
        clip_id: str,
        kind: VisualEffectKind,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        asset = self.repository.catalog.get_asset(clip.asset_id)
        if asset.kind not in {AssetKind.VIDEO, AssetKind.IMAGE}:
            raise ValueError("只有视频和图片片段可以添加视觉效果")
        effect = new_visual_effect(kind, len(clip.visual_effects))

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            source = state.clips[index]
            state.clips[index] = source.model_copy(
                update={"visual_effects": [*source.visual_effects, effect]}
            )

        self._commit("添加视觉效果", mutate)
        return next(item for item in self._clip(clip_id).visual_effects if item.id == effect.id)

    def update_clip_visual_effect(
        self,
        clip_id: str,
        effect_id: str,
        *,
        enabled: bool,
        parameters: dict[str, float],
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        source = next(item for item in clip.visual_effects if item.id == effect_id)
        updated = ClipVisualEffect.model_validate(
            {
                **source.model_dump(mode="python"),
                "enabled": enabled,
                "parameters": parameters,
            }
        )

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            current = state.clips[index]
            state.clips[index] = current.model_copy(
                update={
                    "visual_effects": [
                        updated if item.id == effect_id else item
                        for item in current.visual_effects
                    ]
                }
            )

        self._commit("调整视觉效果", mutate)
        return next(item for item in self._clip(clip_id).visual_effects if item.id == effect_id)

    def move_clip_visual_effect(
        self,
        clip_id: str,
        effect_id: str,
        position: int,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        if not 0 <= position < len(clip.visual_effects):
            raise ValueError("视觉效果位置超出效果链")
        effects = list(clip.visual_effects)
        source_index = next(index for index, item in enumerate(effects) if item.id == effect_id)
        effect = effects.pop(source_index)
        effects.insert(position, effect)
        effects = [item.model_copy(update={"position": index}) for index, item in enumerate(effects)]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={"visual_effects": effects}
            )

        self._commit("排序视觉效果", mutate)
        return next(item for item in self._clip(clip_id).visual_effects if item.id == effect_id)

    def remove_clip_visual_effect(self, clip_id: str, effect_id: str) -> None:
        clip = self._clip(clip_id)
        if effect_id not in {item.id for item in clip.visual_effects}:
            raise KeyError(effect_id)
        effects = [item for item in clip.visual_effects if item.id != effect_id]
        effects = [item.model_copy(update={"position": index}) for index, item in enumerate(effects)]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={"visual_effects": effects}
            )

        self._commit("移除视觉效果", mutate)

    def set_clip_transform_keyframes(
        self,
        clip_id: str,
        keyframes: list[ClipTransformKeyframe],
        *,
        expected_clip: Clip | None = None,
    ) -> Clip:
        ordered = sorted(keyframes, key=lambda item: item.source_frame)
        if len({item.source_frame for item in ordered}) != len(ordered):
            raise ValueError("画面跟踪关键帧不能位于同一源帧")

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            if expected_clip is not None and state.clips[index] != expected_clip:
                raise TimelineMergeConflict("clip", clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"transform_keyframes": ordered})

        self._commit("更新画面跟踪", mutate)
        return self._clip(clip_id)

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
                        "transform": clip.transform.model_copy(
                            update={"opacity": opacity}
                        ),
                    }
                )

        self._commit("批量调整片段", mutate)
        clips = {clip.id: clip for clip in self._state.clips}
        return [clips[clip_id] for clip_id in selected_ids]

    def set_web_clip_state(
        self,
        web_state: WebClipState,
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        current = self._state.web_states.get(web_state.clip_id)
        if current is None:
            raise KeyError(web_state.clip_id)
        if expected_revision is not None and current.revision != expected_revision:
            raise RuntimeError(
                f"Editable media revision conflict: expected {expected_revision}, current {current.revision}"
            )

        def mutate(state: TimelineState) -> None:
            state.web_states[web_state.clip_id] = web_state

        self._commit("编辑网页图层", mutate)
        return self._state.web_states[web_state.clip_id]

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
                consumed = source_frames_for_timeline_frames(
                    source.duration,
                    source.speed_numerator,
                    source.speed_denominator,
                )
                source_in = (
                    source.source_in + consumed - 1
                    if speed_numerator < 0
                    else max(0, source.source_in - consumed + 1)
                )
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
        source_delta = source_frames_for_timeline_frames(
            left_duration,
            source.speed_numerator,
            source.speed_denominator,
        )
        right_source_in = (
            source.source_in + source_delta if source.speed_numerator > 0 else source.source_in - source_delta
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
        asset = self.repository.catalog.get_asset(source.asset_id)
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
                    self._state,
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
        preview = self._state.model_copy(deep=True)
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

    def create_compound_clip(
        self,
        clip_ids: Iterable[str],
        *,
        name: str = "复合片段",
    ) -> CompoundClip:
        selected_ids = list(dict.fromkeys(clip_ids))
        if len(selected_ids) < 2:
            raise ValueError("请至少选择两个片段来创建复合片段")
        selected = sorted(
            (self._clip(clip_id) for clip_id in selected_ids),
            key=lambda clip: (clip.timeline_start, clip.id),
        )
        if len({clip.track_id for clip in selected}) != 1:
            raise ValueError("复合片段必须位于同一轨道")
        if any(
            left.timeline_end != right.timeline_start
            for left, right in zip(selected, selected[1:], strict=False)
        ):
            raise ValueError("复合片段中的片段必须首尾相接")
        occupied = {clip_id for item in self._state.compounds for clip_id in item.clip_ids}
        if occupied.intersection(selected_ids):
            raise ValueError("所选片段已经属于其他复合片段")
        compound = CompoundClip(
            sequence_id=self.sequence_id,
            name=name,
            clip_ids=[clip.id for clip in selected],
        )

        def mutate(state: TimelineState) -> None:
            state.compounds.append(compound)

        self._commit("创建复合片段", mutate)
        return next(item for item in self._state.compounds if item.id == compound.id)

    def dissolve_compound_clip(self, compound_id: str) -> None:
        if not any(item.id == compound_id for item in self._state.compounds):
            raise KeyError(compound_id)

        def mutate(state: TimelineState) -> None:
            state.compounds = [item for item in state.compounds if item.id != compound_id]

        self._commit("解除复合片段", mutate)

    def create_transition(
        self,
        left_clip_id: str,
        right_clip_id: str,
        kind: TransitionKind,
        duration: int,
    ) -> Transition:
        left = self._clip(left_clip_id)
        right = self._clip(right_clip_id)
        if not transition_is_available(kind, self._state.sequence.profile.color_mode):
            raise ValueError("Transition is not verified for HDR10 projects")
        if left.track_id != right.track_id:
            raise ValueError("Transition clips must be on the same track")
        if left.timeline_end != right.timeline_start:
            raise ValueError("Transition clips must be adjacent")
        if duration > min(left.duration, right.duration):
            raise ValueError("Transition duration exceeds a source clip")
        transition = Transition(
            track_id=left.track_id,
            left_clip_id=left.id,
            right_clip_id=right.id,
            kind=kind,
            duration=duration,
        )

        def mutate(state: TimelineState) -> None:
            state.transitions = [
                item
                for item in state.transitions
                if not (item.left_clip_id == left.id and item.right_clip_id == right.id)
            ]
            state.transitions.append(transition)

        self._commit("添加转场", mutate)
        return transition

    def update_transition(
        self,
        transition_id: str,
        *,
        kind: TransitionKind,
        duration: int,
        parameters: dict | None = None,
    ) -> Transition:
        source = self._transition(transition_id)
        if not transition_is_available(kind, self._state.sequence.profile.color_mode):
            raise ValueError("Transition is not verified for HDR10 projects")
        left = self._clip(source.left_clip_id)
        right = self._clip(source.right_clip_id)
        if duration <= 0 or duration > min(left.duration, right.duration):
            raise ValueError("Transition duration exceeds the available clips")

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.transitions) if item.id == transition_id)
            state.transitions[index] = source.model_copy(
                update={
                    "kind": kind,
                    "duration": duration,
                    "parameters": source.parameters if parameters is None else parameters,
                }
            )

        self._commit("调整转场", mutate)
        return self._transition(transition_id)

    def remove_transition(self, transition_id: str) -> None:
        self._transition(transition_id)

        def mutate(state: TimelineState) -> None:
            state.transitions = [item for item in state.transitions if item.id != transition_id]

        self._commit("移除转场", mutate)

    def add_marker(self, frame: int, name: str = "", color: str = "#4ea1ff") -> TimelineMarker:
        marker = TimelineMarker(
            sequence_id=self.sequence_id,
            frame=frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.markers.append(marker)

        self._commit("添加标记", mutate)
        return self._marker(marker.id)

    def replace_scene_markers(
        self,
        clip_id: str,
        frames: Iterable[int],
        *,
        expected_clip: Clip,
    ) -> list[TimelineMarker]:
        marker_prefix = f"场景切点 · {clip_id[:8]} · "
        markers = [
            TimelineMarker(
                sequence_id=self.sequence_id,
                frame=frame,
                name=f"{marker_prefix}{index}",
                color="#ff9f43",
            )
            for index, frame in enumerate(frames, start=1)
        ]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            if state.clips[index] != expected_clip:
                raise TimelineMergeConflict("clip", clip_id)
            state.markers = [marker for marker in state.markers if not marker.name.startswith(marker_prefix)]
            state.markers.extend(markers)

        self._commit("更新场景切点", mutate)
        return [self._marker(marker.id) for marker in markers]

    def update_marker(
        self,
        marker_id: str,
        *,
        frame: int,
        name: str,
        color: str,
    ) -> TimelineMarker:
        source = self._marker(marker_id)

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.markers) if item.id == marker_id)
            state.markers[index] = source.model_copy(update={"frame": frame, "name": name, "color": color})

        self._commit("调整标记", mutate)
        return self._marker(marker_id)

    def remove_marker(self, marker_id: str) -> None:
        self._marker(marker_id)

        def mutate(state: TimelineState) -> None:
            state.markers = [item for item in state.markers if item.id != marker_id]

        self._commit("删除标记", mutate)

    def add_range(
        self,
        start_frame: int,
        end_frame: int,
        name: str = "",
        color: str = "#4ea1ff",
    ) -> TimelineRange:
        item = TimelineRange(
            sequence_id=self.sequence_id,
            start_frame=start_frame,
            end_frame=end_frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.ranges.append(item)

        self._commit("添加范围", mutate)
        return self._range(item.id)

    def update_range(
        self,
        range_id: str,
        *,
        start_frame: int,
        end_frame: int,
        name: str,
        color: str,
    ) -> TimelineRange:
        source = self._range(range_id)

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.ranges) if item.id == range_id)
            state.ranges[index] = source.model_copy(
                update={
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "name": name,
                    "color": color,
                }
            )

        self._commit("调整范围", mutate)
        return self._range(range_id)

    def remove_range(self, range_id: str) -> None:
        self._range(range_id)

        def mutate(state: TimelineState) -> None:
            state.ranges = [item for item in state.ranges if item.id != range_id]

        self._commit("删除范围", mutate)

    def undo(self) -> TimelineState:
        self.history.undo()
        return self.state

    def redo(self) -> TimelineState:
        self.history.redo()
        return self.state

    snap_frame = staticmethod(snap_frame)

    def _commit(
        self,
        label: str,
        mutate: Callable[[TimelineState], None],
        *,
        allow_locked_changes: bool = False,
    ) -> None:
        before = self._snapshot(self._state)
        after = self._snapshot(before)
        mutate(after)
        TimelineRules.assign_default_primary_dialogue_track(after)
        TimelineRules.normalize_sequence_in_out(after)
        TimelineRules.normalize_compounds(after)
        self._validator.validate(
            after,
            baseline=before,
            allow_locked_changes=allow_locked_changes,
        )
        if after == before:
            return
        persisted = self._apply_change(before, after)
        self._state = persisted
        before_patch, after_patch = compact_timeline_change(before, after)

        self.history.push(
            ProjectEditCommand(
                label=label,
                undo_actions=[
                    ProjectEditAction(
                        kind=self._history_action_kind,
                        payload={
                            "mode": TIMELINE_HISTORY_MODE,
                            "source": after_patch.model_dump(
                                mode="json", exclude_computed_fields=True
                            ),
                            "destination": before_patch.model_dump(
                                mode="json", exclude_computed_fields=True
                            ),
                        },
                    )
                ],
                redo_actions=[
                    ProjectEditAction(
                        kind=self._history_action_kind,
                        payload={
                            "mode": TIMELINE_HISTORY_MODE,
                            "source": before_patch.model_dump(
                                mode="json", exclude_computed_fields=True
                            ),
                            "destination": after_patch.model_dump(
                                mode="json", exclude_computed_fields=True
                            ),
                        },
                    )
                ],
            )
        )

    def _apply_history_action(self, action: ProjectEditAction) -> None:
        payload = action.payload
        mode = str(payload.get("mode") or "")
        if mode == TIMELINE_HISTORY_MODE:
            self.restore_snapshot(
                TimelineState.model_validate(payload.get("source")),
                TimelineState.model_validate(payload.get("destination")),
            )
            return
        if mode == "frame_clock":
            self.repository.timeline.restore_main_frame_clock(
                MainFrameClockSnapshot.model_validate(payload.get("source")),
                MainFrameClockSnapshot.model_validate(payload.get("destination")),
            )
            self.reload()
            return
        raise ValueError(f"Unknown timeline history action mode: {mode}")

    def _apply_change(
        self,
        source: TimelineState,
        destination: TimelineState,
    ) -> TimelineState:
        stored_sequence = self.repository.catalog.get_sequence(self.sequence_id)
        current = (
            self._state
            if stored_sequence.timeline_revision
            == self._state.sequence.timeline_revision
            else self.repository.timeline.load_timeline(self.sequence_id)
        )
        merged = self._canonical_state(
            TimelineMergePolicy.merge(source, destination, current)
        )
        if merged == current:
            return current
        self._validator.validate(merged, baseline=self._state)
        return self._persist_change(current, merged)

    @staticmethod
    def _snapshot(state: TimelineState) -> TimelineState:
        # Editing commands replace validated domain objects instead of mutating
        # them in place. Copying the state containers is therefore a complete
        # session snapshot without recursively cloning hundreds of clips.
        return state.model_copy(
            update={
                "tracks": list(state.tracks),
                "clips": list(state.clips),
                "compounds": list(state.compounds),
                "transitions": list(state.transitions),
                "markers": list(state.markers),
                "ranges": list(state.ranges),
                "web_states": dict(state.web_states),
            }
        )

    @staticmethod
    def _canonical_state(state: TimelineState) -> TimelineState:
        """Match the durable timeline ordering without performing another read."""

        return state.model_copy(
            update={
                "tracks": sorted(
                    state.tracks,
                    key=lambda item: (item.position, item.id),
                ),
                "clips": sorted(
                    state.clips,
                    key=lambda item: (item.timeline_start, item.id),
                ),
                "compounds": sorted(
                    state.compounds,
                    key=lambda item: item.id,
                ),
                "transitions": sorted(
                    state.transitions,
                    key=lambda item: item.id,
                ),
                "markers": sorted(
                    state.markers,
                    key=lambda item: (item.frame, item.id),
                ),
                "ranges": sorted(
                    state.ranges,
                    key=lambda item: (item.start_frame, item.id),
                ),
                "web_states": dict(sorted(state.web_states.items())),
            }
        )

    def _persist_change(
        self,
        before: TimelineState,
        after: TimelineState,
    ) -> TimelineState:
        before_clips = {clip.id: clip for clip in before.clips}
        after_clips = {clip.id: clip for clip in after.clips}
        graph_is_unchanged = (
            before.sequence == after.sequence
            and before.tracks == after.tracks
            and before.compounds == after.compounds
            and before.transitions == after.transitions
            and before.markers == after.markers
            and before.ranges == after.ranges
            and set(before_clips) == set(after_clips)
        )
        if graph_is_unchanged:
            changed_clip_ids = {
                clip_id for clip_id, clip in after_clips.items() if clip != before_clips[clip_id]
            }
            changed_web_states = [
                web_state
                for clip_id, web_state in after.web_states.items()
                if web_state != before.web_states.get(clip_id)
            ]
            if changed_web_states:
                revision = self.repository.timeline.save_timeline(after)
            else:
                revision = self.repository.timeline.save_clip_changes(
                    after,
                    changed_clip_ids,
                )
        else:
            revision = self.repository.timeline.save_timeline(after)
        return self._canonical_state(after).model_copy(
            update={
                "sequence": after.sequence.model_copy(
                    update={"timeline_revision": revision}
                )
            }
        )

    def _track(self, track_id: str) -> Track:
        try:
            return next(track for track in self._state.tracks if track.id == track_id)
        except StopIteration as error:
            raise KeyError(track_id) from error

    def _clip(self, clip_id: str) -> Clip:
        try:
            return next(clip for clip in self._state.clips if clip.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error

    def _transition(self, transition_id: str) -> Transition:
        try:
            return next(item for item in self._state.transitions if item.id == transition_id)
        except StopIteration as error:
            raise KeyError(transition_id) from error

    def _marker(self, marker_id: str) -> TimelineMarker:
        try:
            return next(item for item in self._state.markers if item.id == marker_id)
        except StopIteration as error:
            raise KeyError(marker_id) from error

    def _range(self, range_id: str) -> TimelineRange:
        try:
            return next(item for item in self._state.ranges if item.id == range_id)
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
