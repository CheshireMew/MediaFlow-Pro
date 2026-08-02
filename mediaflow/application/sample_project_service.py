from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from pathlib import Path

from mediaflow.atomic_file import atomic_write_bytes
from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.project import Asset, MediaMetadata


class SampleProjectService:
    """Populate a newly created project through the normal project contracts."""

    def __init__(self, repository, timeline: Callable, project_dir: Path) -> None:
        self.repository = repository
        self.timeline = timeline
        self.project_dir = project_dir

    def populate(self) -> None:
        if self.repository.catalog.list_assets():
            raise ValueError("示例内容只能写入空项目")
        project = self.repository.catalog.get_project()
        source_dir = self.project_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        scenes_bin = self.repository.catalog.create_asset_bin("示例场景")
        accents_bin = self.repository.catalog.create_asset_bin("强调画面", scenes_bin.id)
        palette = (
            ("01-opening.png", "开场 · 工作台", (33, 48, 81), (91, 141, 239), scenes_bin.id),
            ("02-story.png", "主体 · 内容节奏", (52, 38, 62), (230, 111, 155), scenes_bin.id),
            ("03-ending.png", "收束 · 准备导出", (30, 60, 55), (72, 190, 154), accents_bin.id),
        )
        assets = []
        for index, (filename, name, background, accent, bin_id) in enumerate(palette):
            path = source_dir / filename
            self._write_sample_png(path, background, accent, index)
            assets.append(
                self.repository.catalog.add_asset(
                    Asset(
                        project_id=project.id,
                        name=name,
                        kind=AssetKind.IMAGE,
                        origin=AssetOrigin.GENERATED,
                        path=str(path),
                        managed=True,
                        bin_id=bin_id,
                        metadata=MediaMetadata(
                            width=960,
                            height=540,
                            has_video=True,
                            pixel_format="rgb24",
                            color_primaries="bt709",
                            color_transfer="bt709",
                            color_space="bt709",
                        ),
                    )
                )
            )

        editor = self.timeline(project.main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO, "视频 1")
        editor.add_track(TrackKind.AUDIO, "音频 1")
        editor.add_track(TrackKind.SUBTITLE, "字幕 1")
        clips = [
            editor.add_clip(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=index * 120,
                source_in=0,
                duration=120,
            )
            for index, asset in enumerate(assets)
        ]
        editor.create_transition(clips[0].id, clips[1].id, TransitionKind.DISSOLVE, 18)
        editor.create_transition(clips[1].id, clips[2].id, TransitionKind.FADE_BLACK, 18)
        adjustment = editor.add_clip_visual_effect(
            clips[1].id,
            VisualEffectKind.COLOR_ADJUSTMENT,
        )
        editor.update_clip_visual_effect(
            clips[1].id,
            adjustment.id,
            enabled=True,
            parameters={"brightness": 0.05, "contrast": 1.12, "saturation": 1.18},
        )
        editor.add_clip_visual_effect(clips[2].id, VisualEffectKind.VIGNETTE)
        editor.add_marker(0, "从这里开始")
        editor.add_marker(120, "观察转场和效果")
        editor.add_range(120, 240, "重点片段")

        short = self.repository.catalog.create_short_sequence("竖屏精选")
        short_editor = self.timeline(short.id)
        short_track = short_editor.add_track(TrackKind.VIDEO, "视频 1")
        for index, asset in enumerate((assets[1], assets[2])):
            short_editor.add_clip(
                track_id=short_track.id,
                asset_id=asset.id,
                timeline_start=index * 90,
                source_in=0,
                duration=90,
            )

    @staticmethod
    def _write_sample_png(
        path: Path,
        background: tuple[int, int, int],
        accent: tuple[int, int, int],
        variant: int,
    ) -> None:
        width, height = 960, 540
        rows = bytearray()
        gradient_row = bytearray()
        center = 190 + variant * 260
        for x in range(width):
            mix = max(0.0, 1.0 - abs(x - center) / 560.0) * 0.42
            gradient_row.extend(
                round(background[channel] * (1.0 - mix) + accent[channel] * mix)
                for channel in range(3)
            )

        def solid(color: tuple[int, int, int], pixels: int) -> bytes:
            return bytes(color) * pixels

        card_color = tuple(
            round(background[channel] * 0.68 + accent[channel] * 0.32)
            for channel in range(3)
        )
        for y in range(height):
            rows.append(0)
            row = bytearray(gradient_row)
            if y % 80 < 2:
                row[:] = solid(card_color, width)
            if 118 < y < 422:
                row[115 * 3 : 845 * 3] = solid(card_color, 730)
            if 170 < y < 205:
                row[155 * 3 : 520 * 3] = solid(accent, 365)
            if 235 < y < 260:
                row[155 * 3 : 750 * 3] = solid(accent, 595)
            if 280 < y < 305:
                row[155 * 3 : 680 * 3] = solid(accent, 525)
            rows.extend(row)
        signature = b"\x89PNG\r\n\x1a\n"

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        atomic_write_bytes(
            path,
            signature
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), level=7))
            + chunk(b"IEND", b"")
        )
