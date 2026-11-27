from __future__ import annotations

from collections.abc import Iterable

from mediaflow.application.project_storage_ports import (
    AssetDocuments,
    WebMediaDocuments,
)
from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.application.timeline_entities import (
    clip_by_id,
    track_by_id,
)
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.application.timeline_snapping import snap_frame
from mediaflow.application.timeline_track_editing import ensure_linked_audio_track
from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.timeline import (
    Clip,
    ClipAddRequest,
    FreezeClipAddRequest,
    TimelineState,
    default_clip_media_kind,
)
from mediaflow.domain.web_state import WebClipState


class TimelineClipPlacement:
    def __init__(
        self, changes: TimelineChangeSession, assets: AssetDocuments, web: WebMediaDocuments
    ) -> None:
        self._changes = changes
        self.assets = assets
        self.web = web

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
            track = track_by_id(self._changes.current, track_id)
            asset = self.assets.get_asset(asset_id)
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
                self.web.get_web_asset_spec(asset.id).source_hash if asset.kind == AssetKind.WEB else None
            )
            prepared.append((clip, asset.kind, web_source_hash))

        def mutate(state: TimelineState) -> None:
            for clip, asset_kind, web_source_hash in prepared:
                if clip.media_kind == ClipMediaKind.LINKED_AV:
                    ensure_linked_audio_track(state, clip.track_id)
                state.clips.append(clip)
                if asset_kind == AssetKind.WEB:
                    assert web_source_hash is not None
                    state.web_states[clip.id] = WebClipState(
                        clip_id=clip.id,
                        source_hash=web_source_hash,
                    )

        self._changes.commit_change(
            "批量添加片段" if len(prepared) > 1 else "添加片段",
            mutate,
        )
        return [clip for clip, _, _ in prepared]

    def add_freeze_clip(self, request: FreezeClipAddRequest) -> Clip:
        track = track_by_id(self._changes.current, request.track_id)
        asset = self.assets.get_asset(request.asset_id)
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
            self.web.get_web_asset_spec(asset.id).source_hash if asset.kind == AssetKind.WEB else None
        )

        def mutate(state: TimelineState) -> None:
            state.clips.append(clip)
            if web_source_hash is not None:
                state.web_states[clip.id] = WebClipState(
                    clip_id=clip.id,
                    source_hash=web_source_hash,
                )

        self._changes.commit_change("添加定格片段", mutate)
        return clip_by_id(self._changes.current, clip.id)

    def move_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        track_id: str | None = None,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> Clip:
        source = clip_by_id(self._changes.current, clip_id)
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
        snapped_start = snap_frame(
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
        return [clip_by_id(self._changes.current, clip_id) for clip_id in selected_ids]

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
            asset = self.assets.get_asset(clip.asset_id)
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
                ensure_linked_audio_track(state, destination_id)
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
        source = clip_by_id(self._changes.current, clip_id)
        destination = track_id or source.track_id
        track = track_by_id(self._changes.current, destination)
        asset = self.assets.get_asset(source.asset_id)
        TimelineRules.validate_clip_track(
            asset.kind,
            source.media_kind,
            track.kind,
            asset.metadata.has_audio,
        )
        snapped = snap_frame(timeline_start, snap_targets, snap_tolerance_frames)
        copied = source.model_copy(
            update={"id": new_id(), "track_id": destination, "timeline_start": snapped}
        )

        def mutate(state: TimelineState) -> None:
            if copied.media_kind == ClipMediaKind.LINKED_AV:
                ensure_linked_audio_track(state, destination)
            state.clips.append(copied)
            if clip_id in state.web_states:
                state.web_states[copied.id] = state.web_states[clip_id].model_copy(
                    update={"clip_id": copied.id, "revision": 0}
                )

        self._changes.commit_change("创建片段副本", mutate)
        return clip_by_id(self._changes.current, copied.id)
