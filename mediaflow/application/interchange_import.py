from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.domain.collaboration import (
    ProjectChange,
    ProjectChangeSet,
    ProjectEditAction,
    ProjectEditCommand,
)
from mediaflow.domain.enums import TransitionKind
from mediaflow.domain.interchange import (
    InterchangeClipAdjustment,
    LoadedInterchangeTimeline,
)
from mediaflow.domain.project import Asset, ProjectProfile, Sequence
from mediaflow.domain.timeline import (
    Clip,
    ClipTransform,
    ClipTransformKeyframe,
    TimelineState,
    Transition,
)

from .asset_service import AssetService
from .portable_timeline_import import PortableTimelineImportService
from .ports import SequenceServiceDocuments
from .timeline_editor import TimelineEditor


class InterchangeTimelineLoaderPort(Protocol):
    def load(
        self,
        path: str | Path,
        *,
        default_profile: ProjectProfile,
        frame_rate: float | None = None,
        media_mappings: dict[str, str] | None = None,
    ) -> LoadedInterchangeTimeline: ...


class InterchangeImportService:
    """Import a validated exchange document as a new, editable native sequence."""

    def __init__(
        self,
        repository: SequenceServiceDocuments,
        assets: AssetService,
        portable: PortableTimelineImportService,
        timeline_provider: Callable[[str], TimelineEditor],
        history: ProjectEditHistory,
        loader: InterchangeTimelineLoaderPort,
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.portable = portable
        self.timeline_provider = timeline_provider
        self.history = history
        self.loader = loader

    def inspect(
        self,
        path: str | Path,
        *,
        default_profile: ProjectProfile,
        frame_rate: float | None = None,
        media_mappings: dict[str, str] | None = None,
    ) -> LoadedInterchangeTimeline:
        return self.loader.load(
            path,
            default_profile=default_profile,
            frame_rate=frame_rate,
            media_mappings=media_mappings,
        )

    def import_timeline(
        self,
        path: str | Path,
        *,
        name: str | None,
        default_profile: ProjectProfile,
        frame_rate: float | None = None,
        media_mappings: dict[str, str] | None = None,
    ) -> tuple[LoadedInterchangeTimeline, Sequence, TimelineState, dict[str, Asset], list[str]]:
        loaded = self.inspect(
            path,
            default_profile=default_profile,
            frame_rate=frame_rate,
            media_mappings=media_mappings,
        )
        if loaded.summary.missing_sources:
            raise FileNotFoundError(
                "交换时间线仍有未定位素材：" + "；".join(loaded.summary.missing_sources)
            )
        selected_name = (name or loaded.summary.name).strip()
        if not selected_name:
            raise ValueError("导入序列名称不能为空")
        sequence = self.repository.sequences.prepare_short_sequence(
            selected_name,
            loaded.summary.profile,
        )
        checkpoint = self.history.checkpoint()
        try:
            with self.repository.transaction():
                self.repository.sequences.commit_short_sequence(sequence)
                _portable, state, source_assets, subtitle_ids = self.portable.import_loaded(
                    loaded.portable,
                    sequence_id=sequence.id,
                )
                editor = self.timeline_provider(sequence.id)
                if state.sequence.profile != loaded.summary.profile:
                    editor.set_sequence_profile(loaded.summary.profile)
                state = self._apply_native_semantics(
                    editor.reload(),
                    loaded,
                    source_assets,
                )
                state = editor.replace_contents(state, label="导入交换时间线")
            self.history.restore(checkpoint)
            self.history.push(
                ProjectEditCommand(
                    label="导入交换时间线",
                    undo_actions=[
                        ProjectEditAction(
                            kind="sequence.archive-state",
                            payload={"sequence_id": sequence.id, "archived": True},
                        )
                    ],
                    redo_actions=[
                        ProjectEditAction(
                            kind="sequence.archive-state",
                            payload={"sequence_id": sequence.id, "archived": False},
                        )
                    ],
                ),
                ProjectChangeSet(
                    changes=[
                        ProjectChange(
                            path=f"/sequences/{sequence.id}",
                            action="create",
                            value={
                                "name": sequence.name,
                                "source": str(loaded.path),
                                "format": loaded.summary.format,
                            },
                        )
                    ]
                ),
            )
        except BaseException:
            self.history.restore(checkpoint)
            raise
        return loaded, self.repository.sequences.get_sequence(sequence.id), state, source_assets, subtitle_ids

    def _apply_native_semantics(
        self,
        state: TimelineState,
        loaded: LoadedInterchangeTimeline,
        source_assets: dict[str, Asset],
    ) -> TimelineState:
        specs = {item.clip_id: item for item in loaded.native_clips}
        updated: list[Clip] = []
        for clip in state.clips:
            spec = specs.get(clip.id)
            if spec is None:
                updated.append(clip)
                continue
            asset = source_assets[spec.source_id]
            transform, keyframes = self._native_transform(
                spec.adjustment,
                asset,
                loaded.summary.profile,
                spec.duration,
            )
            imported = clip.model_copy(
                update={
                    "timeline_start": spec.timeline_start,
                    "source_in": spec.source_in,
                    "duration": spec.duration,
                    "media_kind": spec.media_kind,
                    "speed_numerator": spec.speed_numerator,
                    "speed_denominator": spec.speed_denominator,
                    "pitch_compensation": spec.pitch_compensation,
                    "audio": spec.audio,
                    "transform": transform,
                    "transform_keyframes": keyframes,
                }
            )
            updated.append(imported)
        clips = {item.id: item for item in updated}
        transitions: list[Transition] = []
        for item in loaded.transitions:
            left = clips[item.left_clip_id]
            right = clips[item.right_clip_id]
            if left.track_id != right.track_id or left.timeline_end != right.timeline_start:
                raise ValueError("交换时间线转场两侧必须位于同一轨道并首尾相接")
            transitions.append(
                Transition(
                    track_id=left.track_id,
                    left_clip_id=left.id,
                    right_clip_id=right.id,
                    kind=TransitionKind.DISSOLVE,
                    duration=min(item.duration, left.duration, right.duration),
                )
            )
        return state.model_copy(update={"clips": updated, "transitions": transitions})

    def _native_transform(
        self,
        adjustment: InterchangeClipAdjustment,
        asset: Asset,
        profile: ProjectProfile,
        duration: int,
    ) -> tuple[ClipTransform, list[ClipTransformKeyframe]]:
        base_values = {
            "position": adjustment.position or "0 0",
            "scale": adjustment.scale or "1 1",
            "rotation": adjustment.rotation or "0",
            "opacity": adjustment.opacity or "1",
            "crop_left": adjustment.crop_left or "0",
            "crop_top": adjustment.crop_top or "0",
            "crop_right": adjustment.crop_right or "0",
            "crop_bottom": adjustment.crop_bottom or "0",
        }
        base = self._transform_from_values(base_values, asset, profile)
        frames = sorted(
            {
                item.frame
                for values in adjustment.animations.values()
                for item in values
                if item.frame < duration
            }
        )
        keyframes = [
            ClipTransformKeyframe(
                timeline_offset=frame,
                transform=self._transform_from_values(
                    {
                        name: self._animated_value(
                            base_values[name],
                            adjustment.animations.get(name, []),
                            frame,
                        )
                        for name in base_values
                    },
                    asset,
                    profile,
                ),
            )
            for frame in frames
        ]
        return base, keyframes

    @staticmethod
    def _animated_value(base: str, points: list, frame: int) -> str:
        values = [(0, base)]
        values.extend((int(item.frame), str(item.value)) for item in points)
        by_frame = {point_frame: value for point_frame, value in values}
        ordered = sorted(by_frame.items())
        if frame <= ordered[0][0]:
            return ordered[0][1]
        if frame >= ordered[-1][0]:
            return ordered[-1][1]
        left, right = next(
            pair for pair in zip(ordered, ordered[1:], strict=False) if pair[0][0] <= frame <= pair[1][0]
        )
        progress = (frame - left[0]) / (right[0] - left[0])
        left_values = [float(item) for item in left[1].split()]
        right_values = [float(item) for item in right[1].split()]
        if len(left_values) != len(right_values):
            raise ValueError("FCPXML 关键帧参数维度不一致")
        return " ".join(
            f"{(start + (end - start) * progress):g}"
            for start, end in zip(left_values, right_values, strict=True)
        )

    @staticmethod
    def _transform_from_values(
        values: dict[str, str],
        asset: Asset,
        profile: ProjectProfile,
    ) -> ClipTransform:
        position = [float(item) for item in values["position"].split()]
        scale = [float(item) for item in values["scale"].split()]
        if len(position) != 2 or len(scale) != 2:
            raise ValueError("FCPXML position 和 scale 必须各有两个数值")
        source_width = asset.metadata.width or profile.width
        source_height = asset.metadata.height or profile.height
        horizontal_crop_scale = 100.0 * source_width / source_height
        return ClipTransform(
            x=position[0] * profile.height / profile.width,
            y=-position[1],
            scale_x=scale[0],
            scale_y=scale[1],
            rotation=-float(values["rotation"]),
            opacity=float(values["opacity"]),
            crop_left=float(values["crop_left"]) / horizontal_crop_scale,
            crop_top=float(values["crop_top"]) / 100.0,
            crop_right=float(values["crop_right"]) / horizontal_crop_scale,
            crop_bottom=float(values["crop_bottom"]) / 100.0,
        )
