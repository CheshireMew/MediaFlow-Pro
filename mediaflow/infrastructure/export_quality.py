from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from mediaflow.atomic_file import atomic_write_text, native_temporary_sibling
from mediaflow.domain.enums import ExportFormat
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project_records import ExportQualityCheck, ExportQualityReport
from mediaflow.domain.storage_names import export_quality_directory
from mediaflow.domain.timeline import TimelineState
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.mlt.export_service import ExportResult
from mediaflow.infrastructure.runtime_paths import RuntimePaths


class ExportQualityService:
    """Analyze the encoded artifact itself and persist observable QA evidence."""

    def __init__(self, project_dir: Path, paths: RuntimePaths) -> None:
        self.project_dir = project_dir
        self.paths = paths
        self.ffmpeg = FfmpegRunner(paths.ffmpeg)

    def analyze(
        self,
        state: TimelineState,
        preset: ExportPreset,
        result: ExportResult,
        *,
        report_id: str,
        progress=None,
        check_cancelled=None,
    ) -> tuple[ExportQualityReport, Path]:
        output = result.output_path
        report_dir = export_quality_directory(self.project_dir, report_id)
        streams = result.probe.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        checks = [self._stream_check(preset, video, audio)]
        if result.hardware_fallback_used:
            checks.append(
                ExportQualityCheck(
                    key="encoder_recovery",
                    label="编码器恢复",
                    status="warning",
                    summary=(
                        f"{result.requested_video_codec} 失败，已改用 "
                        f"{result.actual_video_codec} 完成同格式导出"
                    ),
                    details={
                        "reason": result.hardware_fallback_reason,
                        "requested_video_codec": result.requested_video_codec,
                        "actual_video_codec": result.actual_video_codec,
                        "hardware_failure_log_tail": result.hardware_failure_details,
                        "archived_failed_outputs": [
                            str(path) for path in result.archived_failed_outputs
                        ],
                    },
                )
            )
        checks.append(self._duration_check(state, result))
        duration_seconds = max(
            0.001,
            float((result.probe.get("format") or {}).get("duration") or 0)
            or (result.end_frame - result.start_frame) / state.sequence.profile.fps,
        )
        analysis_log, analysis_ok = self._analyze_media(
            output,
            video is not None,
            audio is not None,
            duration_seconds=duration_seconds,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        if analysis_ok:
            if video is not None:
                checks.extend(
                    (
                        self._event_check("black", "黑场", analysis_log, "black_start:"),
                        self._event_check("freeze", "静帧", analysis_log, "freeze_start:"),
                    )
                )
            if audio is not None:
                checks.extend(
                    (
                        self._event_check("silence", "长静音", analysis_log, "silence_start:"),
                        self._true_peak_check(analysis_log),
                    )
                )
        else:
            checks.append(
                ExportQualityCheck(
                    key="analysis",
                    label="媒体扫描",
                    status="failed",
                    summary="FFmpeg 无法完成成片内容扫描",
                    details={"log_tail": analysis_log[-2000:]},
                )
            )
        checks.append(self._safe_area_check(preset))
        proof_frames, proof_check = self._proof_frames(
            output,
            report_dir,
            state,
            result,
            enabled=video is not None and preset.format != ExportFormat.AUDIO,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        checks.append(proof_check)
        report = ExportQualityReport(
            id=report_id,
            output_path=str(output),
            passed=not any(check.status == "failed" for check in checks),
            checks=checks,
            proof_frames=[str(path) for path in proof_frames],
            sha256=sha256_file(
                output,
                progress=(
                    lambda completed, total: progress(
                        OperationProgress.determinate(
                            "export_quality_hashing",
                            completed=completed,
                            total=max(1, total),
                            unit="bytes",
                        )
                    )
                    if progress
                    else None
                ),
                check_cancelled=check_cancelled,
            ),
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        return report, report_path

    @staticmethod
    def _stream_check(preset: ExportPreset, video: dict | None, audio: dict | None) -> ExportQualityCheck:
        required_video = preset.format != ExportFormat.AUDIO
        missing = []
        if required_video and video is None:
            missing.append("视频")
        if preset.audio_codec and audio is None:
            missing.append("音频")
        return ExportQualityCheck(
            key="streams",
            label="编码流",
            status="failed" if missing else "passed",
            summary=("缺少" + "、".join(missing) + "流") if missing else "编码流与导出设置一致",
            details={
                "video_codec": video.get("codec_name") if video else None,
                "audio_codec": audio.get("codec_name") if audio else None,
            },
        )

    @staticmethod
    def _duration_check(state: TimelineState, result: ExportResult) -> ExportQualityCheck:
        expected = (result.end_frame - result.start_frame) / state.sequence.profile.fps
        actual = float((result.probe.get("format") or {}).get("duration") or 0)
        tolerance = max(0.1, 1.5 / state.sequence.profile.fps)
        difference = abs(actual - expected)
        return ExportQualityCheck(
            key="duration",
            label="时长",
            status="passed" if difference <= tolerance else "failed",
            summary=(
                f"时长一致（{actual:.3f} 秒）"
                if difference <= tolerance
                else f"成片时长 {actual:.3f} 秒，预期 {expected:.3f} 秒"
            ),
            details={"expected_seconds": expected, "actual_seconds": actual, "tolerance": tolerance},
        )

    def _analyze_media(
        self,
        output: Path,
        has_video: bool,
        has_audio: bool,
        *,
        duration_seconds: float,
        progress=None,
        check_cancelled=None,
    ) -> tuple[str, bool]:
        command = ["-i", str(output)]
        if has_video:
            command.extend(["-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-50dB:d=2"])
        if has_audio:
            command.extend(
                ["-af", "silencedetect=n=-50dB:d=2,ebur128=peak=true:framelog=verbose"]
            )
        command.extend(["-f", "null", "-"])
        completed = self.ffmpeg.run_progress(
            command,
            total_seconds=duration_seconds,
            on_position=(
                lambda position: progress(
                    OperationProgress.determinate(
                        "export_quality_scanning",
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
        log = "\n".join((completed.stdout, completed.stderr))
        return log, completed.returncode == 0

    @staticmethod
    def _event_check(
        key: str,
        label: str,
        log: str,
        marker: str,
    ) -> ExportQualityCheck:
        values = [float(value) for value in re.findall(re.escape(marker) + r"\s*([0-9.]+)", log)]
        return ExportQualityCheck(
            key=key,
            label=label,
            status="warning" if values else "passed",
            summary=(f"检测到 {len(values)} 处{label}" if values else f"未检测到异常{label}"),
            details={"starts_seconds": values},
        )

    @staticmethod
    def _true_peak_check(log: str) -> ExportQualityCheck:
        matches = re.findall(r"Peak:\s*(-?inf|[-+]?[0-9.]+)\s*dBFS", log, flags=re.IGNORECASE)
        finite = [float(value) for value in matches if value.lower() != "-inf"]
        if not finite:
            return ExportQualityCheck(
                key="true_peak",
                label="音频峰值",
                status="warning",
                summary="没有读取到可用的真峰值",
            )
        peak = max(finite)
        status: Literal["passed", "warning", "failed"] = (
            "failed" if peak > 0 else "warning" if peak > -1 else "passed"
        )
        summary = f"真峰值 {peak:.1f} dBFS"
        return ExportQualityCheck(
            key="true_peak",
            label="音频峰值",
            status=status,
            summary=summary,
            details={"true_peak_dbfs": peak},
        )

    @staticmethod
    def _safe_area_check(preset: ExportPreset) -> ExportQualityCheck:
        warnings: list[str] = []
        if preset.burn_subtitle_track_id:
            style = preset.subtitle_style
            if style is None:
                raise ValueError("Burned subtitles require a subtitle style")
            if not 0.05 <= style.position_x <= 0.95 or not 0.05 <= style.position_y <= 0.95:
                warnings.append("字幕锚点超出 5%–95% 安全区")
        if (
            preset.watermark.enabled
            and preset.watermark.position_x is not None
            and preset.watermark.position_y is not None
        ):
            if (
                not 0.02 <= preset.watermark.position_x <= 0.98
                or not 0.02 <= preset.watermark.position_y <= 0.98
            ):
                warnings.append("水印锚点接近画面边界")
        return ExportQualityCheck(
            key="safe_area",
            label="安全区",
            status="warning" if warnings else "passed",
            summary="；".join(warnings) if warnings else "字幕与水印位置位于安全范围",
        )

    def _proof_frames(
        self,
        output: Path,
        output_dir: Path,
        state: TimelineState,
        result: ExportResult,
        *,
        enabled: bool,
        progress=None,
        check_cancelled=None,
    ) -> tuple[list[Path], ExportQualityCheck]:
        if not enabled:
            return [], ExportQualityCheck(
                key="proof_frames",
                label="证明帧",
                status="passed",
                summary="纯音频导出无需证明帧",
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        duration = max(0.001, (result.end_frame - result.start_frame) / state.sequence.profile.fps)
        frames: list[Path] = []
        for index, ratio in enumerate((0.1, 0.5, 0.9), start=1):
            destination = output_dir / f"proof-{index}.jpg"
            temporary = native_temporary_sibling(destination, label="proof")
            if progress:
                progress(
                    OperationProgress.determinate(
                        "export_quality_proof_frames",
                        completed=index - 1,
                        total=3,
                        unit="items",
                    )
                )
            try:
                completed = self.ffmpeg.run(
                    [
                        "-v",
                        "error",
                        "-ss",
                        f"{duration * ratio:.6f}",
                        "-i",
                        str(output),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=640:-2",
                        "-q:v",
                        "3",
                        "-y",
                        str(temporary),
                    ],
                    timeout=120,
                    check_cancelled=check_cancelled,
                )
                if (
                    completed.returncode == 0
                    and temporary.is_file()
                    and temporary.stat().st_size
                ):
                    temporary.replace(destination)
                    frames.append(destination)
            finally:
                temporary.unlink(missing_ok=True)
            if progress:
                progress(
                    OperationProgress.determinate(
                        "export_quality_proof_frames",
                        completed=index,
                        total=3,
                        unit="items",
                    )
                )
        complete = len(frames) == 3
        return frames, ExportQualityCheck(
            key="proof_frames",
            label="证明帧",
            status="passed" if complete else "warning",
            summary=("已生成开头、中段和结尾证明帧" if complete else f"只生成 {len(frames)}/3 张证明帧"),
            details={"frames": [str(path) for path in frames]},
        )
