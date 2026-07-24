from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.audio import (
    AudioBus,
    AudioEffect,
)
from mediaflow.domain.enums import AssetKind, AudioEffectKind, ColorMode, TrackKind, TransitionKind
from mediaflow.domain.exports import (
    SubtitleStyle,
    WatermarkOverlay,
)
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import audio_clips_for_track, select_audible_sequence_audio
from mediaflow.domain.timeline import (
    Clip,
    ClipTransform,
    TimelineState,
    Track,
    Transition,
)
from mediaflow.infrastructure.web_render_service import WebRenderCache


@dataclass(frozen=True, slots=True)
class MltDocument:
    xml: str
    duration_frames: int
    source_paths: tuple[Path, ...]


class TimelineCompiler:
    """Compile the canonical project timeline into an MLT graph."""

    TRANSITION_SERVICES = {
        TransitionKind.FADE: "luma",
        TransitionKind.DISSOLVE: "luma",
        TransitionKind.FADE_BLACK: "luma",
        TransitionKind.WIPE_LEFT: "luma",
        TransitionKind.WIPE_RIGHT: "luma",
        TransitionKind.SLIDE_LEFT: "affine",
        TransitionKind.SLIDE_RIGHT: "affine",
        TransitionKind.ZOOM: "affine",
    }

    def __init__(self, repository: TimelineCompilationDocuments):
        self.repository = repository

    def compile(
        self,
        state: TimelineState,
        *,
        use_proxies: bool = False,
        subtitle_track_id: str | None = None,
        subtitle_style: SubtitleStyle | None = None,
        watermark: WatermarkOverlay | None = None,
        native_preview: bool = False,
        prefer_sdr_preview_proxy: bool = False,
    ) -> MltDocument:
        profile = state.sequence.profile
        duration = max(1, state.duration_frames)
        root = ET.Element(
            "mlt",
            {
                "LC_NUMERIC": "C",
                "version": "7.40.0",
                "title": state.sequence.name,
                "producer": "tractor0",
            },
        )
        ET.SubElement(
            root,
            "profile",
            {
                "description": "MediaFlow Pro",
                "width": str(profile.width),
                "height": str(profile.height),
                "progressive": "1",
                "sample_aspect_num": "1",
                "sample_aspect_den": "1",
                "display_aspect_num": str(profile.width),
                "display_aspect_den": str(profile.height),
                "frame_rate_num": str(profile.fps_numerator),
                "frame_rate_den": str(profile.fps_denominator),
                "colorspace": "2020" if profile.color_mode == ColorMode.HDR10_BT2020_PQ else "709",
            },
        )

        assets = {asset.id: asset for asset in self.repository.list_assets()}
        outgoing_transitions = {item.left_clip_id: item for item in state.transitions}
        source_paths: list[Path] = []
        for clip in state.clips:
            asset = assets.get(clip.asset_id)
            if asset is None:
                raise ValueError(f"Timeline references unknown asset: {clip.asset_id}")
            source = (
                WebRenderCache(self.repository).target(state, clip, asset).path
                if asset.kind == AssetKind.WEB
                else self._source_path(
                    asset,
                    use_proxies=use_proxies,
                    prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
                )
            )
            if not source.is_file():
                raise FileNotFoundError(source)
            source_paths.append(source)
            self._append_producer(
                root,
                clip,
                asset,
                source,
                profile.color_mode,
                transition_tail_frames=(
                    self._transition_parts(outgoing_transitions[clip.id])[1]
                    if clip.id in outgoing_transitions
                    else 0
                ),
                native_preview=native_preview,
            )

        clips = {clip.id: clip for clip in state.clips}
        for transition in state.transitions:
            self._append_transition_tractor(root, transition, clips, assets)

        ordered_tracks = sorted(state.tracks, key=lambda track: (track.position, track.id))
        for track in ordered_tracks:
            self._append_playlist(root, track, state, assets)

        watermark_track = self._append_watermark_playlist(
            root,
            state,
            watermark,
            duration,
        )
        if watermark_track is not None:
            source_paths.append(watermark_track[2])

        audio_root_id = self._append_audio_graph(
            root,
            state,
            ordered_tracks,
            duration,
            assets,
            use_proxies=use_proxies,
            prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
        )

        tractor = ET.SubElement(
            root,
            "tractor",
            {"id": "tractor0", "in": "0", "out": str(max(0, duration - 1))},
        )
        self._property(tractor, "global_feed", "1")
        video_tracks = [track for track in ordered_tracks if track.kind == TrackKind.VIDEO and track.enabled]
        for track in video_tracks:
            ET.SubElement(
                tractor,
                "track",
                {"producer": self._playlist_id(track.id), "hide": "audio"},
            )
        if watermark_track is not None:
            ET.SubElement(
                tractor,
                "track",
                {"producer": watermark_track[0], "hide": "audio"},
            )
        if audio_root_id:
            ET.SubElement(tractor, "track", {"producer": audio_root_id, "hide": "video"})
        self._append_layer_compositors(tractor, video_tracks, duration, native_preview=native_preview)
        if watermark_track is not None:
            self._append_watermark_compositor(
                tractor,
                b_track=len(video_tracks),
                duration=duration,
                geometry=watermark_track[1],
                native_preview=native_preview,
            )
        if not native_preview:
            self._append_subtitle_filters(
                tractor,
                state,
                ordered_tracks,
                subtitle_track_id,
                subtitle_style,
            )

        ET.indent(root, space="  ")
        xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
        return MltDocument(xml=xml, duration_frames=duration, source_paths=tuple(dict.fromkeys(source_paths)))

    def write(
        self,
        state: TimelineState,
        destination: str | Path,
        *,
        use_proxies: bool = False,
        subtitle_track_id: str | None = None,
        subtitle_style: SubtitleStyle | None = None,
        watermark: WatermarkOverlay | None = None,
        native_preview: bool = False,
        prefer_sdr_preview_proxy: bool = False,
    ) -> MltDocument:
        document = self.compile(
            state,
            use_proxies=use_proxies,
            subtitle_track_id=subtitle_track_id,
            subtitle_style=subtitle_style,
            watermark=watermark,
            native_preview=native_preview,
            prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
        )
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(document.xml, encoding="utf-8")
        temporary.replace(path)
        return document

    def _source_path(
        self,
        asset: Asset,
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool = False,
    ) -> Path:
        if use_proxies and prefer_sdr_preview_proxy and asset.sdr_preview_proxy_path:
            proxy = Path(asset.sdr_preview_proxy_path)
            return (
                (self.repository.project_dir / proxy).resolve()
                if not proxy.is_absolute()
                else proxy.resolve()
            )
        if use_proxies and asset.proxy_path:
            proxy = Path(asset.proxy_path)
            return (
                (self.repository.project_dir / proxy).resolve()
                if not proxy.is_absolute()
                else proxy.resolve()
            )
        return self.repository.resolve_asset_path(asset)

    def _append_producer(
        self,
        root: ET.Element,
        clip: Clip,
        asset: Asset,
        source: Path,
        project_color_mode: ColorMode,
        *,
        transition_tail_frames: int,
        native_preview: bool,
    ) -> None:
        speed = clip.speed_numerator / clip.speed_denominator
        service = (
            "qimage"
            if asset.kind == AssetKind.IMAGE or (asset.kind == AssetKind.WEB and source.suffix == ".png")
            else "avformat"
        )
        resource = str(source)
        if speed != 1.0:
            service = "timewarp"
            resource = f"{speed}:{source}"
        producer = ET.SubElement(
            root,
            "producer",
            {"id": self._producer_id(clip.id)},
        )
        self._property(producer, "mlt_service", service)
        self._property(producer, "resource", resource)
        producer_start, natural_length = self._producer_timing(clip, asset)
        required_length = producer_start + clip.duration + transition_tail_frames
        producer_length = max(natural_length, required_length)
        self._property(producer, "length", str(producer_length))
        self._property(producer, "eof", "pause")
        if service == "timewarp":
            self._property(producer, "warp_speed", str(speed))
            self._property(producer, "warp_resource", str(source))
            self._property(producer, "warp_pitch", "1" if clip.pitch_compensation else "0")
        if service == "qimage":
            self._property(producer, "ttl", "1")
        elif transition_tail_frames and required_length > natural_length:
            freeze = ET.SubElement(
                producer,
                "filter",
                {
                    "id": f"transition_hold_{clip.id}",
                    "in": "0",
                    "out": str(producer_length - 1),
                },
            )
            self._property(freeze, "mlt_service", "freeze")
            self._property(freeze, "frame", str(max(0, natural_length - 1)))
            self._property(freeze, "freeze_after", "1")
        if asset.kind in {AssetKind.VIDEO, AssetKind.IMAGE, AssetKind.WEB}:
            self._append_color_pipeline(producer, asset, project_color_mode)
        self._append_clip_filters(
            producer,
            clip,
            asset,
            producer_start=producer_start,
            native_preview=native_preview,
        )

    def _append_color_pipeline(
        self,
        producer: ET.Element,
        asset: Asset,
        project_color_mode: ColorMode,
    ) -> None:
        metadata = asset.metadata
        token = str(producer.get("id") or asset.id)
        source_hdr = metadata.color_primaries == "bt2020" and metadata.color_transfer in {
            "smpte2084",
            "arib-std-b67",
        }
        target_hdr = project_color_mode == ColorMode.HDR10_BT2020_PQ
        if source_hdr == target_hdr:
            return
        if target_hdr:
            self._append_filter(
                producer,
                f"color_sdr_to_hdr_{token}",
                "avfilter.zscale",
                {
                    "av.primariesin": metadata.color_primaries or "bt709",
                    "av.transferin": metadata.color_transfer or "bt709",
                    "av.matrixin": metadata.color_space or "bt709",
                    "av.primaries": "bt2020",
                    "av.transfer": "smpte2084",
                    "av.matrix": "bt2020nc",
                    "av.range": "tv",
                    "av.npl": 203.0,
                    "av.dither": "error_diffusion",
                },
            )
            return

        self._append_filter(
            producer,
            f"color_hdr_linear_{token}",
            "avfilter.zscale",
            {
                "av.primariesin": "bt2020",
                "av.transferin": metadata.color_transfer or "smpte2084",
                "av.matrixin": metadata.color_space or "bt2020nc",
                "av.transfer": "linear",
                "av.npl": 100.0,
            },
        )
        self._append_filter(
            producer,
            f"color_hdr_tonemap_{token}",
            "avfilter.tonemap",
            {"av.tonemap": "mobius", "av.param": 0.3, "av.desat": 2.0, "av.peak": 10.0},
        )
        self._append_filter(
            producer,
            f"color_hdr_to_sdr_{token}",
            "avfilter.zscale",
            {
                "av.primaries": "bt709",
                "av.transfer": "bt709",
                "av.matrix": "bt709",
                "av.range": "tv",
                "av.dither": "error_diffusion",
            },
        )

    def _append_clip_filters(
        self,
        producer: ET.Element,
        clip: Clip,
        asset: Asset,
        *,
        producer_start: int,
        native_preview: bool,
    ) -> None:
        transform = clip.transform
        if any(
            value > 0.0
            for value in (
                transform.crop_left,
                transform.crop_top,
                transform.crop_right,
                transform.crop_bottom,
            )
        ):
            crop = ET.SubElement(
                producer,
                "filter",
                {
                    "id": f"crop_{clip.id}",
                    "in": str(producer_start),
                    "out": str(producer_start + clip.duration - 1),
                },
            )
            self._property(crop, "mlt_service", "crop")
            self._property(crop, "active", "1")
            self._property(crop, "left", str(round(transform.crop_left * (asset.metadata.width or 1))))
            self._property(crop, "top", str(round(transform.crop_top * (asset.metadata.height or 1))))
            self._property(crop, "right", str(round(transform.crop_right * (asset.metadata.width or 1))))
            self._property(crop, "bottom", str(round(transform.crop_bottom * (asset.metadata.height or 1))))
        if (
            transform.x
            or transform.y
            or transform.scale_x != 1.0
            or transform.scale_y != 1.0
            or transform.rotation
            or transform.opacity != 1.0
            or clip.transform_keyframes
        ):
            filter_element = ET.SubElement(
                producer,
                "filter",
                {
                    "id": f"transform_{clip.id}",
                    "in": str(producer_start),
                    "out": str(producer_start + clip.duration - 1),
                },
            )
            self._property(filter_element, "mlt_service", "affine" if native_preview else "qtblend")
            def rect_value(value: ClipTransform) -> str:
                width = max(0.01, value.scale_x) * 100.0
                height = max(0.01, value.scale_y) * 100.0
                return (
                    f"{value.x:g}%/{value.y:g}%:{width:g}%x{height:g}%:"
                    f"{value.opacity * 100:g}%"
                )

            rect = rect_value(transform)
            rotation = f"{transform.rotation:g}"
            if clip.transform_keyframes:
                speed = Fraction(abs(clip.speed_numerator), clip.speed_denominator)
                points: dict[int, ClipTransform] = {producer_start: transform}
                for keyframe in clip.transform_keyframes:
                    source_delta = (
                        keyframe.source_frame - clip.source_in
                        if clip.speed_numerator > 0
                        else clip.source_in - keyframe.source_frame
                    )
                    if source_delta < 0:
                        continue
                    local_frame = self._round_fraction(Fraction(source_delta) / speed)
                    if 0 <= local_frame < clip.duration:
                        points[producer_start + local_frame] = keyframe.transform
                final_value = points[max(points)]
                points[producer_start + clip.duration - 1] = final_value
                rect = ";".join(
                    f"{frame}={rect_value(value)}"
                    for frame, value in sorted(points.items())
                )
                rotation = ";".join(
                    f"{frame}={value.rotation:g}"
                    for frame, value in sorted(points.items())
                )
            if native_preview:
                self._property(filter_element, "transition.rect", rect)
                self._property(filter_element, "transition.rotate_z", rotation)
            else:
                self._property(filter_element, "rect", rect)
                self._property(filter_element, "rotation", rotation)
        self._append_clip_audio_filters(producer, clip, producer_start=producer_start)
        if asset.kind in {AssetKind.IMAGE, AssetKind.WEB}:
            self._property(producer, "set.test_audio", "1")

    def _append_clip_audio_filters(
        self,
        producer: ET.Element,
        clip: Clip,
        *,
        producer_start: int,
    ) -> None:
        if clip.audio.gain_db != 0.0 or clip.audio.fade_in_frames or clip.audio.fade_out_frames:
            volume = ET.SubElement(
                producer,
                "filter",
                {
                    "id": f"volume_{clip.id}",
                    "in": str(producer_start),
                    "out": str(producer_start + clip.duration - 1),
                },
            )
            self._property(volume, "mlt_service", "volume")
            gain = clip.audio.gain_db
            points: list[tuple[int, float]] = []
            if clip.audio.fade_in_frames:
                points.extend([(0, -60.0), (min(clip.duration - 1, clip.audio.fade_in_frames), gain)])
            else:
                points.append((0, gain))
            if clip.audio.fade_out_frames:
                points.extend(
                    [
                        (max(0, clip.duration - clip.audio.fade_out_frames - 1), gain),
                        (clip.duration - 1, -60.0),
                    ]
                )
            else:
                points.append((clip.duration - 1, gain))
            animation = ";".join(f"{frame}={level:g}dB" for frame, level in dict(points).items())
            self._property(volume, "level", animation)
        if clip.audio.pan != 0.0:
            panner = ET.SubElement(
                producer,
                "filter",
                {
                    "id": f"panner_{clip.id}",
                    "in": str(producer_start),
                    "out": str(producer_start + clip.duration - 1),
                },
            )
            self._property(panner, "mlt_service", "panner")
            self._property(panner, "start", str(clip.audio.pan))

    def _append_playlist(
        self,
        root: ET.Element,
        track: Track,
        state: TimelineState,
        assets: dict[str, Asset],
    ) -> None:
        playlist = ET.SubElement(root, "playlist", {"id": self._playlist_id(track.id)})
        self._property(playlist, "mediaflow:track_name", track.name)
        incoming = {item.right_clip_id: item for item in state.transitions if item.track_id == track.id}
        outgoing = {item.left_clip_id: item for item in state.transitions if item.track_id == track.id}
        cursor = 0
        for clip in state.clips_for_track(track.id):
            incoming_after = self._transition_parts(incoming[clip.id])[1] if clip.id in incoming else 0
            outgoing_before = self._transition_parts(outgoing[clip.id])[0] if clip.id in outgoing else 0
            visible_start = clip.timeline_start + incoming_after
            visible_end = clip.timeline_end - outgoing_before
            if visible_start > cursor:
                ET.SubElement(playlist, "blank", {"length": str(visible_start - cursor)})
            if visible_end > visible_start:
                producer_in = self._producer_frame(
                    clip,
                    assets[clip.asset_id],
                    incoming_after,
                )
                ET.SubElement(
                    playlist,
                    "entry",
                    {
                        "producer": self._producer_id(clip.id),
                        "in": str(producer_in),
                        "out": str(producer_in + visible_end - visible_start - 1),
                    },
                )
                cursor = visible_end
            if clip.id in outgoing:
                transition = outgoing[clip.id]
                ET.SubElement(
                    playlist,
                    "entry",
                    {
                        "producer": self._transition_id(transition.id),
                        "in": "0",
                        "out": str(transition.duration - 1),
                    },
                )
                cursor += transition.duration

    def _append_layer_compositors(
        self,
        tractor: ET.Element,
        tracks: list[Track],
        duration: int,
        *,
        native_preview: bool,
    ) -> None:
        for b_track in range(1, len(tracks)):
            transition = ET.SubElement(
                tractor,
                "transition",
                {"id": f"composite_{b_track}", "in": "0", "out": str(max(0, duration - 1))},
            )
            self._property(transition, "a_track", "0")
            self._property(transition, "b_track", str(b_track))
            self._property(transition, "mlt_service", "composite" if native_preview else "qtblend")
            self._property(transition, "always_active", "1")

    def _append_watermark_playlist(
        self,
        root: ET.Element,
        state: TimelineState,
        watermark: WatermarkOverlay | None,
        duration: int,
    ) -> tuple[str, str, Path] | None:
        if watermark is None or not watermark.enabled or not watermark.asset_id:
            return None
        asset = self.repository.get_asset(watermark.asset_id)
        if asset.kind != AssetKind.IMAGE:
            raise ValueError("Watermark asset must be an image")
        source = self.repository.resolve_asset_path(asset)
        if not source.is_file():
            raise FileNotFoundError(source)
        producer_id = f"watermark_producer_{asset.id}"
        playlist_id = f"watermark_playlist_{asset.id}"
        producer = ET.SubElement(root, "producer", {"id": producer_id})
        self._property(producer, "mlt_service", "qimage")
        self._property(producer, "resource", str(source))
        self._property(producer, "length", str(duration))
        self._property(producer, "ttl", "1")
        self._property(producer, "eof", "pause")
        self._property(producer, "set.test_audio", "1")
        self._append_color_pipeline(producer, asset, state.sequence.profile.color_mode)
        playlist = ET.SubElement(root, "playlist", {"id": playlist_id})
        ET.SubElement(
            playlist,
            "entry",
            {"producer": producer_id, "in": "0", "out": str(max(0, duration - 1))},
        )
        return playlist_id, self._watermark_geometry(state, asset, watermark), source

    def _append_watermark_compositor(
        self,
        tractor: ET.Element,
        *,
        b_track: int,
        duration: int,
        geometry: str,
        native_preview: bool,
    ) -> None:
        transition = ET.SubElement(
            tractor,
            "transition",
            {"id": "watermark_composite", "in": "0", "out": str(max(0, duration - 1))},
        )
        self._property(transition, "a_track", "0")
        self._property(transition, "b_track", str(b_track))
        self._property(transition, "mlt_service", "affine" if native_preview else "qtblend")
        self._property(transition, "always_active", "1")
        self._property(transition, "transition.rect" if native_preview else "rect", geometry)

    @staticmethod
    def _watermark_geometry(
        state: TimelineState,
        asset: Asset,
        watermark: WatermarkOverlay,
    ) -> str:
        profile = state.sequence.profile
        width_ratio = watermark.width_ratio
        source_width = asset.metadata.width or profile.width
        source_height = asset.metadata.height or profile.height
        height_ratio = min(
            1.0,
            profile.width * width_ratio * source_height / (source_width * profile.height),
        )
        margin_x = 0.045 if profile.height > profile.width else 0.03
        margin_y = 0.035 if profile.height > profile.width else 0.05
        if watermark.position_x is not None and watermark.position_y is not None:
            center_x = watermark.position_x
            center_y = watermark.position_y
        else:
            position = watermark.position
            vertical = position[0] if len(position) == 2 and position[0] in {"T", "B"} else "C"
            horizontal = (
                position[1]
                if len(position) == 2 and position[1] in {"L", "R"}
                else position[0]
                if len(position) == 2 and position[0] in {"L", "R"}
                else "C"
            )
            center_x = {
                "L": margin_x + width_ratio / 2,
                "C": 0.5,
                "R": 1.0 - margin_x - width_ratio / 2,
            }[horizontal]
            center_y = {
                "T": margin_y + height_ratio / 2,
                "C": 0.5,
                "B": 1.0 - margin_y - height_ratio / 2,
            }[vertical]
        x = max(0.0, min(1.0 - width_ratio, center_x - width_ratio / 2))
        y = max(0.0, min(1.0 - height_ratio, center_y - height_ratio / 2))
        return (
            f"{x * 100:g}%/{y * 100:g}%:"
            f"{width_ratio * 100:g}%x{height_ratio * 100:g}%:{watermark.opacity * 100:g}%"
        )

    def _append_subtitle_filters(
        self,
        tractor: ET.Element,
        state: TimelineState,
        tracks: list[Track],
        requested_track_id: str | None,
        style: SubtitleStyle | None,
    ) -> None:
        if not requested_track_id:
            return
        subtitle_tracks = [track for track in tracks if track.kind == TrackKind.SUBTITLE and track.enabled]
        subtitle_tracks = [track for track in subtitle_tracks if track.id == requested_track_id]
        if not subtitle_tracks:
            raise ValueError("The selected burn-in subtitle track is missing or disabled")
        if not subtitle_tracks:
            return
        track = subtitle_tracks[0]
        placements = self.repository.list_subtitle_placements(track.id)
        if not placements:
            return
        segments = {
            segment.id: segment
            for document in self.repository.list_subtitle_documents()
            for segment in self.repository.list_subtitle_segments(document.id)
        }
        resolved_style = style or SubtitleStyle()
        scale = state.sequence.profile.height / 540.0
        font_size = max(8, round(resolved_style.font_size * scale))
        outline_size = max(0, round(resolved_style.outline_size * scale))
        shadow_size = max(0, round(resolved_style.shadow_size * scale))
        geometry_width = 0.9
        geometry_height = 0.25
        geometry_x = max(
            0.0,
            min(1.0 - geometry_width, resolved_style.position_x - geometry_width / 2),
        )
        geometry_y = max(
            0.0,
            min(1.0 - geometry_height, resolved_style.position_y - geometry_height / 2),
        )
        geometry = (
            f"{geometry_x * 100:g}%/{geometry_y * 100:g}%:"
            f"{geometry_width * 100:g}%x{geometry_height * 100:g}%:100"
        )
        for placement in placements:
            segment = segments.get(placement.segment_id)
            if segment is None:
                raise ValueError("Subtitle placement references a missing segment")
            text = (placement.text_override or segment.text).replace("#", r"\#")
            subtitle = ET.SubElement(
                tractor,
                "filter",
                {
                    "id": f"subtitle_{placement.id}",
                    "in": str(placement.start_frame),
                    "out": str(placement.end_frame - 1),
                },
            )
            self._property(subtitle, "mlt_service", "dynamictext")
            self._property(subtitle, "argument", text)
            self._property(subtitle, "geometry", geometry)
            self._property(subtitle, "family", resolved_style.font_family)
            self._property(subtitle, "size", str(font_size))
            self._property(subtitle, "weight", "700" if resolved_style.bold else "400")
            self._property(subtitle, "style", "italic" if resolved_style.italic else "normal")
            self._property(subtitle, "fgcolour", self._mlt_color(resolved_style.font_color))
            self._property(subtitle, "olcolour", self._mlt_color(resolved_style.outline_color))
            self._property(subtitle, "outline", str(outline_size))
            self._property(subtitle, "shadow", str(shadow_size))
            self._property(subtitle, "pad", str(round(resolved_style.background_padding * scale)))
            self._property(
                subtitle,
                "bgcolour",
                self._mlt_color(
                    resolved_style.background_color,
                    resolved_style.background_opacity if resolved_style.background_enabled else 0.0,
                ),
            )
            self._property(
                subtitle,
                "halign",
                {"left": "left", "center": "centre", "right": "right"}[resolved_style.alignment],
            )
            self._property(
                subtitle,
                "valign",
                {"top": "top", "center": "middle", "bottom": "bottom"}[resolved_style.multiline_alignment],
            )

    @staticmethod
    def _mlt_color(value: str, opacity: float = 1.0) -> str:
        alpha = max(0, min(255, round(opacity * 255)))
        return f"0x{value[1:]}{alpha:02x}".lower()

    def _append_audio_graph(
        self,
        root: ET.Element,
        state: TimelineState,
        tracks: list[Track],
        duration: int,
        assets: dict[str, Asset],
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> str | None:
        buses = self.repository.list_audio_buses(state.sequence.id)
        if not buses:
            return None
        by_id = {bus.id: bus for bus in buses}
        roots = [bus for bus in buses if bus.parent_bus_id is None]
        if len(roots) != 1:
            raise ValueError("An audio graph must have exactly one master bus")
        master = roots[0]
        solo_buses = {bus.id for bus in buses if bus.solo}
        allowed_buses = self._solo_bus_closure(solo_buses, by_id) if solo_buses else set(by_id)
        audible_track_ids = set(
            select_audible_sequence_audio(
                state,
                assets,
                buses,
                start_frame=0,
                end_frame=duration,
            ).track_ids
        )
        bus_tractor_ids: dict[str, str] = {}
        audio_playlists: set[str] = set()
        for bus in sorted(buses, key=lambda item: self._bus_depth(item, by_id), reverse=True):
            if bus.muted or bus.id not in allowed_buses:
                continue
            sources: list[str] = []
            for track in tracks:
                if track.id not in audible_track_ids:
                    continue
                destination = track.audio_bus_id or master.id
                if destination == bus.id:
                    if track.id not in audio_playlists:
                        self._append_audio_playlist(
                            root,
                            track,
                            state,
                            assets,
                            use_proxies=use_proxies,
                            prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
                        )
                        audio_playlists.add(track.id)
                    sources.append(self._audio_playlist_id(track.id))
            sources.extend(
                bus_tractor_ids[child.id]
                for child in buses
                if child.parent_bus_id == bus.id and child.id in bus_tractor_ids
            )
            if not sources:
                continue
            tractor_id = self._audio_bus_id(bus.id)
            tractor = ET.SubElement(
                root,
                "tractor",
                {"id": tractor_id, "in": "0", "out": str(max(0, duration - 1))},
            )
            self._property(tractor, "mediaflow:audio_bus", bus.name)
            for source in sources:
                ET.SubElement(tractor, "track", {"producer": source, "hide": "video"})
            self._append_audio_mixers(tractor, len(sources))
            self._append_bus_filters(tractor, bus, state, buses)
            bus_tractor_ids[bus.id] = tractor_id
        return bus_tractor_ids.get(master.id)

    def _append_audio_playlist(
        self,
        root: ET.Element,
        track: Track,
        state: TimelineState,
        assets: dict[str, Asset],
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> None:
        clips = audio_clips_for_track(state, track.id)
        source_track_ids = {track.id} | {
            source.id
            for source in state.tracks
            if source.linked_audio_track_id == track.id
        }
        clip_ids = {clip.id for clip in clips}
        transitions = [
            item
            for item in state.transitions
            if item.track_id in source_track_ids
            and item.left_clip_id in clip_ids
            and item.right_clip_id in clip_ids
        ]
        outgoing = {item.left_clip_id: item for item in transitions}
        for clip in clips:
            asset = assets[clip.asset_id]
            self._append_audio_producer(
                root,
                clip,
                asset,
                self._source_path(
                    asset,
                    use_proxies=use_proxies,
                    prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
                ),
                transition_tail_frames=(
                    self._transition_parts(outgoing[clip.id])[1] if clip.id in outgoing else 0
                ),
            )
        for transition in transitions:
            self._append_audio_transition_tractor(root, transition, state, assets)

        playlist = ET.SubElement(root, "playlist", {"id": self._audio_playlist_id(track.id)})
        self._property(playlist, "mediaflow:audio_track_name", track.name)
        incoming = {item.right_clip_id: item for item in transitions}
        cursor = 0
        for clip in clips:
            incoming_after = self._transition_parts(incoming[clip.id])[1] if clip.id in incoming else 0
            outgoing_before = self._transition_parts(outgoing[clip.id])[0] if clip.id in outgoing else 0
            visible_start = clip.timeline_start + incoming_after
            visible_end = clip.timeline_end - outgoing_before
            if visible_start > cursor:
                ET.SubElement(playlist, "blank", {"length": str(visible_start - cursor)})
            if visible_end > visible_start:
                producer_in = self._producer_frame(
                    clip,
                    assets[clip.asset_id],
                    incoming_after,
                )
                ET.SubElement(
                    playlist,
                    "entry",
                    {
                        "producer": self._audio_producer_id(clip.id),
                        "in": str(producer_in),
                        "out": str(producer_in + visible_end - visible_start - 1),
                    },
                )
                cursor = visible_end
            if clip.id in outgoing:
                transition = outgoing[clip.id]
                ET.SubElement(
                    playlist,
                    "entry",
                    {
                        "producer": self._audio_transition_id(transition.id),
                        "in": "0",
                        "out": str(transition.duration - 1),
                    },
                )
                cursor += transition.duration

    def _append_audio_producer(
        self,
        root: ET.Element,
        clip: Clip,
        asset: Asset,
        source: Path,
        *,
        transition_tail_frames: int,
    ) -> None:
        speed = clip.speed_numerator / clip.speed_denominator
        service = "qimage" if asset.kind == AssetKind.IMAGE else "avformat"
        resource = str(source)
        if speed != 1.0:
            service = "timewarp"
            resource = f"{speed}:{source}"
        producer = ET.SubElement(root, "producer", {"id": self._audio_producer_id(clip.id)})
        self._property(producer, "mlt_service", service)
        self._property(producer, "resource", resource)
        producer_start, natural_length = self._producer_timing(clip, asset)
        required_length = producer_start + clip.duration + transition_tail_frames
        self._property(producer, "length", str(max(natural_length, required_length)))
        self._property(producer, "eof", "pause")
        if service == "timewarp":
            self._property(producer, "warp_speed", str(speed))
            self._property(producer, "warp_resource", str(source))
            self._property(producer, "warp_pitch", "1" if clip.pitch_compensation else "0")
        if asset.kind == AssetKind.IMAGE:
            self._property(producer, "ttl", "1")
            self._property(producer, "set.test_audio", "1")
        self._append_clip_audio_filters(producer, clip, producer_start=producer_start)

    def _append_audio_transition_tractor(
        self,
        root: ET.Element,
        item: Transition,
        state: TimelineState,
        assets: dict[str, Asset],
    ) -> None:
        clips = {clip.id: clip for clip in state.clips}
        left = clips[item.left_clip_id]
        right = clips[item.right_clip_id]
        before, _after = self._transition_parts(item)
        left_start = self._producer_frame(left, assets[left.asset_id], left.duration - before)
        right_start = max(0, self._producer_frame(right, assets[right.asset_id], -before))
        tractor = ET.SubElement(
            root,
            "tractor",
            {"id": self._audio_transition_id(item.id), "in": "0", "out": str(item.duration - 1)},
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": self._audio_producer_id(left.id),
                "in": str(left_start),
                "out": str(left_start + item.duration - 1),
            },
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": self._audio_producer_id(right.id),
                "in": str(right_start),
                "out": str(right_start + item.duration - 1),
            },
        )
        transition = ET.SubElement(
            tractor,
            "transition",
            {"id": f"audio_only_transition_{item.id}", "in": "0", "out": str(item.duration - 1)},
        )
        self._property(transition, "a_track", "0")
        self._property(transition, "b_track", "1")
        self._property(transition, "start", "-1")
        self._property(transition, "accepts_blanks", "1")
        self._property(transition, "mlt_service", "mix")

    def _append_audio_mixers(self, tractor: ET.Element, source_count: int) -> None:
        for index in range(1, source_count):
            transition = ET.SubElement(
                tractor,
                "transition",
                {"id": f"audio_mix_{tractor.get('id')}_{index}"},
            )
            self._property(transition, "a_track", "0")
            self._property(transition, "b_track", str(index))
            self._property(transition, "mlt_service", "mix")
            self._property(transition, "always_active", "1")
            self._property(transition, "sum", "1")

    def _append_bus_filters(
        self,
        tractor: ET.Element,
        bus: AudioBus,
        state: TimelineState,
        buses: list[AudioBus],
    ) -> None:
        if bus.gain_db != 0.0:
            self._append_filter(tractor, f"bus_gain_{bus.id}", "volume", {"level": f"{bus.gain_db}dB"})
        for effect in self.repository.list_audio_effects(bus.id):
            if effect.enabled:
                self._append_audio_effect(tractor, effect, state, buses)

    def _append_audio_effect(
        self,
        parent: ET.Element,
        effect: AudioEffect,
        state: TimelineState,
        buses: list[AudioBus],
    ) -> None:
        values = effect.parameters
        if effect.kind == AudioEffectKind.DUCKING:
            self._append_ducking_filter(parent, effect, state, buses)
            return
        if effect.kind == AudioEffectKind.PARAMETRIC_EQ:
            bands = (
                (100.0, values.get("low_db", 0.0)),
                (500.0, values.get("low_mid_db", 0.0)),
                (2500.0, values.get("high_mid_db", 0.0)),
                (10000.0, values.get("high_db", 0.0)),
            )
            for index, (frequency, gain) in enumerate(bands):
                self._append_filter(
                    parent,
                    f"effect_{effect.id}_{index}",
                    "avfilter.equalizer",
                    {"av.frequency": frequency, "av.width_type": "o", "av.width": 1.0, "av.gain": gain},
                )
            return
        service_and_properties: dict[AudioEffectKind, tuple[str, dict[str, object]]] = {
            AudioEffectKind.HIGH_PASS: (
                "avfilter.highpass",
                {"av.frequency": values.get("frequency_hz", 80.0)},
            ),
            AudioEffectKind.LOW_PASS: (
                "avfilter.lowpass",
                {"av.frequency": values.get("frequency_hz", 16000.0)},
            ),
            AudioEffectKind.COMPRESSOR: (
                "avfilter.acompressor",
                {
                    "av.threshold": self._db_to_amplitude(values.get("threshold_db", -18.0)),
                    "av.ratio": values.get("ratio", 3.0),
                    "av.attack": values.get("attack_ms", 10.0),
                    "av.release": values.get("release_ms", 120.0),
                },
            ),
            AudioEffectKind.LIMITER: (
                "avfilter.alimiter",
                {"av.limit": self._db_to_amplitude(values.get("ceiling_db", -1.0))},
            ),
            AudioEffectKind.NOISE_GATE: (
                "avfilter.agate",
                {"av.threshold": self._db_to_amplitude(values.get("threshold_db", -45.0))},
            ),
            AudioEffectKind.RNNOISE: ("rnnoise", {"mix": values.get("mix", 1.0)}),
            AudioEffectKind.CHANNEL_MAP: ("audiomap", {"layout": values.get("layout", "stereo")}),
            AudioEffectKind.LOUDNESS_NORMALIZE: (
                "avfilter.loudnorm",
                {"av.I": values.get("target_lufs", -14.0), "av.TP": values.get("true_peak_db", -1.0)},
            ),
        }
        service, properties = service_and_properties[effect.kind]
        self._append_filter(parent, f"effect_{effect.id}", service, properties)

    def _append_ducking_filter(
        self,
        parent: ET.Element,
        effect: AudioEffect,
        state: TimelineState,
        buses: list[AudioBus],
    ) -> None:
        values = effect.parameters
        driver_bus_id = str(values.get("driver_bus_id") or "")
        if not driver_bus_id:
            driver_bus_id = next(
                (bus.id for bus in buses if bus.name in {"对白", "Dialogue"}),
                "",
            )
        if not driver_bus_id:
            raise ValueError("Ducking requires a dialogue driver bus")
        driver_track_ids = {
            track.id
            for track in state.tracks
            if track.audio_bus_id == driver_bus_id and track.enabled and not track.muted
        }
        ranges = sorted(
            (clip.timeline_start, clip.timeline_end)
            for clip in state.clips
            if clip.track_id in driver_track_ids
        )
        if not ranges:
            return
        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        fps = state.sequence.profile.fps
        attack = max(0, round(float(values.get("attack_ms", 120.0)) * fps / 1000.0))
        release = max(0, round(float(values.get("release_ms", 300.0)) * fps / 1000.0))
        reduction = min(0.0, float(values.get("reduction_db", -10.0)))
        points: dict[int, float] = {0: 0.0}
        for start, end in merged:
            points[max(0, start - attack)] = 0.0
            points[start] = reduction
            points[max(start, end - 1)] = reduction
            points[end + release] = 0.0
        animation = ";".join(f"{frame}={level:g}dB" for frame, level in sorted(points.items()))
        self._append_filter(
            parent,
            f"effect_{effect.id}",
            "volume",
            {"level": animation},
        )

    def _append_filter(
        self,
        parent: ET.Element,
        filter_id: str,
        service: str,
        properties: dict[str, object],
    ) -> None:
        filter_element = ET.SubElement(parent, "filter", {"id": filter_id})
        self._property(filter_element, "mlt_service", service)
        for name, value in properties.items():
            self._property(filter_element, name, str(value))

    @staticmethod
    def _db_to_amplitude(value: float) -> float:
        return max(0.000001, min(1.0, math.pow(10.0, float(value) / 20.0)))

    @staticmethod
    def _bus_depth(bus: AudioBus, by_id: dict[str, AudioBus]) -> int:
        depth = 0
        cursor = bus
        seen = {bus.id}
        while cursor.parent_bus_id:
            if cursor.parent_bus_id in seen or cursor.parent_bus_id not in by_id:
                raise ValueError("Audio bus routing contains a cycle or missing parent")
            seen.add(cursor.parent_bus_id)
            cursor = by_id[cursor.parent_bus_id]
            depth += 1
        return depth

    @staticmethod
    def _solo_bus_closure(solo_ids: set[str], by_id: dict[str, AudioBus]) -> set[str]:
        allowed = set(solo_ids)
        for bus_id in list(solo_ids):
            cursor = by_id[bus_id]
            while cursor.parent_bus_id:
                allowed.add(cursor.parent_bus_id)
                cursor = by_id[cursor.parent_bus_id]
        return allowed

    def _append_transition_tractor(
        self,
        root: ET.Element,
        item: Transition,
        clips: dict[str, Clip],
        assets: dict[str, Asset],
    ) -> None:
        left = clips[item.left_clip_id]
        right = clips[item.right_clip_id]
        before, after = self._transition_parts(item)
        left_start = self._producer_frame(left, assets[left.asset_id], left.duration - before)
        right_start = max(0, self._producer_frame(right, assets[right.asset_id], -before))
        if before >= left.duration or after >= right.duration:
            raise ValueError("Transition duration leaves no visible frames in an adjacent clip")

        tractor = ET.SubElement(
            root,
            "tractor",
            {
                "id": self._transition_id(item.id),
                "in": "0",
                "out": str(item.duration - 1),
            },
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": self._producer_id(left.id),
                "in": str(left_start),
                "out": str(left_start + item.duration - 1),
            },
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": self._producer_id(right.id),
                "in": str(right_start),
                "out": str(right_start + item.duration - 1),
            },
        )
        video = ET.SubElement(
            tractor,
            "transition",
            {"id": f"video_transition_{item.id}", "in": "0", "out": str(item.duration - 1)},
        )
        self._property(video, "a_track", "0")
        self._property(video, "b_track", "1")
        self._property(video, "mlt_service", self.TRANSITION_SERVICES[item.kind])
        self._property(video, "mediaflow:kind", item.kind.value)
        if item.kind == TransitionKind.WIPE_LEFT:
            self._property(video, "geometry", "0=0%/0%:100%x100%; 1=0%/0%:0%x100%")
        elif item.kind == TransitionKind.WIPE_RIGHT:
            self._property(video, "reverse", "1")
        elif item.kind in {TransitionKind.SLIDE_LEFT, TransitionKind.SLIDE_RIGHT}:
            direction = -100 if item.kind == TransitionKind.SLIDE_LEFT else 100
            self._property(video, "geometry", f"0={direction}%/0%:100%x100%; 1=0%/0%:100%x100%")
        elif item.kind == TransitionKind.ZOOM:
            self._property(video, "geometry", "0=25%/25%:50%x50%; 1=0%/0%:100%x100%")
        for name, value in item.parameters.items():
            self._property(video, str(name), str(value))

        audio = ET.SubElement(
            tractor,
            "transition",
            {"id": f"audio_transition_{item.id}", "in": "0", "out": str(item.duration - 1)},
        )
        self._property(audio, "a_track", "0")
        self._property(audio, "b_track", "1")
        self._property(audio, "start", "-1")
        self._property(audio, "accepts_blanks", "1")
        self._property(audio, "mlt_service", "mix")

    @staticmethod
    def _transition_parts(item: Transition) -> tuple[int, int]:
        before = item.duration // 2
        return before, item.duration - before

    @staticmethod
    def _round_fraction(value: Fraction) -> int:
        quotient, remainder = divmod(value.numerator, value.denominator)
        return quotient + (1 if remainder * 2 >= value.denominator else 0)

    @staticmethod
    def _ceil_fraction(value: Fraction) -> int:
        return -(-value.numerator // value.denominator)

    def _producer_timing(self, clip: Clip, asset: Asset) -> tuple[int, int]:
        """Return the cut start and natural length in MLT producer coordinates.

        Timewarp changes the producer time base. Its in/out points therefore use
        output frames (source frame divided by absolute speed), not source frames.
        A reverse producer starts from its natural out point and walks backwards.
        """
        speed = Fraction(abs(clip.speed_numerator), clip.speed_denominator)
        consumed = self._ceil_fraction(Fraction(clip.duration) * speed)
        if clip.speed_numerator > 0:
            fallback_source_length = clip.source_in + consumed
        else:
            fallback_source_length = clip.source_in + 1
        source_length = max(1, asset.metadata.duration_frames or fallback_source_length)
        if speed == 1 and clip.speed_numerator > 0:
            return clip.source_in, source_length
        natural_length = max(1, self._ceil_fraction(Fraction(source_length) / speed))
        scaled_source_in = self._round_fraction(Fraction(clip.source_in) / speed)
        if clip.speed_numerator > 0:
            producer_start = scaled_source_in
        else:
            producer_start = max(0, natural_length - 1 - scaled_source_in)
        return producer_start, natural_length

    def _producer_frame(
        self,
        clip: Clip,
        asset: Asset,
        timeline_offset: int,
    ) -> int:
        producer_start, _ = self._producer_timing(clip, asset)
        return producer_start + timeline_offset

    @staticmethod
    def _property(parent: ET.Element, name: str, value: str) -> None:
        node = ET.SubElement(parent, "property", {"name": name})
        node.text = value

    @staticmethod
    def _producer_id(clip_id: str) -> str:
        return f"producer_{clip_id.replace('-', '_')}"

    @staticmethod
    def _playlist_id(track_id: str) -> str:
        return f"playlist_{track_id.replace('-', '_')}"

    @staticmethod
    def _transition_id(transition_id: str) -> str:
        return f"transition_mix_{transition_id.replace('-', '_')}"

    @staticmethod
    def _audio_bus_id(bus_id: str) -> str:
        return f"audio_bus_{bus_id.replace('-', '_')}"

    @staticmethod
    def _audio_producer_id(clip_id: str) -> str:
        return f"audio_producer_{clip_id.replace('-', '_')}"

    @staticmethod
    def _audio_playlist_id(track_id: str) -> str:
        return f"audio_playlist_{track_id.replace('-', '_')}"

    @staticmethod
    def _audio_transition_id(transition_id: str) -> str:
        return f"audio_transition_mix_{transition_id.replace('-', '_')}"
