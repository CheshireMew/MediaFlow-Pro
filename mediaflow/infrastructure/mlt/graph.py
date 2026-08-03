from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.project import Asset
from mediaflow.domain.timebase import round_fraction
from mediaflow.domain.timeline import Clip, Transition


class MltGraph:
    @staticmethod
    def source_path(
        repository: TimelineCompilationDocuments,
        asset: Asset,
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool = False,
    ) -> Path:
        if use_proxies and prefer_sdr_preview_proxy and asset.sdr_preview_proxy_path:
            proxy = Path(asset.sdr_preview_proxy_path)
            return (repository.project_dir / proxy).resolve() if not proxy.is_absolute() else proxy.resolve()
        if use_proxies and asset.proxy_path:
            proxy = Path(asset.proxy_path)
            return (repository.project_dir / proxy).resolve() if not proxy.is_absolute() else proxy.resolve()
        return repository.catalog.resolve_asset_path(asset)

    @classmethod
    def append_filter(
        cls,
        parent: ET.Element,
        filter_id: str,
        service: str,
        properties: dict[str, object],
    ) -> None:
        filter_element = ET.SubElement(parent, "filter", {"id": filter_id})
        cls.property(filter_element, "mlt_service", service)
        for name, value in properties.items():
            cls.property(filter_element, name, str(value))

    @staticmethod
    def transition_parts(item: Transition) -> tuple[int, int]:
        before = item.duration // 2
        return before, item.duration - before

    @staticmethod
    def ceil_fraction(value: Fraction) -> int:
        return -(-value.numerator // value.denominator)

    @classmethod
    def producer_timing(cls, clip: Clip, asset: Asset) -> tuple[int, int]:
        """Return the cut start and natural length in MLT producer coordinates.

        Timewarp changes the producer time base. Its in/out points therefore use
        output frames (source frame divided by absolute speed), not source frames.
        A reverse producer starts from its natural out point and walks backwards.
        """
        speed = Fraction(abs(clip.speed_numerator), clip.speed_denominator)
        consumed = cls.ceil_fraction(Fraction(clip.duration) * speed)
        if clip.speed_numerator > 0:
            fallback_source_length = clip.source_in + consumed
        else:
            fallback_source_length = clip.source_in + 1
        source_length = max(1, asset.metadata.duration_frames or fallback_source_length)
        if speed == 1 and clip.speed_numerator > 0:
            return clip.source_in, source_length
        natural_length = max(1, cls.ceil_fraction(Fraction(source_length) / speed))
        scaled_source_in = round_fraction(Fraction(clip.source_in) / speed)
        if clip.speed_numerator > 0:
            producer_start = scaled_source_in
        else:
            producer_start = max(0, natural_length - 1 - scaled_source_in)
        return producer_start, natural_length

    @classmethod
    def producer_frame(
        cls,
        clip: Clip,
        asset: Asset,
        timeline_offset: int,
    ) -> int:
        producer_start, _ = cls.producer_timing(clip, asset)
        return producer_start + timeline_offset

    @staticmethod
    def property(parent: ET.Element, name: str, value: str) -> None:
        node = ET.SubElement(parent, "property", {"name": name})
        node.text = value

    @staticmethod
    def producer_id(clip_id: str) -> str:
        return f"producer_{clip_id.replace('-', '_')}"

    @staticmethod
    def playlist_id(track_id: str) -> str:
        return f"playlist_{track_id.replace('-', '_')}"

    @staticmethod
    def transition_id(transition_id: str) -> str:
        return f"transition_mix_{transition_id.replace('-', '_')}"

    @staticmethod
    def audio_bus_id(bus_id: str) -> str:
        return f"audio_bus_{bus_id.replace('-', '_')}"

    @staticmethod
    def audio_producer_id(clip_id: str) -> str:
        return f"audio_producer_{clip_id.replace('-', '_')}"

    @staticmethod
    def audio_playlist_id(track_id: str) -> str:
        return f"audio_playlist_{track_id.replace('-', '_')}"

    @staticmethod
    def audio_transition_id(transition_id: str) -> str:
        return f"audio_transition_mix_{transition_id.replace('-', '_')}"
