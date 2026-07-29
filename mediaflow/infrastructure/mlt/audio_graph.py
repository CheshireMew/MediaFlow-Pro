from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.enums import AssetKind, AudioEffectKind, TrackKind
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import (
    output_audio_clips_for_track,
    select_audible_sequence_audio,
)
from mediaflow.domain.timeline import Clip, TimelineState, Track, Transition
from mediaflow.infrastructure.mlt.clip_graph import MltClipGraph
from mediaflow.infrastructure.mlt.graph import MltGraph


class MltAudioGraph:
    def __init__(
        self,
        repository: TimelineCompilationDocuments,
        clip_graph: MltClipGraph,
    ):
        self.repository = repository
        self.clip_graph = clip_graph

    def append_audio_graph(
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
        buses = self.repository.audio.list_audio_buses(state.sequence.id)
        if not buses:
            return None
        by_id = {bus.id: bus for bus in buses}
        roots = [bus for bus in buses if bus.parent_bus_id is None]
        if len(roots) != 1:
            raise ValueError("An audio graph must have exactly one master bus")
        master = roots[0]
        solo_buses = {bus.id for bus in buses if bus.solo}
        allowed_buses = self.solo_bus_closure(solo_buses, by_id) if solo_buses else set(by_id)
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
        for bus in sorted(buses, key=lambda item: self.bus_depth(item, by_id), reverse=True):
            if bus.muted or bus.id not in allowed_buses:
                continue
            sources: list[str] = []
            for track in tracks:
                if track.id not in audible_track_ids:
                    continue
                destination = track.audio_bus_id or master.id
                if destination == bus.id:
                    if track.id not in audio_playlists:
                        self.append_audio_playlist(
                            root,
                            track,
                            state,
                            assets,
                            use_proxies=use_proxies,
                            prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
                        )
                        audio_playlists.add(track.id)
                    sources.append(MltGraph.audio_playlist_id(track.id))
            sources.extend(
                bus_tractor_ids[child.id]
                for child in buses
                if child.parent_bus_id == bus.id and child.id in bus_tractor_ids
            )
            if not sources:
                continue
            tractor_id = MltGraph.audio_bus_id(bus.id)
            tractor = ET.SubElement(
                root,
                "tractor",
                {"id": tractor_id, "in": "0", "out": str(max(0, duration - 1))},
            )
            MltGraph.property(tractor, "mediaflow:audio_bus", bus.name)
            for source in sources:
                ET.SubElement(tractor, "track", {"producer": source, "hide": "video"})
            self.append_audio_mixers(tractor, len(sources))
            self.append_bus_filters(tractor, bus, state, buses)
            bus_tractor_ids[bus.id] = tractor_id
        return bus_tractor_ids.get(master.id)

    def append_audio_playlist(
        self,
        root: ET.Element,
        track: Track,
        state: TimelineState,
        assets: dict[str, Asset],
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> None:
        clips = output_audio_clips_for_track(state, track.id)
        source_track_ids = {track.id} | {
            source.id for source in state.tracks if source.linked_audio_track_id == track.id
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
            self.append_audio_producer(
                root,
                clip,
                asset,
                MltGraph.source_path(
                    self.repository,
                    asset,
                    use_proxies=use_proxies,
                    prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
                ),
                transition_tail_frames=(
                    MltGraph.transition_parts(outgoing[clip.id])[1] if clip.id in outgoing else 0
                ),
            )
        for transition in transitions:
            self.append_audio_transition_tractor(root, transition, state, assets)

        playlist = ET.SubElement(root, "playlist", {"id": MltGraph.audio_playlist_id(track.id)})
        MltGraph.property(playlist, "mediaflow:audio_track_name", track.name)
        incoming = {item.right_clip_id: item for item in transitions}
        cursor = 0
        for clip in clips:
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
                        "producer": MltGraph.audio_producer_id(clip.id),
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
                        "producer": MltGraph.audio_transition_id(transition.id),
                        "in": "0",
                        "out": str(transition.duration - 1),
                    },
                )
                cursor += transition.duration

    def append_audio_producer(
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
        producer = ET.SubElement(root, "producer", {"id": MltGraph.audio_producer_id(clip.id)})
        MltGraph.property(producer, "mlt_service", service)
        MltGraph.property(producer, "resource", resource)
        producer_start, natural_length = MltGraph.producer_timing(clip, asset)
        required_length = producer_start + clip.duration + transition_tail_frames
        MltGraph.property(producer, "length", str(max(natural_length, required_length)))
        MltGraph.property(producer, "eof", "pause")
        if service == "timewarp":
            MltGraph.property(producer, "warp_speed", str(speed))
            MltGraph.property(producer, "warp_resource", str(source))
            MltGraph.property(producer, "warp_pitch", "1" if clip.pitch_compensation else "0")
        if asset.kind == AssetKind.IMAGE:
            MltGraph.property(producer, "ttl", "1")
            MltGraph.property(producer, "set.test_audio", "1")
        self.clip_graph.append_clip_audio_filters(producer, clip, producer_start=producer_start)

    def append_audio_transition_tractor(
        self,
        root: ET.Element,
        item: Transition,
        state: TimelineState,
        assets: dict[str, Asset],
    ) -> None:
        clips = {clip.id: clip for clip in state.clips}
        left = clips[item.left_clip_id]
        right = clips[item.right_clip_id]
        before, _after = MltGraph.transition_parts(item)
        left_start = MltGraph.producer_frame(left, assets[left.asset_id], left.duration - before)
        right_start = max(0, MltGraph.producer_frame(right, assets[right.asset_id], -before))
        tractor = ET.SubElement(
            root,
            "tractor",
            {"id": MltGraph.audio_transition_id(item.id), "in": "0", "out": str(item.duration - 1)},
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": MltGraph.audio_producer_id(left.id),
                "in": str(left_start),
                "out": str(left_start + item.duration - 1),
            },
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": MltGraph.audio_producer_id(right.id),
                "in": str(right_start),
                "out": str(right_start + item.duration - 1),
            },
        )
        transition = ET.SubElement(
            tractor,
            "transition",
            {"id": f"audio_only_transition_{item.id}", "in": "0", "out": str(item.duration - 1)},
        )
        MltGraph.property(transition, "a_track", "0")
        MltGraph.property(transition, "b_track", "1")
        MltGraph.property(transition, "start", "-1")
        MltGraph.property(transition, "accepts_blanks", "1")
        MltGraph.property(transition, "mlt_service", "mix")

    def append_audio_mixers(self, tractor: ET.Element, source_count: int) -> None:
        for index in range(1, source_count):
            transition = ET.SubElement(
                tractor,
                "transition",
                {"id": f"audio_mix_{tractor.get('id')}_{index}"},
            )
            MltGraph.property(transition, "a_track", "0")
            MltGraph.property(transition, "b_track", str(index))
            MltGraph.property(transition, "mlt_service", "mix")
            MltGraph.property(transition, "always_active", "1")
            MltGraph.property(transition, "sum", "1")

    def append_bus_filters(
        self,
        tractor: ET.Element,
        bus: AudioBus,
        state: TimelineState,
        buses: list[AudioBus],
    ) -> None:
        if bus.gain_db != 0.0:
            MltGraph.append_filter(tractor, f"bus_gain_{bus.id}", "volume", {"level": f"{bus.gain_db}dB"})
        for effect in self.repository.audio.list_audio_effects(bus.id):
            if effect.enabled:
                self.append_audio_effect(tractor, effect, state, buses)

    def append_audio_effect(
        self,
        parent: ET.Element,
        effect: AudioEffect,
        state: TimelineState,
        buses: list[AudioBus],
    ) -> None:
        values = effect.parameters
        if effect.kind == AudioEffectKind.DUCKING:
            self.append_ducking_filter(parent, effect, state, buses)
            return
        if effect.kind == AudioEffectKind.PARAMETRIC_EQ:
            bands = (
                (100.0, values.get("low_db", 0.0)),
                (500.0, values.get("low_mid_db", 0.0)),
                (2500.0, values.get("high_mid_db", 0.0)),
                (10000.0, values.get("high_db", 0.0)),
            )
            for index, (frequency, gain) in enumerate(bands):
                MltGraph.append_filter(
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
                    "av.threshold": self.db_to_amplitude(values.get("threshold_db", -18.0)),
                    "av.ratio": values.get("ratio", 3.0),
                    "av.attack": values.get("attack_ms", 10.0),
                    "av.release": values.get("release_ms", 120.0),
                },
            ),
            AudioEffectKind.LIMITER: (
                "avfilter.alimiter",
                {"av.limit": self.db_to_amplitude(values.get("ceiling_db", -1.0))},
            ),
            AudioEffectKind.NOISE_GATE: (
                "avfilter.agate",
                {"av.threshold": self.db_to_amplitude(values.get("threshold_db", -45.0))},
            ),
            AudioEffectKind.RNNOISE: ("rnnoise", {"mix": values.get("mix", 1.0)}),
            AudioEffectKind.CHANNEL_MAP: ("audiomap", {"layout": values.get("layout", "stereo")}),
            AudioEffectKind.LOUDNESS_NORMALIZE: (
                "avfilter.loudnorm",
                {"av.I": values.get("target_lufs", -14.0), "av.TP": values.get("true_peak_db", -1.0)},
            ),
        }
        service, properties = service_and_properties[effect.kind]
        MltGraph.append_filter(parent, f"effect_{effect.id}", service, properties)

    def append_ducking_filter(
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
        driver_tracks = [
            track
            for track in state.effective_tracks(TrackKind.AUDIO)
            if track.audio_bus_id == driver_bus_id and not track.muted
        ]
        ranges = sorted(
            (clip.timeline_start, clip.timeline_end)
            for track in driver_tracks
            for clip in output_audio_clips_for_track(state, track.id)
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
        MltGraph.append_filter(
            parent,
            f"effect_{effect.id}",
            "volume",
            {"level": animation},
        )

    @staticmethod
    def db_to_amplitude(value: float) -> float:
        return max(0.000001, min(1.0, math.pow(10.0, float(value) / 20.0)))

    @staticmethod
    def bus_depth(bus: AudioBus, by_id: dict[str, AudioBus]) -> int:
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
    def solo_bus_closure(solo_ids: set[str], by_id: dict[str, AudioBus]) -> set[str]:
        allowed = set(solo_ids)
        for bus_id in list(solo_ids):
            cursor = by_id[bus_id]
            while cursor.parent_bus_id:
                allowed.add(cursor.parent_bus_id)
                cursor = by_id[cursor.parent_bus_id]
        return allowed
