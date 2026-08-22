from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.application.timeline_clock import assets_in_timeline_clock
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import AssetKind, ColorMode, TrackKind
from mediaflow.domain.exports import (
    SubtitleStyle,
    WatermarkOverlay,
)
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.sequence_audio import (
    output_audio_clips_for_track,
    select_audible_sequence_audio,
)
from mediaflow.domain.timeline import (
    TimelineState,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_render_target import WebRenderCache

from .audio_graph import MltAudioGraph
from .clip_graph import MltClipGraph
from .graph import MltGraph
from .source_registry import MltSourceRegistry
from .transition_graph import MltTransitionGraph
from .video_graph import MltVideoGraph


@dataclass(frozen=True, slots=True)
class MltDocument:
    xml: str
    duration_frames: int
    source_paths: tuple[Path, ...]


class TimelineCompiler:
    """Compile the canonical project timeline into an MLT graph."""

    def __init__(
        self,
        repository: TimelineCompilationDocuments,
        paths: RuntimePaths,
    ):
        self.repository = repository
        self.paths = paths
        self._clip_graph = MltClipGraph()
        self._video_graph = MltVideoGraph(repository, self._clip_graph)
        self._audio_graph = MltAudioGraph(repository, self._clip_graph)
        self._transition_graph = MltTransitionGraph()

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
                "description": PRODUCT_NAME,
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

        assets = assets_in_timeline_clock(
            self.repository.projects,
            self.repository.sequences,
            self.repository.assets, state.sequence)
        effective_video_tracks = state.effective_tracks(TrackKind.VIDEO)
        buses = self.repository.audio.list_audio_buses(state.sequence.id)
        audible_audio_track_ids = set(
            select_audible_sequence_audio(
                state,
                assets,
                buses,
                start_frame=0,
                end_frame=duration,
            ).track_ids
        )
        active_clip_ids = {
            clip.id for track in effective_video_tracks for clip in state.clips_for_track(track.id)
        }
        active_clip_ids.update(
            clip.id
            for track_id in audible_audio_track_ids
            for clip in output_audio_clips_for_track(state, track_id)
        )
        active_transitions = [
            item
            for item in state.transitions
            if item.left_clip_id in active_clip_ids and item.right_clip_id in active_clip_ids
        ]
        outgoing_transitions = {item.left_clip_id: item for item in active_transitions}
        clip_sources: dict[str, Path] = {}
        sources = MltSourceRegistry(
            self.repository,
            use_proxies=use_proxies,
            prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
        )

        for clip in state.clips:
            if clip.id not in active_clip_ids:
                continue
            asset = assets.get(clip.asset_id)
            if asset is None:
                raise ValueError(f"Timeline references unknown asset: {clip.asset_id}")
            clip.validate_source_range(asset.kind, asset.metadata.duration_frames)
            source = (
                sources.require_source(
                    WebRenderCache(
                        self.repository,
                        self.paths,
                    )
                    .target(state, clip, asset)
                    .path,
                )
                if asset.kind == AssetKind.WEB
                else sources.asset_source(asset)
            )
            clip_sources[clip.id] = source
            visual_effect_resources: dict[str, Path] = {}
            for effect in clip.visual_effects:
                if effect.resource_asset_id is None:
                    continue
                resource_asset = assets.get(effect.resource_asset_id)
                if resource_asset is None:
                    raise ValueError(
                        f"Visual effect references unknown asset: {effect.resource_asset_id}"
                    )
                if resource_asset.kind != AssetKind.LUT:
                    raise ValueError("Visual effect resource asset must be a LUT")
                effect_source = sources.asset_source(resource_asset, use_proxies=False)
                visual_effect_resources[effect.id] = effect_source
            self._clip_graph.append_producer(
                root,
                clip,
                asset,
                source,
                profile.color_mode,
                transition_tail_frames=(
                    MltGraph.transition_parts(outgoing_transitions[clip.id])[1]
                    if clip.id in outgoing_transitions
                    else 0
                ),
                native_preview=native_preview,
                visual_effect_resources=visual_effect_resources,
            )

        clips = {clip.id: clip for clip in state.clips}
        for transition in active_transitions:
            self._transition_graph.append_transition_tractor(
                root,
                transition,
                clips,
                assets,
            )

        ordered_tracks = sorted(state.tracks, key=lambda track: (track.position, track.id))
        for track in effective_video_tracks:
            self._video_graph.append_playlist(root, track, state, assets)

        watermark_track = self._video_graph.append_watermark_playlist(
            root,
            state,
            watermark,
            duration,
        )
        if watermark_track is not None:
            sources.require_source(watermark_track[2])

        audio_root_id = self._audio_graph.append_audio_graph(
            root,
            state,
            ordered_tracks,
            duration,
            assets,
            clip_sources=clip_sources,
        )

        tractor = ET.SubElement(
            root,
            "tractor",
            {"id": "tractor0", "in": "0", "out": str(max(0, duration - 1))},
        )
        MltGraph.property(tractor, "global_feed", "1")
        video_tracks = effective_video_tracks
        for track in video_tracks:
            ET.SubElement(
                tractor,
                "track",
                {"producer": MltGraph.playlist_id(track.id), "hide": "audio"},
            )
        if watermark_track is not None:
            ET.SubElement(
                tractor,
                "track",
                {"producer": watermark_track[0], "hide": "audio"},
            )
        if audio_root_id:
            ET.SubElement(tractor, "track", {"producer": audio_root_id, "hide": "video"})
        self._video_graph.append_layer_compositors(
            tractor,
            video_tracks,
            duration,
            native_preview=native_preview,
        )
        if watermark_track is not None:
            self._video_graph.append_watermark_compositor(
                tractor,
                b_track=len(video_tracks),
                duration=duration,
                geometry=watermark_track[1],
                native_preview=native_preview,
            )
        if not native_preview:
            self._video_graph.append_subtitle_filters(
                tractor,
                state,
                subtitle_track_id,
                subtitle_style,
            )

        ET.indent(root, space="  ")
        xml = ET.tostring(root, encoding="unicode", xml_declaration=True)
        return MltDocument(xml=xml, duration_frames=duration, source_paths=sources.paths)

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
        atomic_write_text(path, document.xml)
        return document
