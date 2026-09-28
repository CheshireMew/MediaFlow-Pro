from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.enums import ColorMode
from mediaflow.domain.portable_timeline import LoadedPortableTimeline, PortableCaptionClip
from mediaflow.domain.production_bundle import (
    ProductionAudioSourceInspection,
    ProductionBundle,
    ProductionBundleInspection,
    ProductionCoverInspection,
    ProductionSubtitleInspection,
    review_similar_speech,
)
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.timebase import seconds_to_frames


class ProductionBundleInspectionService:
    def __init__(
        self,
        loader: Callable[[str | Path], LoadedPortableTimeline],
    ) -> None:
        self._loader = loader

    def inspect(self, bundle: ProductionBundle) -> ProductionBundleInspection:
        loaded = self._loader(bundle.timeline.path)
        document = loaded.document
        duration = document.duration_seconds
        if duration <= 0:
            raise ValueError("Production timeline duration must be positive")
        profile = document.profile
        if profile.sample_rate != 48_000:
            raise ValueError("MediaFlow Pro projects use a 48 kHz audio clock")
        fps = Fraction(str(profile.frame_rate)).limit_denominator(100_000)
        project_profile = ProjectProfile(
            width=profile.width,
            height=profile.height,
            fps_numerator=fps.numerator,
            fps_denominator=fps.denominator,
            color_mode=ColorMode.SDR_BT709,
            bit_depth=8,
            audio_sample_rate=48_000,
            audio_channels=1 if profile.channel_layout == "mono" else 2,
        )
        self._inspect_build_units(bundle, duration, project_profile)
        cover = self._inspect_cover(bundle, loaded)
        subtitles = self._inspect_subtitles(bundle, loaded)
        required_audio_sources = self._inspect_required_audio_sources(bundle, loaded)
        speech_review = review_similar_speech(bundle.speech_review)
        return ProductionBundleInspection(
            status="review_required" if speech_review.blocks_production else "ready",
            timeline_path=str(loaded.path),
            timeline_sha256=loaded.sha256,
            timeline_project_id=document.project_id,
            portable_profile=profile,
            project_profile=project_profile,
            duration_seconds=duration,
            source_count=len(document.sources),
            track_count=len(document.tracks),
            clip_count=sum(len(track.clips) for track in document.tracks),
            export_strategy=("segmented" if bundle.output.build_units else "single-pass"),
            build_unit_count=len(bundle.output.build_units),
            cover=cover,
            subtitles=subtitles,
            required_audio_sources=required_audio_sources,
            speech_review=speech_review,
        )

    @staticmethod
    def _inspect_build_units(
        bundle: ProductionBundle,
        duration_seconds: float,
        profile: ProjectProfile,
    ) -> None:
        if not bundle.output.build_units:
            return
        duration_frames = seconds_to_frames(
            duration_seconds,
            profile.fps_numerator,
            profile.fps_denominator,
        )
        if bundle.output.build_units[-1].end_frame != duration_frames:
            raise ValueError(
                "output.build_units must cover the complete production timeline "
                f"through frame {duration_frames}"
            )

    @staticmethod
    def _inspect_cover(
        bundle: ProductionBundle,
        loaded: LoadedPortableTimeline,
    ) -> ProductionCoverInspection:
        expectation = bundle.timeline.cover
        if expectation is None:
            return ProductionCoverInspection(
                requested=False,
                starts_at_frame_zero=False,
            )
        source = next(
            (item for item in loaded.document.sources if item.id == expectation.source_id),
            None,
        )
        if source is None:
            raise ValueError(f"Cover source does not exist: {expectation.source_id}")
        if source.kind != "image":
            raise ValueError(f"Cover source must be an image: {expectation.source_id}")
        candidates = [
            clip
            for track in loaded.document.tracks
            if track.kind == "video" and not track.muted
            for clip in track.clips
            if getattr(clip, "source_id", None) == expectation.source_id
            and abs(clip.timeline_start_seconds) <= 1e-9
        ]
        if not candidates:
            raise ValueError(
                f"Cover source {expectation.source_id} must appear at frame zero on an active video track"
            )
        clip = min(candidates, key=lambda item: (item.duration_seconds, item.id))
        duration_frames = clip.duration_seconds * loaded.document.profile.frame_rate
        if duration_frames > expectation.max_duration_frames + 1e-6:
            raise ValueError(
                f"Cover clip {clip.id} lasts {duration_frames:g} frames; "
                f"the production request allows at most {expectation.max_duration_frames}"
            )
        return ProductionCoverInspection(
            requested=True,
            source_id=expectation.source_id,
            clip_id=clip.id,
            duration_seconds=clip.duration_seconds,
            duration_frames=duration_frames,
            starts_at_frame_zero=True,
        )

    @staticmethod
    def _inspect_subtitles(
        bundle: ProductionBundle,
        loaded: LoadedPortableTimeline,
    ) -> list[ProductionSubtitleInspection]:
        subtitle_tracks = {
            track.id: track
            for track in loaded.document.tracks
            if track.kind == "subtitle"
        }
        requested = bundle.timeline.subtitle_track_ids or list(subtitle_tracks)
        inspections: list[ProductionSubtitleInspection] = []
        for track_id in requested:
            track = subtitle_tracks.get(track_id)
            if track is None:
                raise ValueError(f"Required subtitle track does not exist: {track_id}")
            if track.muted:
                raise ValueError(f"Required subtitle track is muted: {track_id}")
            caption_count = sum(isinstance(clip, PortableCaptionClip) for clip in track.clips)
            if caption_count == 0:
                raise ValueError(f"Required subtitle track has no captions: {track_id}")
            inspections.append(
                ProductionSubtitleInspection(
                    track_id=track_id,
                    caption_count=caption_count,
                )
            )
        return inspections

    @staticmethod
    def _inspect_required_audio_sources(
        bundle: ProductionBundle,
        loaded: LoadedPortableTimeline,
    ) -> list[ProductionAudioSourceInspection]:
        sources = {source.id: source for source in loaded.document.sources}
        inspections: list[ProductionAudioSourceInspection] = []
        for source_id in bundle.timeline.required_audio_source_ids:
            source = sources.get(source_id)
            if source is None:
                raise ValueError(f"Required audio source does not exist: {source_id}")
            if source.kind not in {"audio", "video", "web-render"}:
                raise ValueError(f"Required audio source cannot carry audio: {source_id}")
            active_clips: list[tuple[str, float]] = []
            for track in loaded.document.tracks:
                if track.muted:
                    continue
                for clip in track.clips:
                    if getattr(clip, "source_id", None) != source_id:
                        continue
                    carries_audio = track.kind == "audio" or (
                        track.kind == "video" and bool(getattr(clip, "audio_enabled", False))
                    )
                    if carries_audio:
                        active_clips.append((track.id, clip.duration_seconds))
            if not active_clips:
                raise ValueError(
                    f"Required audio source is not active on the production timeline: {source_id}"
                )
            inspections.append(
                ProductionAudioSourceInspection(
                    source_id=source_id,
                    active_clip_count=len(active_clips),
                    active_duration_seconds=sum(duration for _track_id, duration in active_clips),
                    track_ids=list(dict.fromkeys(track_id for track_id, _duration in active_clips)),
                )
            )
        return inspections
