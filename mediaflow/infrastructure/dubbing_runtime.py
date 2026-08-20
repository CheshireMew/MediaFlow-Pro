from __future__ import annotations

import wave
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from mediaflow.application.export_catalog import EXPORT_VARIANTS
from mediaflow.application.ports import ProjectTaskDocuments
from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.dubbing import DiarizationSpeechInterval
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import (
    ServiceSettings,
    SpeakerDiarizationSettings,
)
from mediaflow.domain.timeline import TimelineState
from mediaflow.file_digest import sha256_file

from .ffmpeg_runner import FfmpegRunner
from .gpt_sovits_engine import GptSoVitsEngine, GptSoVitsSession
from .mlt import MltExportService, TimelineCompiler
from .output_reservation import archive_published_outputs
from .runtime_components import RuntimeComponentService
from .runtime_paths import RuntimePaths
from .speaker_diarization import (
    DiarizationResult,
    PyannoteDiarizationEngine,
    TranscriptSpeakerClusteringEngine,
)

CancellationCheck = Callable[[], None]
ProgressCallback = Callable[[OperationProgress], None]


@dataclass(frozen=True, slots=True)
class PreparedDubbingAudio:
    path: Path
    sha256: str
    duration_seconds: float
    sample_rate: int
    channels: int


class InfrastructureDubbingRuntime:
    def __init__(
        self,
        documents: ProjectTaskDocuments,
        paths: RuntimePaths,
    ) -> None:
        self.documents = documents
        self.paths = paths
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)

    @staticmethod
    def file_sha256(path: Path) -> str:
        return sha256_file(path.resolve(strict=True))

    def archive_unrecorded_outputs(
        self,
        paths: list[Path],
    ) -> tuple[Path, ...]:
        return archive_published_outputs(
            paths,
            runtime_dir=self.paths.runtime_dir,
            archive_directory_name="failed-dubbing",
        )

    def render_dialogue_audio(
        self,
        state: TimelineState,
        dialogue_track_id: str,
        output_path: str | Path,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio:
        selected = next(
            (track for track in state.tracks if track.id == dialogue_track_id),
            None,
        )
        if selected is None or selected.kind != TrackKind.AUDIO:
            raise ValueError("主要对白轨不存在或不是音频轨")
        isolated = state.model_copy(
            update={
                "tracks": [
                    track.model_copy(
                        update={
                            "enabled": (
                                track.id == dialogue_track_id
                                if track.kind == TrackKind.AUDIO
                                else track.enabled
                            ),
                            "muted": (
                                track.id != dialogue_track_id
                                if track.kind == TrackKind.AUDIO
                                else track.muted
                            ),
                            "solo": False,
                        }
                    )
                    for track in state.tracks
                ]
            },
            deep=True,
        )
        variant = next(item for item in EXPORT_VARIANTS if item.id == "audio_pcm")
        preset = variant.to_preset(
            state.sequence.profile.color_mode,
            state.sequence.profile.fps,
        ).model_copy(
            update={
                "name": "Dubbing dialogue source",
                "advanced": {
                    "audio_sample_rate": state.sequence.profile.audio_sample_rate,
                    "audio_channels": 1,
                },
            }
        )
        output = Path(output_path).resolve()
        MltExportService(
            TimelineCompiler(self.documents, self.paths),
            self.paths,
        ).export(
            isolated,
            preset,
            output,
            overwrite=True,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        return self.inspect_wave(output)

    def diarize(
        self,
        source: str | Path,
        settings: SpeakerDiarizationSettings,
        *,
        minimum_speakers: int | None,
        maximum_speakers: int | None,
        speech_intervals: tuple[DiarizationSpeechInterval, ...],
        check_cancelled: CancellationCheck,
    ) -> DiarizationResult:
        if settings.backend == "transcript_clustering":
            return TranscriptSpeakerClusteringEngine(
                settings,
                check_cancelled=check_cancelled,
            ).diarize(
                source,
                speech_intervals=speech_intervals,
                minimum_speakers=minimum_speakers,
                maximum_speakers=maximum_speakers,
            )
        return PyannoteDiarizationEngine(
            settings,
            check_cancelled=check_cancelled,
        ).diarize(
            source,
            minimum_speakers=minimum_speakers,
            maximum_speakers=maximum_speakers,
        )

    def extract_reference(
        self,
        source: str | Path,
        output_path: str | Path,
        *,
        start_seconds: float,
        end_seconds: float,
        sample_rate: int,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio:
        if start_seconds < 0 or end_seconds <= start_seconds:
            raise ValueError("参考音频范围无效")
        return self._run_ffmpeg_audio(
            source,
            output_path,
            filters=[],
            input_arguments=[
                "-ss",
                f"{start_seconds:.9f}",
                "-t",
                f"{end_seconds - start_seconds:.9f}",
            ],
            sample_rate=sample_rate,
            check_cancelled=check_cancelled,
        )

    def synthesis_session(
        self,
        settings: ServiceSettings,
        *,
        check_cancelled: CancellationCheck,
    ) -> tuple[str, AbstractContextManager[GptSoVitsSession]]:
        components = RuntimeComponentService(settings, self.paths)
        installation = components.resolve("gpt-sovits-v2pro")
        if installation is None:
            raise FileNotFoundError("请先安装或选择 GPT-SoVITS v2Pro")
        engine = GptSoVitsEngine(
            installation.root,
            self.paths.runtime_dir,
            device=settings.speech_synthesis.device,
            startup_timeout_seconds=settings.speech_synthesis.startup_timeout_seconds,
            check_cancelled=check_cancelled,
        )
        return installation.definition.version, engine.session()

    def normalize_utterance(
        self,
        source: str | Path,
        output_path: str | Path,
        *,
        target_seconds: float | None,
        sample_rate: int,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio:
        filters: list[str] = []
        if target_seconds is not None:
            filters.extend(
                [
                    f"apad=whole_dur={target_seconds:.9f}",
                    f"atrim=duration={target_seconds:.9f}",
                ]
            )
        return self._run_ffmpeg_audio(
            source,
            output_path,
            filters=filters,
            input_arguments=[],
            sample_rate=sample_rate,
            check_cancelled=check_cancelled,
        )

    def assemble_master(
        self,
        inputs: list[tuple[str | Path, float]],
        output_path: str | Path,
        *,
        minimum_duration_seconds: float,
        sample_rate: int,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio:
        if not inputs:
            raise ValueError("没有可合成的配音片段")
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = unique_temporary_sibling(output, label="dubbing-master")
        filter_script = unique_temporary_sibling(
            output.with_suffix(".filter.txt"),
            label="dubbing-master",
        )
        command = ["-loglevel", "error", "-y"]
        filter_lines: list[str] = []
        delayed_labels: list[str] = []
        for index, (path, start_seconds) in enumerate(inputs):
            command.extend(["-i", str(Path(path).resolve(strict=True))])
            delay_ms = max(0, round(start_seconds * 1000))
            label = f"d{index}"
            filter_lines.append(
                f"[{index}:a]aresample={sample_rate},aformat=sample_fmts=s16:"
                f"channel_layouts=mono,adelay={delay_ms}:all=1[{label}]"
            )
            delayed_labels.append(f"[{label}]")
        filter_lines.append(
            "".join(delayed_labels)
            + f"amix=inputs={len(delayed_labels)}:duration=longest:normalize=0,"
            + f"apad=whole_dur={minimum_duration_seconds:.9f}[master]"
        )
        filter_script.write_text(";\n".join(filter_lines), encoding="utf-8")
        command.extend(
            [
                "-filter_complex_script",
                str(filter_script),
                "-map",
                "[master]",
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ]
        )
        try:
            self._run_ffmpeg(command, check_cancelled=check_cancelled)
            temporary.replace(output)
            return self.inspect_wave(output)
        finally:
            if temporary.exists():
                temporary.unlink()
            if filter_script.exists():
                filter_script.unlink()

    def _run_ffmpeg_audio(
        self,
        source: str | Path,
        output_path: str | Path,
        *,
        filters: list[str],
        input_arguments: list[str],
        sample_rate: int,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio:
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = unique_temporary_sibling(output, label="dubbing-audio")
        command = [
            "-loglevel",
            "error",
            "-y",
            *input_arguments,
            "-i",
            str(Path(source).resolve(strict=True)),
            "-vn",
        ]
        if filters:
            command.extend(["-af", ",".join(filters)])
        command.extend(
            [
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ]
        )
        try:
            self._run_ffmpeg(command, check_cancelled=check_cancelled)
            temporary.replace(output)
            return self.inspect_wave(output)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def inspect_wave(path: str | Path) -> PreparedDubbingAudio:
        source = Path(path).resolve(strict=True)
        try:
            with wave.open(str(source), "rb") as audio:
                sample_rate = audio.getframerate()
                channels = audio.getnchannels()
                frames = audio.getnframes()
        except (OSError, wave.Error) as error:
            raise RuntimeError(f"配音音频不是可用的 WAV：{error}") from error
        if sample_rate <= 0 or channels <= 0 or frames <= 0:
            raise RuntimeError("配音 WAV 没有有效音频帧")
        return PreparedDubbingAudio(
            path=source,
            sha256=sha256_file(source),
            duration_seconds=frames / sample_rate,
            sample_rate=sample_rate,
            channels=channels,
        )

    def _run_ffmpeg(
        self,
        command: list[str],
        *,
        check_cancelled: CancellationCheck,
    ) -> None:
        completed = self.ffmpeg.run(
            command,
            check_cancelled=check_cancelled,
            timeout=7200,
        )
        if completed.returncode != 0:
            detail = "\n".join(
                part for part in (completed.stderr, completed.stdout) if part
            ).strip()
            raise RuntimeError("FFmpeg 处理配音音频失败：" + detail[-4000:])
