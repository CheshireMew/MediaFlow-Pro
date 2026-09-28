from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

import numpy as np

from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.multicam import (
    MulticamAngle,
    MulticamAngleSyncSpec,
    MulticamCut,
    MulticamGroup,
    MulticamSyncAnalysis,
)
from mediaflow.domain.timeline import Clip, TimelineState, Track
from mediaflow.waveform_cache import inspect_waveform_cache, read_waveform_peaks

TimelineMutation = Callable[[TimelineState], None]
TimelineCommit = Callable[[str, TimelineMutation], None]


class TimelineMulticamEditing:
    def __init__(
        self,
        repository: TimelineEditorDocuments,
        snapshot: Callable[[], TimelineState],
        apply_change: TimelineCommit,
    ) -> None:
        self.repository = repository
        self.snapshot = snapshot
        self.apply_change = apply_change

    def create_group(
        self,
        name: str,
        angles: list[MulticamAngleSyncSpec],
        *,
        timeline_start: int,
        duration: int,
        sync_offset: int = 0,
        initial_angle_id: str | None = None,
        sync_method: str = "manual",
        sync_confidence: float | None = None,
        audio_strategy: str = "follow_video",
        master_audio_angle_id: str | None = None,
    ) -> MulticamGroup:
        if len(angles) < 2:
            raise ValueError("多机位组至少需要两个视频素材")
        if len(angles) > 16:
            raise ValueError("一个多机位组最多包含 16 个机位")
        if len({item.asset_id for item in angles}) != len(angles):
            raise ValueError("多机位组不能重复使用同一素材")
        if timeline_start < 0 or duration <= 0 or not 0 <= sync_offset < duration:
            raise ValueError("多机位节目区间或同步点无效")
        resolved_angles: list[MulticamAngle] = []
        for specification in angles:
            asset = self.repository.assets.get_asset(specification.asset_id)
            if asset.kind != AssetKind.VIDEO:
                raise ValueError(f"多机位只接受视频素材：{asset.name}")
            if asset.metadata.duration_frames <= 0:
                raise ValueError(f"多机位素材缺少可靠时长：{asset.name}")
            source_in = specification.sync_frame - sync_offset
            if source_in < 0 or source_in + duration > asset.metadata.duration_frames:
                raise ValueError(f"同步后的节目区间超出素材范围：{asset.name}")
            resolved_angles.append(
                MulticamAngle(
                    id=specification.id,
                    asset_id=asset.id,
                    name=specification.name,
                    source_in=source_in,
                )
            )
        angle_ids = {item.id for item in resolved_angles}
        active_angle = initial_angle_id or resolved_angles[0].id
        if active_angle not in angle_ids:
            raise KeyError(active_angle)
        master_angle = master_audio_angle_id or active_angle
        if audio_strategy == "master_angle" and master_angle not in angle_ids:
            raise KeyError(master_angle)
        assets_by_id = {
            item.asset_id: self.repository.assets.get_asset(item.asset_id)
            for item in resolved_angles
        }
        if audio_strategy == "master_angle":
            master_asset_id = next(
                item.asset_id for item in resolved_angles if item.id == master_angle
            )
            if not assets_by_id[master_asset_id].metadata.has_audio:
                raise ValueError("选中的主音频机位没有音频流")
            use_program_audio = True
        else:
            use_program_audio = all(
                assets_by_id[item.asset_id].metadata.has_audio
                for item in resolved_angles
            )
        state = self.snapshot()
        video_track = Track(
            sequence_id=state.sequence.id,
            name=f"多机位节目 · {' '.join(name.split()) or '未命名'}",
            kind=TrackKind.VIDEO,
            position=len(state.tracks),
        )
        audio_track = (
            Track(
                sequence_id=state.sequence.id,
                name=f"多机位节目音频 · {' '.join(name.split()) or '未命名'}",
                kind=TrackKind.AUDIO,
                position=len(state.tracks) + 1,
            )
            if use_program_audio
            else None
        )
        group, clips = self._program(
            MulticamGroup(
                sequence_id=state.sequence.id,
                name=" ".join(name.split()) or "多机位",
                program_track_id=video_track.id,
                program_audio_track_id=audio_track.id if audio_track else None,
                timeline_start=timeline_start,
                duration=duration,
                sync_offset=sync_offset,
                sync_method=cast(
                    Literal["manual", "timecode", "waveform"], sync_method
                ),
                sync_confidence=sync_confidence,
                audio_strategy=cast(
                    Literal["follow_video", "master_angle"], audio_strategy
                ),
                master_audio_angle_id=(
                    master_angle
                    if audio_strategy == "master_angle"
                    else None
                ),
                angles=resolved_angles,
                cuts=[MulticamCut(frame=0, angle_id=active_angle)],
                program_clip_ids=[new_id()],
                program_audio_clip_ids=[new_id()] if audio_track else [],
            )
        )

        def mutate(candidate: TimelineState) -> None:
            candidate.tracks.append(video_track)
            if audio_track is not None:
                candidate.tracks.append(audio_track)
            candidate.clips.extend(clips)
            candidate.multicam_groups.append(group)

        self.apply_change("创建多机位节目", mutate)
        return self.get(group.id)

    def switch_angle(
        self,
        group_id: str,
        angle_id: str,
        timeline_frame: int,
    ) -> MulticamGroup:
        group = self.get(group_id)
        if angle_id not in {item.id for item in group.angles}:
            raise KeyError(angle_id)
        offset = timeline_frame - group.timeline_start
        if not 0 <= offset < group.duration:
            raise ValueError("切换帧必须位于多机位节目区间内")
        by_frame = {item.frame: item for item in group.cuts}
        by_frame[offset] = MulticamCut(frame=offset, angle_id=angle_id)
        ordered = [by_frame[frame] for frame in sorted(by_frame)]
        normalized: list[MulticamCut] = []
        for cut in ordered:
            if normalized and normalized[-1].angle_id == cut.angle_id:
                continue
            normalized.append(cut)
        changed, clips = self._program(
            group.model_copy(
                update={
                    "cuts": normalized,
                    "program_clip_ids": [new_id() for _item in normalized],
                    "program_audio_clip_ids": (
                        [new_id()]
                        if group.audio_strategy == "master_angle"
                        else (
                            [new_id() for _item in normalized]
                            if group.program_audio_track_id is not None
                            else []
                        )
                    ),
                }
            )
        )

        def mutate(state: TimelineState) -> None:
            old_ids = set(group.program_clip_ids) | set(group.program_audio_clip_ids)
            state.clips = [item for item in state.clips if item.id not in old_ids]
            state.clips.extend(clips)
            self._replace(state, changed)

        self.apply_change("切换多机位角度", mutate)
        return self.get(group_id)

    def list_groups(self) -> list[MulticamGroup]:
        return list(self.snapshot().multicam_groups)

    def get(self, group_id: str) -> MulticamGroup:
        try:
            return next(item for item in self.snapshot().multicam_groups if item.id == group_id)
        except StopIteration as error:
            raise KeyError(group_id) from error

    def analyze_sync(
        self,
        asset_ids: list[str],
        method: str,
    ) -> MulticamSyncAnalysis:
        if len(asset_ids) < 2 or len(asset_ids) > 16:
            raise ValueError("多机位同步分析需要 2–16 个素材")
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("多机位同步分析不能重复使用同一素材")
        assets = [self.repository.assets.get_asset(asset_id) for asset_id in asset_ids]
        if any(asset.kind != AssetKind.VIDEO for asset in assets):
            raise ValueError("多机位同步只接受视频素材")
        if method == "timecode":
            starts = [asset.metadata.start_timecode_frame for asset in assets]
            if any(value is None for value in starts):
                missing = [asset.name for asset, value in zip(assets, starts, strict=True) if value is None]
                raise ValueError("以下素材没有可用时码：" + "、".join(missing))
            values = [int(value) for value in starts if value is not None]
            origin = max(values)
            sync_frames = [origin - value for value in values]
            confidence = 1.0
        elif method == "waveform":
            sync_frames, confidence = self._waveform_offsets(assets)
        else:
            raise ValueError("多机位同步分析只支持时码或音频波形")
        angles = [
            MulticamAngleSyncSpec(
                asset_id=asset.id,
                name=asset.name,
                sync_frame=sync_frame,
            )
            for asset, sync_frame in zip(assets, sync_frames, strict=True)
        ]
        return MulticamSyncAnalysis(
            method=cast(Literal["timecode", "waveform"], method),
            reference_asset_id=assets[0].id,
            angles=angles,
            confidence=confidence,
            offset_span_frames=max(sync_frames) - min(sync_frames),
        )

    def _waveform_offsets(self, assets) -> tuple[list[int], float]:
        signals: list[np.ndarray] = []
        seconds_per_sample: list[float] = []
        for asset in assets:
            if not asset.waveform_path:
                raise ValueError(f"素材尚未生成音频波形：{asset.name}")
            path = Path(asset.waveform_path)
            if not path.is_absolute():
                path = Path(self.repository.project_dir) / path
            header = inspect_waveform_cache(path)
            level = header.levels[0]
            peaks = read_waveform_peaks(
                path,
                offset=level.offset,
                count=level.count,
                first=0,
                last=level.count,
            )
            signal = np.asarray(
                [max(abs(low), abs(high)) for low, high in peaks],
                dtype=np.float64,
            )
            if signal.size < 32 or float(np.std(signal)) < 1e-6:
                raise ValueError(f"素材波形缺少可识别的声音事件：{asset.name}")
            stride = max(1, int(np.ceil(signal.size / 131072)))
            signal = signal[: signal.size // stride * stride].reshape(-1, stride).max(axis=1)
            signal = (signal - np.mean(signal)) / np.std(signal)
            signals.append(signal)
            seconds_per_sample.append(level.block_size * stride / header.sample_rate)
        reference = signals[0]
        reference_step = seconds_per_sample[0]
        relative_seconds = [0.0]
        confidences: list[float] = []
        for candidate, candidate_step in zip(signals[1:], seconds_per_sample[1:], strict=True):
            if abs(candidate_step - reference_step) > 1e-9:
                duration = candidate.size * candidate_step
                target_count = max(32, round(duration / reference_step))
                candidate = np.interp(
                    np.linspace(0.0, 1.0, target_count, endpoint=False),
                    np.linspace(0.0, 1.0, candidate.size, endpoint=False),
                    candidate,
                )
            lag, confidence = self._fft_lag(reference, candidate)
            relative_seconds.append(-lag * reference_step)
            confidences.append(confidence)
        minimum = min(relative_seconds)
        profile = self.snapshot().sequence.profile
        frames = [
            max(
                0,
                round(
                    (value - minimum)
                    * profile.fps_numerator
                    / profile.fps_denominator
                ),
            )
            for value in relative_seconds
        ]
        return frames, min(confidences, default=1.0)

    @staticmethod
    def _fft_lag(reference: np.ndarray, candidate: np.ndarray) -> tuple[int, float]:
        size = reference.size + candidate.size - 1
        fft_size = 1 << (size - 1).bit_length()
        correlation = np.fft.irfft(
            np.fft.rfft(reference, fft_size)
            * np.fft.rfft(candidate[::-1], fft_size),
            fft_size,
        )[:size]
        peak_index = int(np.argmax(correlation))
        peak = float(correlation[peak_index])
        lag = peak_index - (candidate.size - 1)
        denominator = float(np.linalg.norm(reference) * np.linalg.norm(candidate))
        coefficient = max(0.0, min(1.0, peak / denominator if denominator else 0.0))
        exclusion = max(4, min(reference.size, candidate.size) // 200)
        comparison = correlation.copy()
        comparison[max(0, peak_index - exclusion) : peak_index + exclusion + 1] = -np.inf
        second = float(np.max(comparison)) if comparison.size else 0.0
        uniqueness = max(0.0, min(1.0, (peak - second) / max(abs(peak), 1e-9)))
        return lag, coefficient * (0.5 + 0.5 * uniqueness)

    @staticmethod
    def _program(group: MulticamGroup) -> tuple[MulticamGroup, list[Clip]]:
        angles = {item.id: item for item in group.angles}
        clips: list[Clip] = []
        for index, cut in enumerate(group.cuts):
            end = group.cuts[index + 1].frame if index + 1 < len(group.cuts) else group.duration
            angle = angles[cut.angle_id]
            clips.append(
                Clip(
                    id=group.program_clip_ids[index],
                    track_id=group.program_track_id,
                    asset_id=angle.asset_id,
                    timeline_start=group.timeline_start + cut.frame,
                    source_in=angle.source_in + cut.frame,
                    duration=end - cut.frame,
                    media_kind=ClipMediaKind.VIDEO_ONLY,
                )
            )
            if group.audio_strategy == "follow_video" and group.program_audio_track_id:
                clips.append(
                    Clip(
                        id=group.program_audio_clip_ids[index],
                        track_id=group.program_audio_track_id,
                        asset_id=angle.asset_id,
                        timeline_start=group.timeline_start + cut.frame,
                        source_in=angle.source_in + cut.frame,
                        duration=end - cut.frame,
                        media_kind=ClipMediaKind.AUDIO_ONLY,
                    )
                )
        if group.audio_strategy == "master_angle":
            assert group.master_audio_angle_id is not None
            assert group.program_audio_track_id is not None
            master = angles[group.master_audio_angle_id]
            clips.append(
                Clip(
                    id=group.program_audio_clip_ids[0],
                    track_id=group.program_audio_track_id,
                    asset_id=master.asset_id,
                    timeline_start=group.timeline_start,
                    source_in=master.source_in,
                    duration=group.duration,
                    media_kind=ClipMediaKind.AUDIO_ONLY,
                )
            )
        return group, clips

    @staticmethod
    def _replace(state: TimelineState, group: MulticamGroup) -> None:
        try:
            index = next(
                index for index, item in enumerate(state.multicam_groups) if item.id == group.id
            )
        except StopIteration as error:
            raise KeyError(group.id) from error
        state.multicam_groups[index] = group
