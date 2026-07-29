from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction

from mediaflow.application.ports import ProjectCatalogDocuments
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import (
    Asset,
    MediaMetadata,
    ProjectProfile,
    Sequence,
    SequenceInOut,
)
from mediaflow.domain.timebase import reframe_frames, reframe_interval
from mediaflow.domain.timeline import Clip, ClipAudio, TimelineState


@dataclass(frozen=True, slots=True)
class TimelineClockChange:
    state: TimelineState
    assets: tuple[Asset, ...]


def project_frame_profile(catalog: ProjectCatalogDocuments) -> ProjectProfile:
    project = catalog.get_project()
    return catalog.get_sequence(project.main_sequence_id).profile


def asset_in_timeline_clock(
    catalog: ProjectCatalogDocuments,
    asset: Asset,
    sequence: Sequence,
) -> Asset:
    return asset.in_frame_clock(project_frame_profile(catalog), sequence.profile)


def assets_in_timeline_clock(
    catalog: ProjectCatalogDocuments,
    sequence: Sequence,
) -> dict[str, Asset]:
    project_profile = project_frame_profile(catalog)
    return {
        asset.id: asset.in_frame_clock(project_profile, sequence.profile)
        for asset in catalog.list_assets()
    }


def reframe_timeline_clock(
    state: TimelineState,
    stored_assets: Iterable[Asset],
    destination_profile: ProjectProfile,
    *,
    asset_source_profile: ProjectProfile,
    metadata_overrides: Mapping[str, MediaMetadata] | None = None,
    invalidate_proxies: bool = False,
) -> TimelineClockChange:
    """Move a timeline and all contextual asset bounds to one new frame clock.

    ``stored_assets`` use ``asset_source_profile``. Metadata overrides must
    already be measured in ``destination_profile``. This is the sole boundary
    that changes clip source time, timeline time, transitions, annotations and
    the asset bounds used to validate them.
    """

    old_profile = state.sequence.profile
    overrides = dict(metadata_overrides or {})
    source_assets = tuple(stored_assets)
    unknown_overrides = set(overrides) - {asset.id for asset in source_assets}
    if unknown_overrides:
        raise ValueError(
            f"Frame-clock metadata overrides reference unknown assets: {sorted(unknown_overrides)}"
        )

    target_assets: list[Asset] = []
    for asset in source_assets:
        metadata = overrides.get(asset.id)
        if metadata is None:
            metadata = asset.metadata.in_frame_clock(
                asset_source_profile,
                destination_profile,
            )
        updates: dict[str, object] = {"metadata": metadata}
        if invalidate_proxies:
            updates.update(
                {
                    "proxy_path": None,
                    "sdr_preview_proxy_path": None,
                }
            )
        target_assets.append(asset.model_copy(update=updates))
    assets_by_id = {asset.id: asset for asset in target_assets}

    def reframe(value: int) -> int:
        return reframe_frames(value, old_profile, destination_profile)

    def reframe_range(start: int, end: int) -> tuple[int, int]:
        return reframe_interval(
            start,
            end,
            old_profile,
            destination_profile,
        )

    reframed_bounds = (
        reframe_range(bounds.in_frame, bounds.out_frame)
        if (bounds := state.sequence.in_out) is not None
        else None
    )
    reframed_ranges = []
    for item in state.ranges:
        start_frame, end_frame = reframe_range(
            item.start_frame,
            item.end_frame,
        )
        reframed_ranges.append(
            item.model_copy(
                update={
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                }
            )
        )
    reframed_clips = [
        _reframe_clip(
            clip,
            assets_by_id[clip.asset_id],
            reframe=reframe,
        )
        for clip in state.clips
    ]
    clips_by_id = {clip.id: clip for clip in reframed_clips}
    reframed_transitions = []
    for transition in state.transitions:
        left = clips_by_id.get(transition.left_clip_id)
        right = clips_by_id.get(transition.right_clip_id)
        if left is None or right is None:
            raise ValueError(
                f"Transition {transition.id} references a missing clip during frame-clock conversion"
            )
        if left.timeline_end != right.timeline_start:
            raise ValueError(
                f"Transition {transition.id} cannot preserve its clip boundary "
                "in the destination frame clock"
            )
        candidate = transition.model_copy(
            update={
                "duration": min(
                    max(1, reframe(transition.duration)),
                    left.duration,
                    right.duration,
                )
            }
        )
        if not TimelineRules.transition_is_valid(candidate, clips_by_id):
            raise ValueError(
                f"Transition {transition.id} is invalid in the destination frame clock"
            )
        reframed_transitions.append(candidate)

    reframed = TimelineState(
        sequence=state.sequence.model_copy(
            update={
                "profile": destination_profile,
                "profile_confirmed": True,
                "in_out": (
                    SequenceInOut(
                        in_frame=reframed_bounds[0],
                        out_frame=reframed_bounds[1],
                    )
                    if reframed_bounds is not None
                    else None
                ),
            }
        ),
        tracks=list(state.tracks),
        clips=reframed_clips,
        compounds=list(state.compounds),
        transitions=reframed_transitions,
        markers=[
            marker.model_copy(update={"frame": reframe(marker.frame)})
            for marker in state.markers
        ],
        ranges=reframed_ranges,
        web_states=dict(state.web_states),
    )
    TimelineRules.normalize_compounds(reframed)
    TimelineRules.normalize_sequence_in_out(reframed)
    return TimelineClockChange(state=reframed, assets=tuple(target_assets))


def _reframe_clip(
    clip: Clip,
    asset: Asset,
    *,
    reframe: Callable[[int], int],
) -> Clip:
    timeline_start = reframe(clip.timeline_start)
    source_in = reframe(clip.source_in)
    if (
        asset.kind not in {AssetKind.IMAGE, AssetKind.WEB}
        and asset.metadata.duration_frames > 0
    ):
        source_in = min(source_in, asset.metadata.duration_frames - 1)
    values = clip.model_dump(mode="python", exclude_computed_fields=True)
    candidate = Clip.model_validate(
        {
            **values,
            "timeline_start": timeline_start,
            "source_in": source_in,
            "duration": max(1, reframe(clip.timeline_end) - timeline_start),
            "audio": ClipAudio(
                gain_db=clip.audio.gain_db,
                pan=clip.audio.pan,
                fade_in_frames=reframe(clip.audio.fade_in_frames),
                fade_out_frames=reframe(clip.audio.fade_out_frames),
            ),
            "transform_keyframes": [],
        }
    )
    maximum_duration = candidate.maximum_timeline_duration(
        asset.kind,
        asset.metadata.duration_frames,
    )
    if maximum_duration is None or candidate.duration <= maximum_duration:
        duration = candidate.duration
    elif maximum_duration <= 0:
        raise ValueError(
            f"Clip {clip.id} has no source frames available after changing the frame clock"
        )
    else:
        duration = maximum_duration

    speed = Fraction(
        abs(candidate.speed_numerator),
        candidate.speed_denominator,
    )
    consumed = -(-(duration * speed.numerator) // speed.denominator)
    if candidate.speed_numerator > 0:
        first_source_frame = candidate.source_in
        last_source_frame = candidate.source_in + consumed - 1
    else:
        first_source_frame = candidate.source_in - consumed + 1
        last_source_frame = candidate.source_in
    keyframes_by_frame = {}
    for keyframe in clip.transform_keyframes:
        target_frame = reframe(keyframe.source_frame)
        if first_source_frame <= target_frame <= last_source_frame:
            keyframes_by_frame[target_frame] = keyframe.model_copy(
                update={"source_frame": target_frame}
            )
    return Clip.model_validate(
        {
            **candidate.model_dump(
                mode="python",
                exclude_computed_fields=True,
            ),
            "duration": duration,
            "transform_keyframes": [
                keyframes_by_frame[frame]
                for frame in sorted(keyframes_by_frame)
            ],
        }
    )
