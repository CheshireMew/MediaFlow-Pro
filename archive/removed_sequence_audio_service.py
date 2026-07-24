from __future__ import annotations

import os
from pathlib import Path

from mediaflow.domain.sequence_audio import select_audible_sequence_audio
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable

from .compiler import TimelineCompiler


class SequenceAudioRenderService:
    """Render the audible sequence mix that users hear on the timeline."""

    def __init__(self, compiler: TimelineCompiler, paths: RuntimePaths | None = None):
        self.compiler = compiler
        self.paths = paths or RuntimePaths.discover()

    def render(
        self,
        state: TimelineState,
        output_path: str | Path,
        *,
        start_frame: int,
        end_frame: int,
        check_cancelled=None,
        report_progress=None,
    ) -> Path:
        if self.paths.melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        duration = state.duration_frames
        start = max(0, min(duration, int(start_frame)))
        end = max(0, min(duration, int(end_frame)))
        if end <= start:
            raise ValueError("当前时间轴没有可转录的范围")
        assets = {asset.id: asset for asset in self.compiler.repository.list_assets()}
        selection = select_audible_sequence_audio(
            state,
            assets,
            self.compiler.repository.list_audio_buses(state.sequence.id),
            start_frame=start,
            end_frame=end,
        )
        if not selection.asset_ids:
            raise ValueError("当前时间轴范围内没有可听见的视频或音频")

        project_dir = self.compiler.repository.project_dir
        graph_path = project_dir / "cache" / "mlt" / f"{state.sequence.id}-transcription.mlt"
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if report_progress:
            report_progress(3, "transcription_compiling_timeline")
        self.compiler.write(state, graph_path, use_proxies=False)

        environment = os.environ.copy()
        environment.pop("MLT_REPOSITORY_DENY", None)
        mlt_root = self.paths.melt.parent
        environment["MLT_REPOSITORY"] = str(mlt_root / "lib" / "mlt")
        environment["MLT_DATA"] = str(mlt_root / "share" / "mlt")
        if report_progress:
            report_progress(6, "transcription_rendering_timeline")
        result = run_cancellable(
            [
                str(self.paths.melt),
                str(graph_path),
                f"in={start}",
                f"out={end - 1}",
                "-consumer",
                f"avformat:{output}",
                "f=wav",
                "vn=1",
                "acodec=pcm_s16le",
                "ar=48000",
                "ac=1",
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
        if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 44:
            raise RuntimeError(
                "时间轴音频生成失败：\n"
                + "\n".join(part for part in (result.stdout, result.stderr) if part)
            )
        if report_progress:
            report_progress(15, "transcription_timeline_audio_ready")
        return output
