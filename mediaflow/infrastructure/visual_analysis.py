from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, cast

import cv2
import numpy as np

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.storage_names import require_windows_interop_path
from mediaflow.domain.timebase import (
    source_interval_for_timeline_interval,
    timeline_offset_for_source_frame,
)
from mediaflow.domain.timeline import Clip, ClipTransform, ClipTransformKeyframe
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.runtime_paths import RuntimePaths


class SceneDetectionService:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.ffmpeg = FfmpegRunner(paths.ffmpeg)

    def detect(
        self,
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        *,
        threshold: float = 0.35,
        check_cancelled=None,
        progress=None,
    ) -> list[int]:
        if not 0.05 <= threshold <= 0.95:
            raise ValueError("场景检测阈值必须在 0.05 到 0.95 之间")
        source = require_windows_interop_path(
            Path(source).resolve(strict=True)
        )
        source_fps = profile.fps
        source_start, source_end = source_interval_for_timeline_interval(
            clip.source_in,
            0,
            clip.duration,
            clip.speed_numerator,
            clip.speed_denominator,
            freeze_source_frame=clip.freeze_source_frame,
        )
        consumed = source_end - source_start
        duration_seconds = consumed / source_fps
        if check_cancelled:
            check_cancelled()
        command = [
            "-ss",
            f"{source_start / source_fps:.9f}",
            "-t",
            f"{duration_seconds:.9f}",
            "-i",
            str(source),
            "-filter_complex",
            (
                "[0:v]split=2[scene_input][clock_input];"
                f"[scene_input]select='gt(scene,{threshold:g})',showinfo[scenes];"
                "[clock_input]null[clock]"
            ),
            "-map",
            "[scenes]",
            "-map",
            "[clock]",
            "-an",
            "-f",
            "null",
            "-",
        ]
        completed = self.ffmpeg.run_progress(
            command,
            total_seconds=duration_seconds,
            on_position=(
                lambda position: progress(
                    OperationProgress.determinate(
                        "scene_detection_analyzing",
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )
                if progress
                else None
            ),
            timeout=1800,
            check_cancelled=check_cancelled,
        )
        if completed.returncode != 0:
            raise RuntimeError("场景检测失败：" + completed.stderr[-1200:])
        if check_cancelled:
            check_cancelled()
        seconds = [
            float(value)
            for value in re.findall(r"pts_time:([-+]?[0-9.]+)", completed.stderr)
        ]
        output: list[int] = []
        for relative_seconds in seconds:
            source_frame = source_start + round(relative_seconds * source_fps)
            try:
                local_frame = timeline_offset_for_source_frame(
                    clip.source_in,
                    source_frame,
                    clip.speed_numerator,
                    clip.speed_denominator,
                    freeze_source_frame=clip.freeze_source_frame,
                )
            except ValueError:
                continue
            timeline_frame = clip.timeline_start + local_frame
            if clip.timeline_start < timeline_frame < clip.timeline_end:
                output.append(timeline_frame)
        return sorted(set(output))


class SubjectMotionService:
    def analyze(
        self,
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        *,
        mode: str,
        check_cancelled=None,
        progress=None,
    ) -> list[ClipTransformKeyframe]:
        if mode not in {"auto_reframe", "subject_tracking"}:
            raise ValueError("未知的画面跟踪模式")
        source = require_windows_interop_path(
            Path(source).resolve(strict=True)
        )
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise RuntimeError(f"无法读取视频素材：{source}")
        try:
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if width <= 0 or height <= 0:
                raise RuntimeError("视频素材没有可用的画面尺寸")
            low, source_end = source_interval_for_timeline_interval(
                clip.source_in,
                0,
                clip.duration,
                clip.speed_numerator,
                clip.speed_denominator,
                freeze_source_frame=clip.freeze_source_frame,
            )
            high = source_end - 1
            project_step = max(1, round(profile.fps / 5))
            source_frames = list(range(low, high + 1, project_step))
            if not source_frames or source_frames[-1] != high:
                source_frames.append(high)
            previous_gray: np.ndarray | None = None
            center = np.array([0.5, 0.5], dtype=np.float64)
            keyframes: list[ClipTransformKeyframe] = []
            for index, source_frame in enumerate(source_frames):
                if check_cancelled:
                    check_cancelled()
                capture.set(
                    cv2.CAP_PROP_POS_MSEC,
                    source_frame / profile.fps * 1000.0,
                )
                ok, frame = capture.read()
                if not ok:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (max(160, width // 4), max(90, height // 4)))
                confidence = 0.2
                observed = None
                if previous_gray is not None:
                    difference = cv2.absdiff(gray, previous_gray)
                    threshold_value = max(14.0, float(np.percentile(difference, 88)))
                    mask = difference >= threshold_value
                    coordinates = np.argwhere(mask)
                    if len(coordinates) >= 12:
                        y_mean, x_mean = coordinates.mean(axis=0)
                        observed = np.array(
                            [x_mean / gray.shape[1], y_mean / gray.shape[0]],
                            dtype=np.float64,
                        )
                        confidence = min(0.75, len(coordinates) / mask.size * 8.0)
                if observed is None:
                    horizontal = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
                    vertical = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
                    saliency = cv2.magnitude(horizontal, vertical)
                    threshold_value = float(np.percentile(saliency, 82))
                    coordinates = np.argwhere(saliency >= max(8.0, threshold_value))
                    if len(coordinates) >= 12:
                        y_mean, x_mean = coordinates.mean(axis=0)
                        observed = np.array(
                            [x_mean / gray.shape[1], y_mean / gray.shape[0]],
                            dtype=np.float64,
                        )
                        confidence = min(0.6, len(coordinates) / saliency.size * 4.0)
                if observed is not None:
                    center = center * 0.72 + observed * 0.28
                transform = self._transform_for_center(
                    float(center[0]),
                    float(center[1]),
                    width,
                    height,
                    profile,
                    mode,
                )
                keyframes.append(
                    ClipTransformKeyframe(
                        source_frame=source_frame,
                        transform=transform,
                        source=cast(
                            Literal["auto_reframe", "subject_tracking"],
                            mode,
                        ),
                        confidence=confidence,
                    )
                )
                previous_gray = gray
                if progress:
                    progress(
                        OperationProgress.determinate(
                            "subject_tracking_analyzing",
                            completed=index + 1,
                            total=len(source_frames),
                            unit="frames",
                        )
                    )
            if not keyframes:
                raise RuntimeError("没有从视频中读取到可跟踪画面")
            return keyframes
        finally:
            capture.release()

    @staticmethod
    def _transform_for_center(
        center_x: float,
        center_y: float,
        source_width: int,
        source_height: int,
        profile: ProjectProfile,
        mode: str,
    ) -> ClipTransform:
        source_ratio = source_width / source_height
        target_ratio = profile.width / profile.height
        zoom = max(source_ratio / target_ratio, target_ratio / source_ratio, 1.0)
        if mode == "subject_tracking":
            zoom = max(1.15, zoom)
        scaled = zoom * 100.0
        x = 50.0 - center_x * scaled
        y = 50.0 - center_y * scaled
        minimum = 100.0 - scaled
        x = min(0.0, max(minimum, x))
        y = min(0.0, max(minimum, y))
        return ClipTransform(x=x, y=y, scale_x=zoom, scale_y=zoom)


def write_visual_analysis(path: Path, payload: dict) -> Path:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    return path
