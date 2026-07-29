from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import SequenceInOut
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.process_observers import MeltProgressObserver
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable_streaming

from .compiler import TimelineCompiler

_BLACK_INTERVAL = re.compile(
    r"black_start:(?P<start>\d+(?:\.\d+)?)\s+"
    r"black_end:(?P<end>\d+(?:\.\d+)?)\s+"
    r"black_duration:(?P<duration>\d+(?:\.\d+)?)"
)


class SequenceBoundaryAnalysisService:
    """Resolve editable sequence in/out points from the compiled picture and speech timeline."""

    def __init__(self, compiler: TimelineCompiler, paths: RuntimePaths | None = None):
        self.compiler = compiler
        self.paths = paths or RuntimePaths.discover()
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)

    def snapshot_hash(self, state: TimelineState) -> str:
        enabled_subtitle_tracks = {
            track.id for track in state.tracks if track.kind == TrackKind.SUBTITLE and track.enabled
        }
        placements = [
            placement.model_dump(mode="json")
            for track_id in sorted(enabled_subtitle_tracks)
            for placement in self.compiler.repository.subtitles.list_subtitle_placements(track_id)
        ]
        payload = {
            "timeline": state.model_dump(mode="json"),
            "enabled_subtitle_placements": placements,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def analyze(
        self,
        state: TimelineState,
        *,
        expected_snapshot_hash: str,
        check_cancelled=None,
        progress=None,
    ) -> tuple[SequenceBoundaryAnalysis, Path]:
        if self.paths.melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        duration = state.duration_frames
        if duration <= 0:
            raise ValueError("Sequence has no media to analyze")
        snapshot_hash = self.snapshot_hash(state)
        if snapshot_hash != expected_snapshot_hash:
            raise RuntimeError("Sequence changed before boundary analysis started")

        project_dir = self.compiler.repository.project_dir
        graph_path = content_addressed_child_path(
            project_dir / "cache" / "b",
            f"boundary-graph:{state.sequence.id}:{snapshot_hash}",
            namespace="bg",
            suffix=".mlt",
        )
        cache_dir = content_addressed_child_path(
            project_dir / "cache" / "b",
            f"boundary-windows:{state.sequence.id}:{snapshot_hash}",
            namespace="bw",
            suffix="",
            required_descendant_component_utf16_units=32,
        )
        result_path = content_addressed_child_path(
            project_dir / "generated" / "a",
            f"boundary-result:{state.sequence.id}:{snapshot_hash}",
            namespace="ba",
            suffix=".json",
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(OperationProgress.indeterminate("sequence_boundary_compiling"))
        self.compiler.write(state, graph_path, use_proxies=False)

        black_in = self._detect_edge_black(
            state,
            graph_path,
            cache_dir,
            edge="leading",
            check_cancelled=check_cancelled,
            progress=progress,
        )
        black_out = self._detect_edge_black(
            state,
            graph_path,
            cache_dir,
            edge="trailing",
            check_cancelled=check_cancelled,
            progress=progress,
        )

        if progress:
            progress(OperationProgress.indeterminate("sequence_boundary_speech"))
        speech = self._speech_bounds(state, duration)
        speech_in, speech_out = speech if speech is not None else (None, None)
        suggested_in = max(value for value in (0, black_in, speech_in) if value is not None)
        suggested_out = min(value for value in (duration, black_out, speech_out) if value is not None)
        if suggested_out <= suggested_in:
            raise ValueError("Detected boundaries would remove the entire sequence")
        analysis = SequenceBoundaryAnalysis(
            sequence_id=state.sequence.id,
            snapshot_hash=snapshot_hash,
            duration_frames=duration,
            suggested=SequenceInOut(in_frame=suggested_in, out_frame=suggested_out),
            speech_in_frame=speech_in,
            speech_out_frame=speech_out,
            black_in_frame=black_in,
            black_out_frame=black_out,
        )
        if progress:
            progress(OperationProgress.indeterminate("sequence_boundary_saving"))
        if check_cancelled:
            check_cancelled()
        atomic_write_text(result_path, analysis.model_dump_json(indent=2))
        return analysis, result_path

    def _speech_bounds(self, state: TimelineState, duration: int) -> tuple[int, int] | None:
        intervals: list[tuple[int, int]] = []
        for track in state.tracks:
            if track.kind != TrackKind.SUBTITLE or not track.enabled:
                continue
            for placement in self.compiler.repository.subtitles.list_subtitle_placements(track.id):
                start = max(0, min(duration, placement.start_frame))
                end = max(0, min(duration, placement.end_frame))
                if end > start:
                    intervals.append((start, end))
        if not intervals:
            return None
        profile = state.sequence.profile
        guard_frames = max(
            1,
            seconds_to_frames(
                0.1,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
        )
        return (
            max(0, min(start for start, _ in intervals) - guard_frames),
            min(duration, max(end for _, end in intervals) + guard_frames),
        )

    def _detect_edge_black(
        self,
        state: TimelineState,
        graph_path: Path,
        cache_dir: Path,
        *,
        edge: str,
        check_cancelled,
        progress,
    ) -> int | None:
        duration = state.duration_frames
        profile = state.sequence.profile
        initial = max(
            1,
            seconds_to_frames(30.0, profile.fps_numerator, profile.fps_denominator),
        )
        window_frames = min(duration, initial)
        while True:
            if check_cancelled:
                check_cancelled()
            start_frame = 0 if edge == "leading" else duration - window_frames
            end_frame = window_frames if edge == "leading" else duration
            rendered = content_addressed_child_path(
                cache_dir,
                (f"boundary-window:{state.sequence.id}:{edge}:{start_frame}:{end_frame}"),
                namespace="w",
                suffix=".mkv",
            )
            self._render_window(
                state,
                graph_path,
                rendered,
                start_frame=start_frame,
                end_frame=end_frame,
                check_cancelled=check_cancelled,
                progress=progress,
                message_code=f"sequence_boundary_{edge}_rendering",
            )
            window_seconds = float(
                frames_to_seconds(
                    end_frame - start_frame,
                    profile.fps_numerator,
                    profile.fps_denominator,
                )
            )
            intervals = self._black_intervals(
                rendered,
                check_cancelled,
                duration_seconds=window_seconds,
                progress=progress,
                message_code=f"sequence_boundary_{edge}_scanning",
            )
            tolerance = max(
                0.05,
                float(
                    frames_to_seconds(
                        2,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    )
                ),
            )
            if edge == "leading":
                interval = next((item for item in intervals if item[0] <= tolerance), None)
                if interval is None:
                    return None
                boundary = min(
                    duration,
                    start_frame
                    + seconds_to_frames(interval[1], profile.fps_numerator, profile.fps_denominator),
                )
                fills_window = interval[1] >= window_seconds - tolerance
            else:
                interval = next(
                    (item for item in reversed(intervals) if item[1] >= window_seconds - tolerance),
                    None,
                )
                if interval is None:
                    return None
                boundary = max(
                    0,
                    start_frame
                    + seconds_to_frames(interval[0], profile.fps_numerator, profile.fps_denominator),
                )
                fills_window = interval[0] <= tolerance
            if not fills_window:
                return boundary
            if window_frames >= duration:
                return None
            window_frames = min(duration, window_frames * 2)

    def _render_window(
        self,
        state: TimelineState,
        graph_path: Path,
        destination: Path,
        *,
        start_frame: int,
        end_frame: int,
        check_cancelled,
        progress,
        message_code: str,
    ) -> None:
        profile = state.sequence.profile
        width = min(320, profile.width)
        width += (-width) % 4
        height = max(2, round(width * profile.height / profile.width))
        height += (-height) % 4
        environment = os.environ.copy()
        environment.pop("MLT_REPOSITORY_DENY", None)
        melt = self.paths.melt
        if melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        mlt_root = melt.parent
        environment["MLT_REPOSITORY"] = str(mlt_root / "lib" / "mlt")
        environment["MLT_DATA"] = str(mlt_root / "share" / "mlt")
        total_frames = end_frame - start_frame
        observer = MeltProgressObserver(
            total_frames,
            lambda frame: progress(
                OperationProgress.determinate(
                    message_code,
                    completed=frame,
                    total=total_frames,
                    unit="frames",
                )
            )
            if progress
            else None,
        )
        result = run_cancellable_streaming(
            [
                str(melt),
                "-progress2",
                str(graph_path),
                f"in={start_frame}",
                f"out={end_frame - 1}",
                "-consumer",
                f"avformat:{destination}",
                "f=matroska",
                "vcodec=libx264",
                "preset=ultrafast",
                "crf=30",
                "pix_fmt=yuv420p",
                "an=1",
                f"width={width}",
                f"height={height}",
                "terminate_on_pause=1",
                "real_time=-1",
            ],
            cwd=mlt_root,
            env=environment,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check_cancelled=check_cancelled,
            on_stdout_line=observer,
            on_stderr_line=observer,
            split_carriage_returns=True,
        )
        if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(
                "Sequence edge render failed:\n"
                + "\n".join(part for part in (result.stdout, result.stderr) if part)
            )
        if progress:
            progress(
                OperationProgress.determinate(
                    message_code,
                    completed=total_frames,
                    total=total_frames,
                    unit="frames",
                )
            )

    def _black_intervals(
        self,
        source: Path,
        check_cancelled,
        *,
        duration_seconds: float,
        progress,
        message_code: str,
    ) -> list[tuple[float, float]]:
        command = [
            "-v",
            "info",
            "-i",
            str(source),
            "-vf",
            "blackdetect=d=0.04:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ]
        result = self.ffmpeg.run_progress(
            command,
            total_seconds=duration_seconds,
            on_position=(
                lambda position: progress(
                    OperationProgress.determinate(
                        message_code,
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )
                if progress
                else None
            ),
            timeout=3600,
            check_cancelled=check_cancelled,
        )
        if result.returncode != 0:
            raise RuntimeError("FFmpeg black-frame analysis failed:\n" + (result.stderr or ""))
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        return [
            (float(match.group("start")), float(match.group("end")))
            for match in _BLACK_INTERVAL.finditer(output)
        ]
