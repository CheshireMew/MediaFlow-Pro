from __future__ import annotations

import re
import struct
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Literal

from mediaflow.atomic_file import atomic_write_bytes, atomic_write_text
from mediaflow.domain.enums import AssetKind, ClipMediaKind, ColorMode, TrackKind
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.model_base import new_id
from mediaflow.domain.portable_timeline import (
    LoadedPortableTimeline,
    PortableCaptionClip,
    PortableFade,
    PortableFreezeClip,
    PortableMediaClip,
    PortablePlacement,
    PortableSubtitleStyle,
    PortableTimelineDocument,
    PortableTimelineTrack,
    load_portable_timeline,
)
from mediaflow.domain.project import Asset, ProjectProfile
from mediaflow.domain.srt_time import format_srt_timestamp
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames
from mediaflow.domain.timeline import (
    Clip,
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    TimelineMarker,
    TimelineState,
    Track,
)

from .asset_service import AssetService
from .ports import PortableTimelineImportDocuments
from .subtitle_acquisition import SubtitleAcquisitionService
from .timeline_editor import TimelineEditor


class PortableTimelineImportService:
    """Import the product-independent visual-multimedia timeline as native edits."""

    def __init__(
        self,
        repository: PortableTimelineImportDocuments,
        assets: AssetService,
        subtitles: SubtitleAcquisitionService,
        timeline_provider: Callable[[str], TimelineEditor],
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.subtitles = subtitles
        self.timeline_provider = timeline_provider

    def inspect(self, path: str | Path) -> LoadedPortableTimeline:
        loaded = load_portable_timeline(path)
        self._project_profile(loaded.document)
        return loaded

    def import_timeline(
        self,
        path: str | Path,
        *,
        sequence_id: str,
    ) -> tuple[LoadedPortableTimeline, TimelineState, dict[str, Asset], list[str]]:
        loaded = self.inspect(path)
        editor = self.timeline_provider(sequence_id)
        existing_documents = self.repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
        if editor.state.tracks or editor.state.clips or existing_documents:
            raise ValueError(
                "Portable timelines can only be imported into an empty sequence; "
                "edit an imported project through its native timeline operations"
            )

        editor.set_sequence_profile(self._project_profile(loaded.document))
        imported_assets = self._import_sources(loaded)
        background = self._import_background(loaded)
        tracks, clips, subtitle_groups = self._native_timeline(
            loaded.document,
            imported_assets,
            background,
            editor.state,
        )
        destination = editor.state.model_copy(
            update={
                "tracks": tracks,
                "clips": clips,
                "markers": [
                    TimelineMarker(
                        id=marker.id,
                        sequence_id=sequence_id,
                        frame=seconds_to_frames(
                            marker.time_seconds,
                            editor.state.sequence.profile.fps_numerator,
                            editor.state.sequence.profile.fps_denominator,
                        ),
                        name=(
                            marker.label
                            if not marker.note.strip()
                            else f"{marker.label} — {marker.note.strip()}"
                        ),
                    )
                    for marker in loaded.document.markers
                ],
            }
        )
        state = editor.replace_contents(destination)
        subtitle_document_ids = self._import_subtitles(
            loaded,
            state,
            subtitle_groups,
        )
        return loaded, editor.reload(), imported_assets, subtitle_document_ids

    @staticmethod
    def _project_profile(document: PortableTimelineDocument) -> ProjectProfile:
        profile = document.profile
        if profile.sample_rate != 48_000:
            raise ValueError("MediaFlow Pro projects use a 48 kHz audio clock")
        fps = Fraction(str(profile.frame_rate)).limit_denominator(100_000)
        return ProjectProfile(
            width=profile.width,
            height=profile.height,
            fps_numerator=fps.numerator,
            fps_denominator=fps.denominator,
            color_mode=ColorMode.SDR_BT709,
            bit_depth=8,
            audio_sample_rate=48_000,
            audio_channels=1 if profile.channel_layout == "mono" else 2,
        )

    def _import_sources(self, loaded: LoadedPortableTimeline) -> dict[str, Asset]:
        imported: dict[str, Asset] = {}
        expected = {
            "video": AssetKind.VIDEO,
            "web-render": AssetKind.VIDEO,
            "audio": AssetKind.AUDIO,
            "image": AssetKind.IMAGE,
        }
        for source in loaded.document.sources:
            imported[source.id] = self.assets.import_external(
                loaded.sources[source.id],
                expected_kind=expected[source.kind],
            )
        return imported

    def _import_background(self, loaded: LoadedPortableTimeline) -> Asset:
        value = loaded.document.profile.background.lstrip("#")
        red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
        row = bytes((blue, green, red)) * 2 + b"\x00\x00"
        pixels = row * 2
        file_size = 14 + 40 + len(pixels)
        bitmap = b"".join(
            (
                b"BM",
                struct.pack("<IHHI", file_size, 0, 0, 54),
                struct.pack(
                    "<IIIHHIIIIII",
                    40,
                    2,
                    2,
                    1,
                    24,
                    0,
                    len(pixels),
                    2835,
                    2835,
                    0,
                    0,
                ),
                pixels,
            )
        )
        destination = (
            self.repository.project_dir
            / "generated"
            / "portable-timeline"
            / loaded.sha256[:16]
            / "background.bmp"
        )
        atomic_write_bytes(destination, bitmap)
        return self.assets.import_external(destination, expected_kind=AssetKind.IMAGE)

    def _native_timeline(
        self,
        document: PortableTimelineDocument,
        imported_assets: dict[str, Asset],
        background: Asset,
        source_state: TimelineState,
    ) -> tuple[
        list[Track],
        list[Clip],
        list[tuple[Track, PortableTimelineTrack, str, list[PortableCaptionClip]]],
    ]:
        profile = source_state.sequence.profile
        tracks: list[Track] = []
        clips: list[Clip] = []
        subtitle_groups: list[tuple[Track, PortableTimelineTrack, str, list[PortableCaptionClip]]] = []

        background_track = Track(
            id=f"portable-background-{new_id()}",
            sequence_id=source_state.sequence.id,
            name="时间线背景",
            kind=TrackKind.VIDEO,
            position=0,
        )
        tracks.append(background_track)
        clips.append(
            Clip(
                id=f"portable-background-{new_id()}",
                track_id=background_track.id,
                asset_id=background.id,
                timeline_start=0,
                source_in=0,
                duration=max(
                    1,
                    seconds_to_frames(
                        document.duration_seconds,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                ),
                media_kind=ClipMediaKind.VIDEO_ONLY,
            )
        )

        styles = {style.id: style for style in document.subtitle_styles}
        for portable_track in document.tracks:
            if portable_track.kind == "subtitle":
                grouped: dict[str, list[PortableCaptionClip]] = {}
                for clip in portable_track.clips:
                    assert isinstance(clip, PortableCaptionClip)
                    grouped.setdefault(clip.style_id, []).append(clip)
                for style_id, captions in grouped.items():
                    track = Track(
                        id=(portable_track.id if len(grouped) == 1 else f"{portable_track.id}--{style_id}"),
                        sequence_id=source_state.sequence.id,
                        name=(
                            portable_track.name
                            if len(grouped) == 1
                            else f"{portable_track.name} · {style_id}"
                        ),
                        kind=TrackKind.SUBTITLE,
                        position=len(tracks),
                        muted=portable_track.muted,
                        subtitle_style=self._subtitle_style(styles[style_id], profile.height),
                    )
                    tracks.append(track)
                    subtitle_groups.append((track, portable_track, style_id, captions))
                continue

            track = Track(
                id=portable_track.id,
                sequence_id=source_state.sequence.id,
                name=portable_track.name,
                kind=(TrackKind.VIDEO if portable_track.kind == "video" else TrackKind.AUDIO),
                position=len(tracks),
                muted=portable_track.muted,
            )
            if portable_track.kind == "video" and any(
                isinstance(clip, PortableMediaClip) and clip.audio_enabled for clip in portable_track.clips
            ):
                linked = Track(
                    id=f"{portable_track.id}--linked-audio",
                    sequence_id=source_state.sequence.id,
                    name=f"{portable_track.name} · 关联音频",
                    kind=TrackKind.AUDIO,
                    position=len(tracks) + 1,
                    muted=portable_track.muted,
                )
                track = track.model_copy(update={"linked_audio_track_id": linked.id})
                tracks.extend((track, linked))
            else:
                tracks.append(track)
            for portable_clip in portable_track.clips:
                assert isinstance(portable_clip, (PortableMediaClip, PortableFreezeClip))
                clips.append(
                    self._native_clip(
                        portable_clip,
                        track,
                        imported_assets[portable_clip.source_id],
                        profile,
                    )
                )

        tracks = [track.model_copy(update={"position": position}) for position, track in enumerate(tracks)]
        return tracks, clips, subtitle_groups

    def _native_clip(
        self,
        source: PortableMediaClip | PortableFreezeClip,
        track: Track,
        asset: Asset,
        profile: ProjectProfile,
    ) -> Clip:
        timeline_start = seconds_to_frames(
            source.timeline_start_seconds,
            profile.fps_numerator,
            profile.fps_denominator,
        )
        duration = max(
            1,
            seconds_to_frames(
                source.duration_seconds,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
        )
        if isinstance(source, PortableFreezeClip):
            source_in = seconds_to_frames(
                source.source_time_seconds,
                profile.fps_numerator,
                profile.fps_denominator,
            )
            speed = Fraction(1)
            media_kind = ClipMediaKind.VIDEO_ONLY
            freeze_source_frame = source_in
            audio = ClipAudio()
        else:
            source_in = seconds_to_frames(
                source.source_in_seconds,
                profile.fps_numerator,
                profile.fps_denominator,
            )
            speed = Fraction(str(source.speed)).limit_denominator(10_000)
            if not Fraction(1, 4) <= speed <= 4:
                raise ValueError(f"Portable clip speed is outside MediaFlow limits: {source.id}")
            if track.kind == TrackKind.AUDIO:
                media_kind = ClipMediaKind.AUDIO_ONLY
            elif source.audio_enabled:
                if not asset.metadata.has_audio:
                    raise ValueError(f"Portable clip requests missing source audio: {source.id}")
                media_kind = ClipMediaKind.LINKED_AV
            else:
                media_kind = ClipMediaKind.VIDEO_ONLY
            freeze_source_frame = None
            audio = ClipAudio(
                gain_db=source.gain_db,
                fade_in_frames=self._fade_frames(source.fade_in, duration, profile),
                fade_out_frames=self._fade_frames(source.fade_out, duration, profile),
            )
        transform = self._placement_transform(
            source.placement,
            asset,
            profile,
            opacity=source.opacity,
        )
        keyframes = (
            []
            if track.kind == TrackKind.AUDIO
            else self._opacity_keyframes(source, transform, duration, profile)
        )
        clip = Clip(
            id=source.id,
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=timeline_start,
            source_in=source_in,
            duration=duration,
            media_kind=media_kind,
            speed_numerator=speed.numerator,
            speed_denominator=speed.denominator,
            freeze_source_frame=freeze_source_frame,
            transform=transform,
            transform_keyframes=keyframes,
            audio=audio,
        )
        clip.validate_source_range(asset.kind, asset.metadata.duration_frames)
        return clip

    @staticmethod
    def _fade_frames(
        fade: PortableFade | None,
        duration: int,
        profile: ProjectProfile,
    ) -> int:
        if fade is None or fade.kind != "fade" or fade.duration_seconds <= 0:
            return 0
        return min(
            duration,
            max(
                1,
                seconds_to_frames(
                    fade.duration_seconds,
                    profile.fps_numerator,
                    profile.fps_denominator,
                ),
            ),
        )

    def _opacity_keyframes(
        self,
        source: PortableMediaClip | PortableFreezeClip,
        transform: ClipTransform,
        duration: int,
        profile: ProjectProfile,
    ) -> list[ClipTransformKeyframe]:
        fade_in = self._fade_frames(source.fade_in, duration, profile)
        fade_out = self._fade_frames(source.fade_out, duration, profile)
        if not fade_in and not fade_out:
            return []
        opacity = transform.opacity
        points: dict[int, float] = {}
        if fade_in:
            points[0] = 0.0
            points[min(duration - 1, fade_in)] = opacity
        if fade_out:
            points[max(0, duration - fade_out - 1)] = opacity
            points[duration - 1] = 0.0
        return [
            ClipTransformKeyframe(
                timeline_offset=frame,
                transform=transform.model_copy(update={"opacity": value}),
            )
            for frame, value in sorted(points.items())
        ]

    @staticmethod
    def _placement_transform(
        placement: PortablePlacement | None,
        asset: Asset,
        profile: ProjectProfile,
        *,
        opacity: float,
    ) -> ClipTransform:
        value = placement or PortablePlacement()
        target_width = float(value.width or profile.width)
        target_height = float(value.height or profile.height)
        target_x = float(value.x if value.x is not None else (profile.width - target_width) / 2)
        target_y = float(value.y if value.y is not None else (profile.height - target_height) / 2)
        source_width = float(asset.metadata.width or target_width)
        source_height = float(asset.metadata.height or target_height)
        crop_left = crop_right = crop_top = crop_bottom = 0.0
        if value.fit == "stretch":
            output_width, output_height = target_width, target_height
            output_x, output_y = target_x, target_y
        elif value.fit == "contain":
            scale = min(target_width / source_width, target_height / source_height)
            output_width = source_width * scale
            output_height = source_height * scale
            output_x = target_x + (target_width - output_width) / 2
            output_y = target_y + (target_height - output_height) / 2
        else:
            source_aspect = source_width / source_height
            target_aspect = target_width / target_height
            if source_aspect > target_aspect:
                kept = target_aspect / source_aspect
                crop_left = crop_right = (1.0 - kept) / 2
            elif source_aspect < target_aspect:
                kept = source_aspect / target_aspect
                crop_top = crop_bottom = (1.0 - kept) / 2
            output_width, output_height = target_width, target_height
            output_x, output_y = target_x, target_y
        return ClipTransform(
            x=output_x / profile.width * 100.0,
            y=output_y / profile.height * 100.0,
            scale_x=output_width / profile.width,
            scale_y=output_height / profile.height,
            crop_left=crop_left,
            crop_right=crop_right,
            crop_top=crop_top,
            crop_bottom=crop_bottom,
            opacity=opacity,
        )

    @staticmethod
    def _subtitle_style(style: PortableSubtitleStyle, height: int) -> SubtitleStyle:
        vertical = (style.alignment - 1) // 3
        horizontal = (style.alignment - 1) % 3
        multiline_alignment: Literal["top", "center", "bottom"]
        if vertical == 0:
            position_y = 1.0 - style.margin_vertical / height
            multiline_alignment = "bottom"
        elif vertical == 1:
            position_y = 0.5
            multiline_alignment = "center"
        else:
            position_y = style.margin_vertical / height
            multiline_alignment = "top"
        return SubtitleStyle(
            font_family=style.font_family,
            font_size=max(8, round(style.font_size * 540 / height)),
            font_color=style.primary_color,
            bold=style.bold,
            italic=style.italic,
            outline_size=max(0, round(style.outline_width * 540 / height)),
            outline_color=style.outline_color,
            position_x=(0.1, 0.5, 0.9)[horizontal],
            position_y=max(0.0, min(1.0, position_y)),
            alignment=("left", "center", "right")[horizontal],
            multiline_alignment=multiline_alignment,
        )

    def _import_subtitles(
        self,
        loaded: LoadedPortableTimeline,
        state: TimelineState,
        groups: list[tuple[Track, PortableTimelineTrack, str, list[PortableCaptionClip]]],
    ) -> list[str]:
        document_ids: list[str] = []
        profile = state.sequence.profile
        target_root = (
            self.repository.project_dir / "generated" / "portable-timeline" / loaded.sha256[:16] / "subtitles"
        )
        for track, portable_track, style_id, captions in groups:
            file_name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{portable_track.id}-{style_id}").strip("-")
            target = target_root / f"{file_name or new_id()}.srt"
            content: list[str] = []
            for index, caption in enumerate(captions, start=1):
                start_frame = seconds_to_frames(
                    caption.timeline_start_seconds,
                    profile.fps_numerator,
                    profile.fps_denominator,
                )
                end_frame = max(
                    start_frame + 1,
                    seconds_to_frames(
                        caption.timeline_start_seconds + caption.duration_seconds,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                )
                start_timestamp = format_srt_timestamp(
                    frames_to_seconds(
                        start_frame,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    )
                )
                end_timestamp = format_srt_timestamp(
                    frames_to_seconds(
                        end_frame,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    )
                )
                content.extend(
                    (
                        str(index),
                        f"{start_timestamp} --> {end_timestamp}",
                        caption.text.strip(),
                        "",
                    )
                )
            atomic_write_text(target, "\n".join(content))
            languages = {
                caption.language.strip()
                for caption in captions
                if caption.language and caption.language.strip()
            }
            document = self.subtitles.import_subtitle_file(
                target,
                self.assets,
                language=next(iter(languages)) if len(languages) == 1 else "und",
            )
            self.repository.subtitles.place_subtitle_document(
                document.id,
                track.id,
                follow_clips=False,
            )
            document_ids.append(document.id)
        return document_ids
