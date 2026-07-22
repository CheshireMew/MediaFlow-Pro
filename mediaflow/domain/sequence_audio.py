from __future__ import annotations

from dataclasses import dataclass

from mediaflow.domain.audio import AudioBus
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import TimelineState


@dataclass(frozen=True, slots=True)
class SequenceAudioSelection:
    track_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]


def select_audible_sequence_audio(
    state: TimelineState,
    assets: dict[str, Asset],
    buses: list[AudioBus],
    *,
    start_frame: int = 0,
    end_frame: int | None = None,
) -> SequenceAudioSelection:
    """Return the clips that can reach the sequence's audible master output."""
    end = state.duration_frames if end_frame is None else min(state.duration_frames, end_frame)
    start = max(0, start_frame)
    if end <= start or not buses:
        return SequenceAudioSelection((), ())

    by_id = {bus.id: bus for bus in buses}
    roots = [bus for bus in buses if bus.parent_bus_id is None]
    if len(roots) != 1:
        return SequenceAudioSelection((), ())
    master = roots[0]
    solo_bus_ids = {bus.id for bus in buses if bus.solo}
    allowed_bus_ids = set(by_id)
    if solo_bus_ids:
        allowed_bus_ids = set(solo_bus_ids)
        for bus_id in tuple(solo_bus_ids):
            cursor = by_id[bus_id]
            while cursor.parent_bus_id:
                allowed_bus_ids.add(cursor.parent_bus_id)
                cursor = by_id[cursor.parent_bus_id]

    def bus_reaches_master(bus_id: str) -> bool:
        seen: set[str] = set()
        cursor = by_id.get(bus_id)
        while cursor is not None:
            if cursor.id in seen or cursor.id not in allowed_bus_ids or cursor.muted:
                return False
            if cursor.id == master.id:
                return True
            seen.add(cursor.id)
            cursor = by_id.get(cursor.parent_bus_id or "")
        return False

    solo_track_ids = {track.id for track in state.tracks if track.solo}
    track_ids: list[str] = []
    asset_ids: list[str] = []
    for track in sorted(state.tracks, key=lambda item: (item.position, item.id)):
        if track.kind == TrackKind.SUBTITLE or not track.enabled or track.muted:
            continue
        if solo_track_ids and track.id not in solo_track_ids:
            continue
        if not bus_reaches_master(track.audio_bus_id or master.id):
            continue
        audible_assets = [
            clip.asset_id
            for clip in state.clips_for_track(track.id)
            if clip.timeline_start < end
            and clip.timeline_end > start
            and clip.asset_id in assets
            and assets[clip.asset_id].metadata.has_audio
        ]
        if not audible_assets:
            continue
        track_ids.append(track.id)
        for asset_id in audible_assets:
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)
    return SequenceAudioSelection(tuple(track_ids), tuple(asset_ids))
