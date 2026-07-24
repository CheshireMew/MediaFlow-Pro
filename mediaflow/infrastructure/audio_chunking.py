from __future__ import annotations

import json
import re
import statistics
import uuid
from collections.abc import Callable
from pathlib import Path

from mediaflow.domain.progress import OperationProgress
from mediaflow.infrastructure.process_observers import (
    FfmpegProgressObserver,
    ffmpeg_progress_command,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable, run_cancellable_streaming


class AudioPreparationService:
    STRONG_ANTIPHASE_MEDIAN_THRESHOLD = -0.75
    STRONG_ANTIPHASE_FRAME_RATIO = 0.60

    def __init__(self, paths: RuntimePaths | None = None):
        self.paths = paths or RuntimePaths.discover()

    def prepare_for_asr(
        self,
        media_path: str | Path,
        *,
        start_seconds: float = 0.0,
        end_seconds: float | None = None,
        check_cancelled: Callable[[], None] | None = None,
        progress: Callable[[OperationProgress], None] | None = None,
    ) -> Path:
        source = Path(media_path).resolve(strict=True)
        if progress:
            progress(OperationProgress.indeterminate("preparing_asr_audio_probe"))
        channels, source_duration = self._probe_audio(source)
        start = max(0.0, float(start_seconds))
        end = (
            source_duration
            if end_seconds is None
            else min(source_duration, float(end_seconds))
        )
        if end <= start:
            raise ValueError("准备转录音频的源区间无效")
        duration = end - start
        audio_filter = None
        if channels == 2:
            phase_values = self._measure_stereo_phase(
                source,
                duration,
                source_start=start,
                check_cancelled=check_cancelled,
                progress=progress,
            )
            audio_filter = (
                "pan=mono|c0=0.5*c0-0.5*c1"
                if self._is_strongly_antiphase(phase_values)
                else "pan=mono|c0=0.5*c0+0.5*c1"
            )
        output = self.paths.runtime_dir / "cache" / "asr-inputs" / "runs" / str(uuid.uuid4()) / "input.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(source),
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:a:0",
            "-vn",
        ]
        if audio_filter:
            command.extend(["-af", audio_filter])
        else:
            command.extend(["-ac", "1"])
        command.extend(["-ar", "16000", "-c:a", "pcm_s16le", str(output)])
        observer = (
            FfmpegProgressObserver(
                duration,
                lambda position: progress(
                    OperationProgress.determinate(
                        "preparing_asr_audio",
                        completed=position,
                        total=duration,
                        unit="media_seconds",
                    )
                )
                if progress
                else None,
            )
            if duration > 0
            else None
        )
        if observer is None and progress:
            progress(OperationProgress.indeterminate("preparing_asr_audio"))
        result = run_cancellable_streaming(
            ffmpeg_progress_command(command),
            on_stderr_line=observer,
            check_cancelled=check_cancelled,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            detail = str(result.stderr or "FFmpeg 没有生成 ASR 输入").strip()
            raise RuntimeError(f"准备 ASR 音频失败：{detail}")
        return output

    def _probe_audio(self, source: Path) -> tuple[int, float]:
        result = run_cancellable(
            [
                str(self.paths.ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=channels:format=duration",
                "-of",
                "json",
                str(source),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(str(result.stderr or "无法读取音频流").strip())
        payload = json.loads(str(result.stdout or "{}"))
        streams = payload.get("streams") or []
        if not streams:
            raise ValueError(f"媒体没有音频流：{source}")
        return (
            int(streams[0]["channels"]),
            max(0.0, float(payload.get("format", {}).get("duration") or 0.0)),
        )

    def _measure_stereo_phase(
        self,
        source: Path,
        duration: float,
        *,
        source_start: float,
        check_cancelled: Callable[[], None] | None,
        progress: Callable[[OperationProgress], None] | None,
    ) -> list[float]:
        windows = (
            [(0.0, None)]
            if duration <= 90.0
            else [
                (0.0, 30.0),
                (max(duration / 2.0 - 15.0, 0.0), 30.0),
                (max(duration - 30.0, 0.0), 30.0),
            ]
        )
        values: list[float] = []
        analysis_total = sum(
            duration if window_duration is None else window_duration
            for _relative_start, window_duration in windows
        )
        analysis_completed = 0.0
        for relative_start, window_duration in windows:
            start = source_start + relative_start
            command = [str(self.paths.ffmpeg), "-hide_banner", "-v", "error"]
            if start > 0:
                command.extend(["-ss", f"{start:.3f}"])
            command.extend(["-i", str(source)])
            if window_duration is not None:
                command.extend(["-t", f"{window_duration:.3f}"])
            command.extend(
                [
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-af",
                    "aphasemeter=video=0,ametadata=print:key=lavfi.aphasemeter.phase:file=-",
                    "-f",
                    "null",
                    "-",
                ]
            )
            measured_duration = duration if window_duration is None else window_duration
            if measured_duration > 0 and progress is not None:

                def report_channel_analysis(
                    position: float,
                    measured_total: float = measured_duration,
                    completed_before: float = analysis_completed,
                ) -> None:
                    progress(
                        OperationProgress.determinate(
                            "preparing_asr_channel_analysis",
                            completed=min(
                                analysis_total,
                                completed_before + min(position, measured_total),
                            ),
                            total=analysis_total,
                            unit="media_seconds",
                        )
                    )

                observer = FfmpegProgressObserver(
                    measured_duration,
                    report_channel_analysis,
                )
            else:
                observer = None
            result = run_cancellable_streaming(
                ffmpeg_progress_command(command),
                on_stderr_line=observer,
                check_cancelled=check_cancelled,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                raise RuntimeError(str(result.stderr or "立体声相位检测失败").strip())
            values.extend(
                float(match.group(1))
                for match in re.finditer(
                    r"lavfi\.aphasemeter\.phase=(-?\d+(?:\.\d+)?)",
                    str(result.stdout or ""),
                )
            )
            analysis_completed += measured_duration
        return values

    @classmethod
    def _is_strongly_antiphase(cls, values: list[float]) -> bool:
        if not values:
            return False
        median = statistics.median(values)
        negative_ratio = sum(value < cls.STRONG_ANTIPHASE_MEDIAN_THRESHOLD for value in values) / len(values)
        return (
            median < cls.STRONG_ANTIPHASE_MEDIAN_THRESHOLD
            and negative_ratio >= cls.STRONG_ANTIPHASE_FRAME_RATIO
        )


class AudioChunkingService:
    def __init__(self, paths: RuntimePaths | None = None):
        self.paths = paths or RuntimePaths.discover()

    def duration_seconds(self, media_path: str | Path) -> float:
        source = Path(media_path).resolve(strict=True)
        result = run_cancellable(
            [
                str(self.paths.ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(source),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(str(result.stderr or "无法读取媒体时长").strip())
        payload = json.loads(str(result.stdout or "{}"))
        return max(0.0, float(payload.get("format", {}).get("duration") or 0.0))

    def detect_silence(
        self,
        media_path: str | Path,
        *,
        threshold_db: float = -30.0,
        minimum_duration: float = 0.5,
        duration_seconds: float,
        check_cancelled: Callable[[], None] | None = None,
        progress: Callable[[OperationProgress], None] | None = None,
    ) -> list[tuple[float, float]]:
        source = Path(media_path).resolve(strict=True)
        command = [
                str(self.paths.ffmpeg),
                "-hide_banner",
                "-v",
                "info",
                "-i",
                str(source),
                "-af",
                f"silencedetect=noise={threshold_db:g}dB:d={minimum_duration:g}",
                "-f",
                "null",
                "-",
            ]
        observer = FfmpegProgressObserver(
            duration_seconds,
            lambda position: progress(
                OperationProgress.determinate(
                    "asr_silence_detection",
                    completed=position,
                    total=duration_seconds,
                    unit="media_seconds",
                )
            )
            if progress
            else None,
        )
        result = run_cancellable_streaming(
            ffmpeg_progress_command(command),
            on_stderr_line=observer,
            check_cancelled=check_cancelled,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(str(result.stderr or "静音检测失败").strip())
        intervals: list[tuple[float, float]] = []
        start: float | None = None
        for line in str(result.stderr or "").splitlines():
            start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
            if start_match:
                start = float(start_match.group(1))
            end_match = re.search(r"silence_end:\s*([0-9.]+)", line)
            if end_match and start is not None:
                end = float(end_match.group(1))
                if end > start:
                    intervals.append((start, end))
                start = None
        if start is not None:
            duration = self.duration_seconds(source)
            if duration > start:
                intervals.append((start, duration))
        return intervals

    @staticmethod
    def split_points(
        total_duration: float,
        silence_intervals: list[tuple[float, float]],
        *,
        target_duration: float = 600.0,
    ) -> list[float]:
        points: list[float] = []
        current = 0.0
        while current + target_duration < total_duration:
            target = current + target_duration
            search_start = max(current + 60.0, target - 60.0)
            search_end = min(total_duration - 10.0, target + 60.0)
            choices = [
                interval for interval in silence_intervals if search_start <= interval[0] <= search_end
            ]
            if choices:
                nearest: tuple[float, float] = min(
                    choices,
                    key=lambda item: abs(item[0] - target),
                )
                point = sum(nearest) / 2.0
            else:
                point = target
            if point <= current:
                point = target
            points.append(point)
            current = point
        return points

    def extract_chunks(
        self,
        media_path: str | Path,
        split_points: list[float],
        *,
        total_duration: float,
        check_cancelled: Callable[[], None] | None = None,
        progress: Callable[[OperationProgress], None] | None = None,
    ) -> list[tuple[Path, float]]:
        source = Path(media_path).resolve(strict=True)
        output_dir = self.paths.runtime_dir / "cache" / "asr-chunks" / "runs" / str(uuid.uuid4())
        output_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[tuple[Path, float]] = []
        starts = [0.0, *split_points]
        ends = [*split_points, total_duration]
        for index, (start, end) in enumerate(zip(starts, ends, strict=True)):
            output = output_dir / f"chunk-{index:03d}.wav"
            command = [
                str(self.paths.ffmpeg),
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-ss",
                f"{start:.6f}",
                "-i",
                str(source),
            ]
            chunk_duration = end - start
            command.extend(["-t", f"{chunk_duration:.6f}"])
            command.extend(
                [
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ]
            )
            if progress is not None:

                def report_chunk_extraction(
                    position: float,
                    measured_total: float = chunk_duration,
                    completed_before: float = start,
                ) -> None:
                    progress(
                        OperationProgress.determinate(
                            "asr_chunk_extracting",
                            completed=min(
                                total_duration,
                                completed_before + min(position, measured_total),
                            ),
                            total=total_duration,
                            unit="media_seconds",
                        )
                    )

                observer = FfmpegProgressObserver(
                    chunk_duration,
                    report_chunk_extraction,
                )
            else:
                observer = None
            result = run_cancellable_streaming(
                ffmpeg_progress_command(command),
                on_stderr_line=observer,
                check_cancelled=check_cancelled,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                detail = str(result.stderr or "FFmpeg 没有生成音频分块").strip()
                raise RuntimeError(f"ASR 音频分块失败：{detail}")
            chunks.append((output, start))
        return chunks
