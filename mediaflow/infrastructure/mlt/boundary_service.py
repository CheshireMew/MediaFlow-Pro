from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from mediaflow.domain.enums import TrackKind
from mediaflow.domain.project import SequenceInOut
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable

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

    def snapshot_hash(self, state: TimelineState) -> str:
        enabled_subtitle_tracks = {
            track.id for track in state.tracks if track.kind == TrackKind.SUBTITLE and track.enabled
        }
        placements = [
            placement.model_dump(mode="json")
            for track_id in sorted(enabled_subtitle_tracks)
            for placement in self.compiler.repository.list_subtitle_placements(track_id)
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
        report_progress=None,
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
        graph_path = project_dir / "cache" / "mlt" / f"{state.sequence.id}-boundary-analysis.mlt"
        cache_dir = project_dir / "cache" / "boundary-analysis" / snapshot_hash[:16]
        result_path = (
            project_dir / "generated" / "analysis" / f"{state.sequence.id}-boundary-{snapshot_hash[:16]}.json"
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if report_progress:
            report_progress(5, "sequence_boundary_compiling")
        self.compiler.write(state, graph_path, use_proxies=False)

        if report_progress:
            report_progress(15, "sequence_boundary_leading_black")
        black_in = self._detect_edge_black(
            state,
            graph_path,
            cache_dir,
            edge="leading",
            check_cancelled=check_cancelled,
        )
        if report_progress:
            report_progress(50, "sequence_boundary_trailing_black")
        black_out = self._detect_edge_black(
            state,
            graph_path,
            cache_dir,
            edge="trailing",
            check_cancelled=check_cancelled,
        )

        if report_progress:
            report_progress(80, "sequence_boundary_speech")
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
        temporary = result_path.with_suffix(result_path.suffix + ".tmp")
        temporary.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(result_path)
        if report_progress:
            report_progress(100, "sequence_boundary_complete")
        return analysis, result_path

    def _speech_bounds(self, state: TimelineState, duration: int) -> tuple[int, int] | None:
        intervals: list[tuple[int, int]] = []
        for track in state.tracks:
            if track.kind != TrackKind.SUBTITLE or not track.enabled:
                continue
            for placement in self.compiler.repository.list_subtitle_placements(track.id):
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
            rendered = cache_dir / f"{edge}-{start_frame}-{end_frame}.mkv"
            self._render_window(
                state,
                graph_path,
                rendered,
                start_frame=start_frame,
                end_frame=end_frame,
                check_cancelled=check_cancelled,
            )
            intervals = self._black_intervals(rendered, check_cancelled)
            window_seconds = float(
                frames_to_seconds(
                    end_frame - start_frame,
                    profile.fps_numerator,
                    profile.fps_denominator,
                )
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
        result = run_cancellable(
            [
                str(melt),
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
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check_cancelled=check_cancelled,
        )
        if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(
                "Sequence edge render failed:\n"
                + "\n".join(part for part in (result.stdout, result.stderr) if part)
            )

    def _black_intervals(self, source: Path, check_cancelled) -> list[tuple[float, float]]:
        result = run_cancellable(
            [
                str(self.paths.ffmpeg),
                "-hide_banner",
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
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
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
