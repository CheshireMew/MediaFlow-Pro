from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.enums import AssetKind, ColorMode
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import Clip, ClipTransform
from mediaflow.infrastructure.mlt.graph import MltGraph


class MltClipGraph:
    def append_producer(
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
            {"id": MltGraph.producer_id(clip.id)},
        )
        MltGraph.property(producer, "mlt_service", service)
        MltGraph.property(producer, "resource", resource)
        producer_start, natural_length = MltGraph.producer_timing(clip, asset)
        required_length = producer_start + clip.duration + transition_tail_frames
        producer_length = max(natural_length, required_length)
        MltGraph.property(producer, "length", str(producer_length))
        MltGraph.property(producer, "eof", "pause")
        if service == "timewarp":
            MltGraph.property(producer, "warp_speed", str(speed))
            MltGraph.property(producer, "warp_resource", str(source))
            MltGraph.property(producer, "warp_pitch", "1" if clip.pitch_compensation else "0")
        if service == "qimage":
            MltGraph.property(producer, "ttl", "1")
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
            MltGraph.property(freeze, "mlt_service", "freeze")
            MltGraph.property(freeze, "frame", str(max(0, natural_length - 1)))
            MltGraph.property(freeze, "freeze_after", "1")
        if asset.kind in {AssetKind.VIDEO, AssetKind.IMAGE, AssetKind.WEB}:
            self.append_color_pipeline(producer, asset, project_color_mode)
        self.append_clip_filters(
            producer,
            clip,
            asset,
            producer_start=producer_start,
            native_preview=native_preview,
        )

    def append_color_pipeline(
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
            MltGraph.append_filter(
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

        MltGraph.append_filter(
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
        MltGraph.append_filter(
            producer,
            f"color_hdr_tonemap_{token}",
            "avfilter.tonemap",
            {"av.tonemap": "mobius", "av.param": 0.3, "av.desat": 2.0, "av.peak": 10.0},
        )
        MltGraph.append_filter(
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

    def append_clip_filters(
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
            MltGraph.property(crop, "mlt_service", "crop")
            MltGraph.property(crop, "active", "1")
            MltGraph.property(crop, "left", str(round(transform.crop_left * (asset.metadata.width or 1))))
            MltGraph.property(crop, "top", str(round(transform.crop_top * (asset.metadata.height or 1))))
            MltGraph.property(crop, "right", str(round(transform.crop_right * (asset.metadata.width or 1))))
            MltGraph.property(
                crop, "bottom", str(round(transform.crop_bottom * (asset.metadata.height or 1)))
            )
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
            MltGraph.property(filter_element, "mlt_service", "affine" if native_preview else "qtblend")

            def rect_value(value: ClipTransform) -> str:
                width = max(0.01, value.scale_x) * 100.0
                height = max(0.01, value.scale_y) * 100.0
                return f"{value.x:g}%/{value.y:g}%:{width:g}%x{height:g}%:{value.opacity * 100:g}%"

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
                    local_frame = MltGraph.round_fraction(Fraction(source_delta) / speed)
                    if 0 <= local_frame < clip.duration:
                        points[producer_start + local_frame] = keyframe.transform
                final_value = points[max(points)]
                points[producer_start + clip.duration - 1] = final_value
                rect = ";".join(f"{frame}={rect_value(value)}" for frame, value in sorted(points.items()))
                rotation = ";".join(f"{frame}={value.rotation:g}" for frame, value in sorted(points.items()))
            if native_preview:
                MltGraph.property(filter_element, "transition.rect", rect)
                MltGraph.property(filter_element, "transition.rotate_z", rotation)
            else:
                MltGraph.property(filter_element, "rect", rect)
                MltGraph.property(filter_element, "rotation", rotation)
        self.append_clip_audio_filters(producer, clip, producer_start=producer_start)
        if asset.kind in {AssetKind.IMAGE, AssetKind.WEB}:
            MltGraph.property(producer, "set.test_audio", "1")

    def append_clip_audio_filters(
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
            MltGraph.property(volume, "mlt_service", "volume")
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
            MltGraph.property(volume, "level", animation)
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
            MltGraph.property(panner, "mlt_service", "panner")
            MltGraph.property(panner, "start", str(clip.audio.pan))
