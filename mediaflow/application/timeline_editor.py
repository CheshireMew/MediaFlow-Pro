from __future__ import annotations

from collections.abc import Iterable

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.application.timeline_clip_editing import TimelineClipEditing
from mediaflow.application.timeline_clip_placement import TimelineClipPlacement
from mediaflow.application.timeline_marker_editing import TimelineMarkerRangeEditing
from mediaflow.application.timeline_multicam_editing import TimelineMulticamEditing
from mediaflow.application.timeline_review_editing import TimelineReviewEditing
from mediaflow.application.timeline_snapping import snap_frame
from mediaflow.application.timeline_structure_editing import TimelineStructureEditing
from mediaflow.application.timeline_track_editing import TimelineTrackEditing
from mediaflow.application.timeline_visual_editing import TimelineVisualEditing
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.enums import (
    MaskShapeKind,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.keyframes import KeyframeCurve
from mediaflow.domain.masks import ClipMask, MaskGeometry, MaskKeyframe
from mediaflow.domain.multicam import (
    MulticamAngleSyncSpec,
    MulticamGroup,
    MulticamSyncAnalysis,
)
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.review import (
    ReviewMarkupShape,
    ReviewPriority,
    ReviewSnapshot,
    ReviewThread,
)
from mediaflow.domain.timeline import (
    Clip,
    ClipAddRequest,
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    CompoundClip,
    FreezeClipAddRequest,
    TimelineMarker,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
)
from mediaflow.domain.visual_effects import ClipVisualEffect, VisualEffectParameterKeyframe
from mediaflow.domain.web_state import WebClipState


class TimelineEditor:
    """Typed public editing surface composed from independent operation owners."""

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
            self._changes.commit_change,
        )
        self._review_editing = TimelineReviewEditing(
            lambda: self.state,
            self._changes.commit_change,
            repository.content_revision,
        )
        self._multicam_editing = TimelineMulticamEditing(
            repository,
            lambda: self.state,
            self._changes.commit_change,
        )
        self._clip_placement = TimelineClipPlacement(self._changes, repository.assets, repository.web)
        self._clip_editing = TimelineClipEditing(
            self._changes, repository.assets, repository.projects, repository.sequences
        )
        self._track_editing = TimelineTrackEditing(self._changes, repository.audio)
        self._structure_editing = TimelineStructureEditing(self._changes)
        self._marker_editing = TimelineMarkerRangeEditing(self._changes)

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

    def create_multicam_group(
        self,
        name: str,
        angles: list[MulticamAngleSyncSpec],
        *,
        timeline_start: int,
        duration: int,
        sync_offset: int = 0,
        initial_angle_id: str | None = None,
        sync_method: str = "manual",
        sync_confidence: float | None = None,
        audio_strategy: str = "follow_video",
        master_audio_angle_id: str | None = None,
    ) -> MulticamGroup:
        return self._multicam_editing.create_group(
            name,
            angles,
            timeline_start=timeline_start,
            duration=duration,
            sync_offset=sync_offset,
            initial_angle_id=initial_angle_id,
            sync_method=sync_method,
            sync_confidence=sync_confidence,
            audio_strategy=audio_strategy,
            master_audio_angle_id=master_audio_angle_id,
        )

    def analyze_multicam_sync(self, asset_ids: list[str], method: str) -> MulticamSyncAnalysis:
        return self._multicam_editing.analyze_sync(asset_ids, method)

    def switch_multicam_angle(
        self,
        group_id: str,
        angle_id: str,
        timeline_frame: int,
    ) -> MulticamGroup:
        return self._multicam_editing.switch_angle(group_id, angle_id, timeline_frame)

    def list_multicam_groups(self) -> list[MulticamGroup]:
        return self._multicam_editing.list_groups()

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
        return self._clip_placement.add_clip(
            track_id=track_id,
            asset_id=asset_id,
            timeline_start=timeline_start,
            source_in=source_in,
            duration=duration,
            speed_numerator=speed_numerator,
            speed_denominator=speed_denominator,
            pitch_compensation=pitch_compensation,
        )

    def add_clips(self, specifications: Iterable[ClipAddRequest]) -> list[Clip]:
        return self._clip_placement.add_clips(specifications)

    def add_freeze_clip(self, request: FreezeClipAddRequest) -> Clip:
        return self._clip_placement.add_freeze_clip(request)

    def replace_contents(
        self, destination: TimelineState, *, label: str = "导入可携带时间线"
    ) -> TimelineState:
        return self._structure_editing.replace_contents(destination, label=label)

    def move_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        track_id: str | None = None,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> Clip:
        return self._clip_placement.move_clip(
            clip_id,
            timeline_start=timeline_start,
            track_id=track_id,
            snap_targets=snap_targets,
            snap_tolerance_frames=snap_tolerance_frames,
        )

    def preview_move_clips(
        self, clip_ids: Iterable[str], *, primary_clip_id: str, timeline_start: int, track_id: str
    ) -> list[Clip]:
        return self._clip_placement.preview_move_clips(
            clip_ids, primary_clip_id=primary_clip_id, timeline_start=timeline_start, track_id=track_id
        )

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
        return self._clip_placement.move_clips(
            clip_ids,
            primary_clip_id=primary_clip_id,
            timeline_start=timeline_start,
            track_id=track_id,
            snap_targets=snap_targets,
            snap_tolerance_frames=snap_tolerance_frames,
        )

    def copy_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        track_id: str | None = None,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> Clip:
        return self._clip_placement.copy_clip(
            clip_id,
            timeline_start=timeline_start,
            track_id=track_id,
            snap_targets=snap_targets,
            snap_tolerance_frames=snap_tolerance_frames,
        )

    def trim_clip(self, clip_id: str, *, timeline_start: int, source_in: int, duration: int) -> Clip:
        return self._clip_editing.trim_clip(
            clip_id, timeline_start=timeline_start, source_in=source_in, duration=duration
        )

    def replace_clip_source(self, clip_id: str, asset_id: str) -> Clip:
        return self._clip_editing.replace_clip_source(clip_id, asset_id)

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

    def assign_clip_visual_effect_mask(
        self,
        clip_id: str,
        effect_id: str,
        mask_id: str | None,
    ) -> ClipVisualEffect:
        return self._visual_editing.assign_effect_mask(clip_id, effect_id, mask_id)

    def add_clip_mask(
        self,
        clip_id: str,
        kind: MaskShapeKind,
        *,
        name: str,
        geometry: MaskGeometry,
        combine_mode: str = "replace",
    ) -> ClipMask:
        return self._visual_editing.add_mask(
            clip_id,
            kind,
            name=name,
            geometry=geometry,
            combine_mode=combine_mode,
        )

    def update_clip_mask(
        self,
        clip_id: str,
        mask_id: str,
        *,
        name: str,
        enabled: bool,
        inverted: bool,
        feather: int,
        feather_passes: int,
        opacity: float,
        geometry: MaskGeometry,
        combine_mode: str = "replace",
    ) -> ClipMask:
        return self._visual_editing.update_mask(
            clip_id,
            mask_id,
            name=name,
            enabled=enabled,
            combine_mode=combine_mode,
            inverted=inverted,
            feather=feather,
            feather_passes=feather_passes,
            opacity=opacity,
            geometry=geometry,
        )

    def move_clip_mask(self, clip_id: str, mask_id: str, position: int) -> ClipMask:
        return self._visual_editing.move_mask(clip_id, mask_id, position)

    def remove_clip_mask(self, clip_id: str, mask_id: str) -> None:
        self._visual_editing.remove_mask(clip_id, mask_id)

    def upsert_clip_mask_keyframe(
        self,
        clip_id: str,
        mask_id: str,
        timeline_offset: int,
        geometry: MaskGeometry,
        *,
        curve: KeyframeCurve | None = None,
    ) -> ClipMask:
        return self._visual_editing.upsert_mask_keyframe(
            clip_id,
            mask_id,
            timeline_offset,
            geometry,
            curve=curve,
        )

    def remove_clip_mask_keyframe(
        self,
        clip_id: str,
        mask_id: str,
        timeline_offset: int,
    ) -> ClipMask:
        return self._visual_editing.remove_mask_keyframe(
            clip_id,
            mask_id,
            timeline_offset,
        )

    def move_clip_mask_keyframe(
        self,
        clip_id: str,
        mask_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> ClipMask:
        return self._visual_editing.move_mask_keyframe(
            clip_id,
            mask_id,
            old_timeline_offset,
            new_timeline_offset,
        )

    def retime_clip_mask_keyframes(
        self,
        clip_id: str,
        mask_id: str,
        timeline_offsets: list[int],
        *,
        anchor_offset: int,
        scale: float,
    ) -> ClipMask:
        return self._visual_editing.retime_mask_keyframes(
            clip_id,
            mask_id,
            timeline_offsets,
            anchor_offset=anchor_offset,
            scale=scale,
        )

    def set_clip_mask_keyframes(
        self,
        clip_id: str,
        mask_id: str,
        keyframes: list[MaskKeyframe],
        *,
        expected_clip: Clip | None = None,
    ) -> ClipMask:
        return self._visual_editing.set_mask_keyframes(
            clip_id,
            mask_id,
            keyframes,
            expected_clip=expected_clip,
        )

    def add_review_thread(
        self,
        start_frame: int,
        body: str,
        author: ActorIdentity,
        *,
        end_frame: int | None = None,
        clip_id: str | None = None,
        subject: str = "",
        priority: ReviewPriority = "normal",
    ) -> ReviewThread:
        return self._review_editing.add_thread(
            start_frame,
            body,
            author,
            end_frame=end_frame,
            clip_id=clip_id,
            subject=subject,
            priority=priority,
        )

    def reply_review_thread(
        self,
        thread_id: str,
        body: str,
        author: ActorIdentity,
    ) -> ReviewThread:
        return self._review_editing.reply(thread_id, body, author)

    def add_review_snapshot(self, thread_id: str, snapshot: ReviewSnapshot) -> ReviewThread:
        return self._review_editing.add_snapshot(thread_id, snapshot)

    def set_review_snapshot_markup(
        self,
        thread_id: str,
        snapshot_id: str,
        markup: list[ReviewMarkupShape],
    ) -> ReviewThread:
        return self._review_editing.set_snapshot_markup(thread_id, snapshot_id, markup)

    def import_review_threads(self, threads: list[ReviewThread]) -> list[ReviewThread]:
        return self._review_editing.import_threads(threads)

    def edit_review_message(
        self,
        thread_id: str,
        message_id: str,
        body: str,
        actor: ActorIdentity,
    ) -> ReviewThread:
        return self._review_editing.edit_message(thread_id, message_id, body, actor)

    def update_review_thread(
        self,
        thread_id: str,
        *,
        start_frame: int,
        end_frame: int | None,
        clip_id: str | None,
        subject: str,
        priority: ReviewPriority,
    ) -> ReviewThread:
        return self._review_editing.update_thread(
            thread_id,
            start_frame=start_frame,
            end_frame=end_frame,
            clip_id=clip_id,
            subject=subject,
            priority=priority,
        )

    def resolve_review_thread(
        self,
        thread_id: str,
        actor: ActorIdentity,
    ) -> ReviewThread:
        return self._review_editing.resolve(thread_id, actor)

    def reopen_review_thread(self, thread_id: str) -> ReviewThread:
        return self._review_editing.reopen(thread_id)

    def archive_review_thread(self, thread_id: str) -> ReviewThread:
        return self._review_editing.archive(thread_id)

    def restore_review_thread(self, thread_id: str) -> ReviewThread:
        return self._review_editing.restore(thread_id)

    def upsert_clip_transform_keyframe(
        self,
        clip_id: str,
        timeline_offset: int,
        transform: ClipTransform,
        *,
        curve: KeyframeCurve | None = None,
    ) -> Clip:
        return self._visual_editing.upsert_transform_keyframe(
            clip_id,
            timeline_offset,
            transform,
            curve=curve,
        )

    def remove_clip_transform_keyframe(
        self,
        clip_id: str,
        timeline_offset: int,
    ) -> Clip:
        return self._visual_editing.remove_transform_keyframe(
            clip_id,
            timeline_offset,
        )

    def move_clip_transform_keyframe(
        self,
        clip_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> Clip:
        return self._visual_editing.move_transform_keyframe(
            clip_id,
            old_timeline_offset,
            new_timeline_offset,
        )

    def retime_clip_transform_keyframes(
        self,
        clip_id: str,
        timeline_offsets: list[int],
        *,
        anchor_offset: int,
        scale: float,
    ) -> Clip:
        return self._visual_editing.retime_transform_keyframes(
            clip_id,
            timeline_offsets,
            anchor_offset=anchor_offset,
            scale=scale,
        )

    def upsert_clip_effect_parameter_keyframe(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        timeline_offset: int,
        value: float,
        *,
        curve: KeyframeCurve | None = None,
    ) -> ClipVisualEffect:
        return self._visual_editing.upsert_effect_parameter_keyframe(
            clip_id,
            effect_id,
            field_id,
            timeline_offset,
            value,
            curve=curve,
        )

    def set_clip_effect_parameter_keyframes(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        keyframes: list[VisualEffectParameterKeyframe],
    ) -> ClipVisualEffect:
        return self._visual_editing.set_effect_parameter_keyframes(
            clip_id,
            effect_id,
            field_id,
            keyframes,
        )

    def remove_clip_effect_parameter_keyframe(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        timeline_offset: int,
    ) -> ClipVisualEffect:
        return self._visual_editing.remove_effect_parameter_keyframe(
            clip_id,
            effect_id,
            field_id,
            timeline_offset,
        )

    def move_clip_effect_parameter_keyframe(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> ClipVisualEffect:
        return self._visual_editing.move_effect_parameter_keyframe(
            clip_id,
            effect_id,
            field_id,
            old_timeline_offset,
            new_timeline_offset,
        )

    def retime_clip_effect_parameter_keyframes(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        timeline_offsets: list[int],
        *,
        anchor_offset: int,
        scale: float,
    ) -> ClipVisualEffect:
        return self._visual_editing.retime_effect_parameter_keyframes(
            clip_id,
            effect_id,
            field_id,
            timeline_offsets,
            anchor_offset=anchor_offset,
            scale=scale,
        )

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

    def paste_clip_transform_keyframes(
        self,
        clip_id: str,
        keyframes: list[ClipTransformKeyframe],
        *,
        timeline_frame: int,
        scale: float,
    ) -> Clip:
        return self._visual_editing.paste_transform_keyframes(
            clip_id,
            keyframes,
            timeline_frame=timeline_frame,
            scale=scale,
        )

    def paste_clip_effect_parameter_keyframes(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        keyframes: list[VisualEffectParameterKeyframe],
        *,
        timeline_frame: int,
        scale: float,
    ) -> ClipVisualEffect:
        return self._visual_editing.paste_effect_parameter_keyframes(
            clip_id,
            effect_id,
            field_id,
            keyframes,
            timeline_frame=timeline_frame,
            scale=scale,
        )

    def set_clip_audio(self, clip_id: str, audio: ClipAudio) -> Clip:
        return self._clip_editing.set_clip_audio(clip_id, audio)

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
        return self._clip_editing.set_clips_properties(
            clip_ids,
            gain_db=gain_db,
            pan=pan,
            fade_in_frames=fade_in_frames,
            fade_out_frames=fade_out_frames,
            opacity=opacity,
        )

    def set_web_clip_state(
        self, web_state: WebClipState, *, expected_revision: int | None = None
    ) -> WebClipState:
        return self._clip_editing.set_web_clip_state(web_state, expected_revision=expected_revision)

    def set_clip_speed(
        self, clip_id: str, *, speed_numerator: int, speed_denominator: int, pitch_compensation: bool
    ) -> Clip:
        return self._clip_editing.set_clip_speed(
            clip_id,
            speed_numerator=speed_numerator,
            speed_denominator=speed_denominator,
            pitch_compensation=pitch_compensation,
        )

    def split_clip(self, clip_id: str, split_frame: int) -> tuple[Clip, Clip]:
        return self._clip_editing.split_clip(clip_id, split_frame)

    def detach_clip_audio(self, clip_id: str) -> tuple[Clip, Clip]:
        return self._clip_editing.detach_clip_audio(clip_id)

    def delete_clip(self, clip_id: str, *, ripple: bool = False) -> None:
        return self._clip_editing.delete_clip(clip_id, ripple=ripple)

    def delete_clips(self, clip_ids: Iterable[str], *, ripple: bool = False) -> None:
        return self._clip_editing.delete_clips(clip_ids, ripple=ripple)

    def preview_ripple_delete_intervals(self, intervals: Iterable[tuple[int, int]]) -> TimelineState:
        return self._clip_editing.preview_ripple_delete_intervals(intervals)

    def apply_ripple_delete_intervals(
        self, intervals: Iterable[tuple[int, int]], *, label: str = "按转录计划波纹剪辑"
    ) -> None:
        return self._clip_editing.apply_ripple_delete_intervals(intervals, label=label)

    def undo(self) -> TimelineState:
        return self._changes.undo()

    def redo(self) -> TimelineState:
        return self._changes.redo()

    snap_frame = staticmethod(snap_frame)

    def add_track(
        self,
        kind: TrackKind,
        name: str | None = None,
        *,
        audio_bus_id: str | None = None,
        position: int | None = None,
    ) -> Track:
        return self._track_editing.add_track(kind, name, audio_bus_id=audio_bus_id, position=position)

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
        return self._track_editing.set_track_state(
            track_id, enabled=enabled, locked=locked, muted=muted, solo=solo, audio_bus_id=audio_bus_id
        )

    def set_subtitle_track_style(self, track_id: str, style: SubtitleStyle) -> Track:
        return self._track_editing.set_subtitle_track_style(track_id, style)

    def set_primary_dialogue_track(self, track_id: str) -> Track:
        return self._track_editing.set_primary_dialogue_track(track_id)

    def move_track(self, track_id: str, position: int) -> Track:
        return self._track_editing.move_track(track_id, position)

    def set_sequence_profile(self, profile: ProjectProfile) -> TimelineState:
        return self._track_editing.set_sequence_profile(profile)

    def set_sequence_in_out(self, in_frame: int, out_frame: int) -> TimelineState:
        return self._track_editing.set_sequence_in_out(in_frame, out_frame)

    def clear_sequence_in_out(self) -> TimelineState:
        return self._track_editing.clear_sequence_in_out()

    def create_compound_clip(self, clip_ids: Iterable[str], *, name: str = "复合片段") -> CompoundClip:
        return self._structure_editing.create_compound_clip(clip_ids, name=name)

    def dissolve_compound_clip(self, compound_id: str) -> None:
        return self._structure_editing.dissolve_compound_clip(compound_id)

    def create_transition(
        self, left_clip_id: str, right_clip_id: str, kind: TransitionKind, duration: int
    ) -> Transition:
        return self._structure_editing.create_transition(left_clip_id, right_clip_id, kind, duration)

    def update_transition(
        self, transition_id: str, *, kind: TransitionKind, duration: int, parameters: dict | None = None
    ) -> Transition:
        return self._structure_editing.update_transition(
            transition_id, kind=kind, duration=duration, parameters=parameters
        )

    def remove_transition(self, transition_id: str) -> None:
        return self._structure_editing.remove_transition(transition_id)

    def add_marker(self, frame: int, name: str = "", color: str = "#4ea1ff") -> TimelineMarker:
        return self._marker_editing.add_marker(frame, name, color)

    def replace_scene_markers(
        self, clip_id: str, frames: Iterable[int], *, expected_clip: Clip
    ) -> list[TimelineMarker]:
        return self._marker_editing.replace_scene_markers(clip_id, frames, expected_clip=expected_clip)

    def update_marker(self, marker_id: str, *, frame: int, name: str, color: str) -> TimelineMarker:
        return self._marker_editing.update_marker(marker_id, frame=frame, name=name, color=color)

    def remove_marker(self, marker_id: str) -> None:
        return self._marker_editing.remove_marker(marker_id)

    def add_range(
        self, start_frame: int, end_frame: int, name: str = "", color: str = "#4ea1ff"
    ) -> TimelineRange:
        return self._marker_editing.add_range(start_frame, end_frame, name, color)

    def update_range(
        self, range_id: str, *, start_frame: int, end_frame: int, name: str, color: str
    ) -> TimelineRange:
        return self._marker_editing.update_range(
            range_id, start_frame=start_frame, end_frame=end_frame, name=name, color=color
        )

    def remove_range(self, range_id: str) -> None:
        return self._marker_editing.remove_range(range_id)
