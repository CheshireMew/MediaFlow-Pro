from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

from mediaflow.domain.clip_transform_projection import sampled_clip_transform_points
from mediaflow.domain.enums import AssetKind, ColorMode
from mediaflow.domain.mask_projection import mask_spline_json
from mediaflow.domain.masks import ClipMask
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import Clip, ClipTransform
from mediaflow.domain.visual_effects import (
    ClipVisualEffect,
    visual_effect_mlt,
    visual_effect_parameter_points,
)
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
        visual_effect_resources: Mapping[str, Path],
    ) -> None:
        speed = clip.speed_numerator / clip.speed_denominator
        service = (
            "qimage"
            if asset.kind == AssetKind.IMAGE or (asset.kind == AssetKind.WEB and source.suffix == ".png")
            else "avformat"
        )
        resource = str(source)
        if speed != 1.0 and clip.freeze_source_frame is None:
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
        if clip.freeze_source_frame is not None:
            freeze = ET.SubElement(
                producer,
                "filter",
                {
                    "id": f"freeze_{clip.id}",
                    "in": "0",
                    "out": str(producer_length - 1),
                },
            )
            MltGraph.property(freeze, "mlt_service", "freeze")
            MltGraph.property(freeze, "frame", str(clip.freeze_source_frame))
            MltGraph.property(freeze, "freeze_after", "1")
        elif service == "qimage":
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
            visual_effect_resources=visual_effect_resources,
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
        visual_effect_resources: Mapping[str, Path],
    ) -> None:
        transform = clip.transform
        transform_points = sampled_clip_transform_points(clip)
        masks = {mask.id: mask for mask in clip.masks}
        for effect in clip.visual_effects:
            if not effect.enabled:
                continue
            mask = masks.get(effect.mask_id) if effect.mask_id is not None else None
            if effect.mask_id is not None and (mask is None or not mask.enabled):
                continue
            mask_stack = self._effect_mask_stack(clip, mask) if mask is not None else []
            if mask_stack:
                self._append_mask_start(
                    producer,
                    clip,
                    mask_stack[0],
                    effect,
                    producer_start=producer_start,
                )
                for combined_mask in mask_stack[1:]:
                    self._append_combined_mask(
                        producer,
                        clip,
                        combined_mask,
                        effect,
                        producer_start=producer_start,
                    )
            resource_path = visual_effect_resources.get(effect.id)
            self._append_visual_effect(
                producer,
                clip,
                effect,
                producer_start=producer_start,
                resource_path=resource_path,
            )
            if mask_stack:
                self._append_mask_apply(
                    producer,
                    clip,
                    mask_stack[-1],
                    effect,
                    producer_start=producer_start,
                )

        if any(
            value > 0.0
            for _frame, point in transform_points
            for value in (
                point.crop_left,
                point.crop_top,
                point.crop_right,
                point.crop_bottom,
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
            width = asset.metadata.width or 1
            height = asset.metadata.height or 1

            def crop_animation(field: str, dimension: int) -> str:
                if not clip.transform_keyframes:
                    return str(round(getattr(transform, field) * dimension))
                return ";".join(
                    f"{producer_start + frame}={round(getattr(point, field) * dimension)}"
                    for frame, point in transform_points
                )

            MltGraph.property(crop, "left", crop_animation("crop_left", width))
            MltGraph.property(crop, "top", crop_animation("crop_top", height))
            MltGraph.property(crop, "right", crop_animation("crop_right", width))
            MltGraph.property(crop, "bottom", crop_animation("crop_bottom", height))
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
                points = {
                    producer_start + frame: value
                    for frame, value in transform_points
                }
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

    @staticmethod
    def _effect_mask_stack(clip: Clip, terminal: ClipMask) -> list[ClipMask]:
        ordered = sorted(clip.masks, key=lambda item: item.position)
        terminal_index = next(
            index for index, item in enumerate(ordered) if item.id == terminal.id
        )
        candidates = [item for item in ordered[: terminal_index + 1] if item.enabled]
        replace_index = max(
            (
                index
                for index, item in enumerate(candidates)
                if item.combine_mode == "replace"
            ),
            default=0,
        )
        return candidates[replace_index:]

    @staticmethod
    def _append_mask_start(
        producer: ET.Element,
        clip: Clip,
        mask: ClipMask,
        effect: ClipVisualEffect,
        *,
        producer_start: int,
    ) -> None:
        element = ET.SubElement(
            producer,
            "filter",
            {
                "id": f"mask_start_{mask.id}_{effect.id}",
                "in": str(producer_start),
                "out": str(producer_start + clip.duration - 1),
            },
        )
        MltGraph.property(element, "mlt_service", "mask_start")
        MltGraph.property(element, "filter", "rotoscoping")
        MltGraph.property(element, "filter.mode", "alpha")
        MltGraph.property(element, "filter.alpha_operation", "clear")
        MltGraph.property(element, "filter.invert", "1" if mask.inverted else "0")
        MltGraph.property(element, "filter.feather", str(mask.feather))
        MltGraph.property(element, "filter.feather_passes", str(mask.feather_passes))
        MltGraph.property(
            element,
            "filter.spline",
            mask_spline_json(clip, mask, producer_start=producer_start),
        )

    @staticmethod
    def _append_visual_effect(
        producer: ET.Element,
        clip: Clip,
        effect: ClipVisualEffect,
        *,
        producer_start: int,
        resource_path: Path | None,
    ) -> None:
        service, properties = visual_effect_mlt(
            effect,
            resource_path=str(resource_path) if resource_path is not None else None,
        )
        element = ET.SubElement(
            producer,
            "filter",
            {
                "id": f"visual_effect_{effect.id}",
                "in": str(producer_start),
                "out": str(producer_start + clip.duration - 1),
            },
        )
        MltGraph.property(element, "mlt_service", service)
        for name, value in properties.items():
            field_id = name.removeprefix("av.")
            animation = effect.parameter_keyframes.get(field_id)
            if animation:
                rendered = ";".join(
                    f"{producer_start + frame}={sample:g}"
                    for frame, sample in visual_effect_parameter_points(
                        effect,
                        field_id,
                        duration=clip.duration,
                    )
                )
            else:
                rendered = f"{value:g}" if isinstance(value, (int, float)) else value
            MltGraph.property(element, name, rendered)

    @staticmethod
    def _append_combined_mask(
        producer: ET.Element,
        clip: Clip,
        mask: ClipMask,
        effect: ClipVisualEffect,
        *,
        producer_start: int,
    ) -> None:
        operations = {
            "add": "max",
            "subtract": "sub",
            "intersect": "min",
        }
        operation = operations.get(mask.combine_mode)
        if operation is None:
            raise ValueError("蒙版堆叠中只有首个启用蒙版可以使用替换模式")
        element = ET.SubElement(
            producer,
            "filter",
            {
                "id": f"mask_combine_{mask.id}_{effect.id}",
                "in": str(producer_start),
                "out": str(producer_start + clip.duration - 1),
            },
        )
        MltGraph.property(element, "mlt_service", "rotoscoping")
        MltGraph.property(element, "mode", "alpha")
        MltGraph.property(element, "alpha_operation", operation)
        MltGraph.property(element, "invert", "1" if mask.inverted else "0")
        MltGraph.property(element, "feather", str(mask.feather))
        MltGraph.property(element, "feather_passes", str(mask.feather_passes))
        MltGraph.property(
            element,
            "spline",
            mask_spline_json(clip, mask, producer_start=producer_start),
        )

    @staticmethod
    def _append_mask_apply(
        producer: ET.Element,
        clip: Clip,
        mask: ClipMask,
        effect: ClipVisualEffect,
        *,
        producer_start: int,
    ) -> None:
        element = ET.SubElement(
            producer,
            "filter",
            {
                "id": f"mask_apply_{mask.id}_{effect.id}",
                "in": str(producer_start),
                "out": str(producer_start + clip.duration - 1),
            },
        )
        MltGraph.property(element, "mlt_service", "mask_apply")
        MltGraph.property(element, "transition", "qtblend")
        MltGraph.property(element, "transition.threads", "0")
        MltGraph.property(
            element,
            "transition.rect",
            f"0%/0%:100%x100%:{mask.opacity * 100:g}%",
        )

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
