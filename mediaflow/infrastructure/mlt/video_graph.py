from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.exports import SubtitleStyle, WatermarkOverlay
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import TimelineState, Track
from mediaflow.infrastructure.mlt.clip_graph import MltClipGraph
from mediaflow.infrastructure.mlt.graph import MltGraph


class MltVideoGraph:
    def __init__(
        self,
        repository: TimelineCompilationDocuments,
        clip_graph: MltClipGraph,
    ):
        self.repository = repository
        self.clip_graph = clip_graph

    def append_playlist(
        self,
        root: ET.Element,
        track: Track,
        state: TimelineState,
        assets: dict[str, Asset],
    ) -> None:
        playlist = ET.SubElement(root, "playlist", {"id": MltGraph.playlist_id(track.id)})
        MltGraph.property(playlist, "mediaflow:track_name", track.name)
        incoming = {item.right_clip_id: item for item in state.transitions if item.track_id == track.id}
        outgoing = {item.left_clip_id: item for item in state.transitions if item.track_id == track.id}
        cursor = 0
        for clip in state.clips_for_track(track.id):
            incoming_after = MltGraph.transition_parts(incoming[clip.id])[1] if clip.id in incoming else 0
            outgoing_before = MltGraph.transition_parts(outgoing[clip.id])[0] if clip.id in outgoing else 0
            visible_start = clip.timeline_start + incoming_after
            visible_end = clip.timeline_end - outgoing_before
            if visible_start > cursor:
                ET.SubElement(playlist, "blank", {"length": str(visible_start - cursor)})
            if visible_end > visible_start:
                producer_in = MltGraph.producer_frame(
                    clip,
                    assets[clip.asset_id],
                    incoming_after,
                )
                ET.SubElement(
                    playlist,
                    "entry",
                    {
                        "producer": MltGraph.producer_id(clip.id),
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
                        "producer": MltGraph.transition_id(transition.id),
                        "in": "0",
                        "out": str(transition.duration - 1),
                    },
                )
                cursor += transition.duration

    def append_layer_compositors(
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
            MltGraph.property(transition, "a_track", "0")
            MltGraph.property(transition, "b_track", str(b_track))
            MltGraph.property(transition, "mlt_service", "composite" if native_preview else "qtblend")
            MltGraph.property(transition, "always_active", "1")

    def append_watermark_playlist(
        self,
        root: ET.Element,
        state: TimelineState,
        watermark: WatermarkOverlay | None,
        duration: int,
    ) -> tuple[str, str, Path] | None:
        if watermark is None or not watermark.enabled or not watermark.asset_id:
            return None
        asset = self.repository.catalog.get_asset(watermark.asset_id)
        if asset.kind != AssetKind.IMAGE:
            raise ValueError("Watermark asset must be an image")
        source = self.repository.catalog.resolve_asset_path(asset)
        if not source.is_file():
            raise FileNotFoundError(source)
        producer_id = f"watermark_producer_{asset.id}"
        playlist_id = f"watermark_playlist_{asset.id}"
        producer = ET.SubElement(root, "producer", {"id": producer_id})
        MltGraph.property(producer, "mlt_service", "qimage")
        MltGraph.property(producer, "resource", str(source))
        MltGraph.property(producer, "length", str(duration))
        MltGraph.property(producer, "ttl", "1")
        MltGraph.property(producer, "eof", "pause")
        MltGraph.property(producer, "set.test_audio", "1")
        self.clip_graph.append_color_pipeline(producer, asset, state.sequence.profile.color_mode)
        playlist = ET.SubElement(root, "playlist", {"id": playlist_id})
        ET.SubElement(
            playlist,
            "entry",
            {"producer": producer_id, "in": "0", "out": str(max(0, duration - 1))},
        )
        return playlist_id, self.watermark_geometry(state, asset, watermark), source

    def append_watermark_compositor(
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
        MltGraph.property(transition, "a_track", "0")
        MltGraph.property(transition, "b_track", str(b_track))
        MltGraph.property(transition, "mlt_service", "affine" if native_preview else "qtblend")
        MltGraph.property(transition, "always_active", "1")
        MltGraph.property(transition, "transition.rect" if native_preview else "rect", geometry)

    @staticmethod
    def watermark_geometry(
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

    def append_subtitle_filters(
        self,
        tractor: ET.Element,
        state: TimelineState,
        requested_track_id: str | None,
        style: SubtitleStyle | None,
    ) -> None:
        if not requested_track_id:
            return
        subtitle_tracks = [
            track for track in state.effective_tracks(TrackKind.SUBTITLE) if track.id == requested_track_id
        ]
        if not subtitle_tracks:
            raise ValueError("The selected burn-in subtitle track is missing or disabled")
        track = subtitle_tracks[0]
        placements = self.repository.subtitles.list_subtitle_placements(track.id)
        if not placements:
            return
        segments = {
            segment.id: segment
            for document in self.repository.subtitles.list_subtitle_documents()
            for segment in self.repository.subtitles.list_subtitle_segments(document.id)
        }
        resolved_style = style or track.subtitle_style or SubtitleStyle()
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
            MltGraph.property(subtitle, "mlt_service", "dynamictext")
            MltGraph.property(subtitle, "argument", text)
            MltGraph.property(subtitle, "geometry", geometry)
            MltGraph.property(subtitle, "family", resolved_style.font_family)
            MltGraph.property(subtitle, "size", str(font_size))
            MltGraph.property(subtitle, "weight", "700" if resolved_style.bold else "400")
            MltGraph.property(subtitle, "style", "italic" if resolved_style.italic else "normal")
            MltGraph.property(subtitle, "fgcolour", self.mlt_color(resolved_style.font_color))
            MltGraph.property(subtitle, "olcolour", self.mlt_color(resolved_style.outline_color))
            MltGraph.property(subtitle, "outline", str(outline_size))
            MltGraph.property(subtitle, "shadow", str(shadow_size))
            MltGraph.property(subtitle, "pad", str(round(resolved_style.background_padding * scale)))
            MltGraph.property(
                subtitle,
                "bgcolour",
                self.mlt_color(
                    resolved_style.background_color,
                    resolved_style.background_opacity if resolved_style.background_enabled else 0.0,
                ),
            )
            MltGraph.property(
                subtitle,
                "halign",
                {"left": "left", "center": "centre", "right": "right"}[resolved_style.alignment],
            )
            MltGraph.property(
                subtitle,
                "valign",
                {"top": "top", "center": "middle", "bottom": "bottom"}[resolved_style.multiline_alignment],
            )

    @staticmethod
    def mlt_color(value: str, opacity: float = 1.0) -> str:
        alpha = max(0, min(255, round(opacity * 255)))
        return f"0x{value[1:]}{alpha:02x}".lower()
