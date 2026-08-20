from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from mediaflow.atomic_file import atomic_write_text, native_temporary_sibling
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.file_fingerprint import fingerprint_file, fingerprint_matches
from mediaflow.infrastructure.process_observers import MeltProgressObserver
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable_streaming

from .compiler import MltDocument, TimelineCompiler


@dataclass(frozen=True, slots=True)
class LoudnessMetrics:
    sample_peak_dbfs: float
    true_peak_dbtp: float
    short_term_lufs: float
    integrated_lufs: float

    def desktop_payload(self) -> dict[str, float]:
        return {
            "samplePeakDbfs": self.sample_peak_dbfs,
            "truePeakDbtp": self.true_peak_dbtp,
            "shortTermLufs": self.short_term_lufs,
            "integratedLufs": self.integrated_lufs,
        }


class LoudnessAnalysisService:
    """Measure the compiled sequence audio graph with FFmpeg EBU R128 filters."""

    RESULT_SCHEMA_VERSION = 1
    CURRENT_RESULT_SCHEMA_VERSION = 1
    LOCK_WAIT_TIMEOUT_SECONDS = 10_800

    def __init__(self, compiler: TimelineCompiler, paths: RuntimePaths):
        self.compiler = compiler
        self.paths = paths
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)

    def analyze(
        self,
        state: TimelineState,
        *,
        check_cancelled=None,
        progress=None,
    ) -> tuple[LoudnessMetrics, Path]:
        if self.paths.melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        repository = cast(ProjectRepository, self.compiler.repository)
        project_dir = repository.project_dir
        analysis_revision = repository.content_revision()
        cache_dir = project_dir / "cache" / "l"
        cache_dir.mkdir(parents=True, exist_ok=True)

        if progress:
            progress(OperationProgress.indeterminate("audio_analysis_compiling"))
        document = self.compiler.compile(state, use_proxies=False)
        snapshot_hash = self._snapshot_hash(document.xml, document.source_paths)
        graph_path = self.graph_path(project_dir, state.sequence.id, snapshot_hash)
        result_path = self.result_path(project_dir, state.sequence.id, snapshot_hash)
        cached = self.read_metrics(
            result_path,
            expected_sequence_id=state.sequence.id,
            expected_snapshot_hash=snapshot_hash,
        )
        if cached is not None:
            self._publish_current_result(
                state,
                snapshot_hash=snapshot_hash,
                result_path=result_path,
                analysis_revision=analysis_revision,
            )
            if progress:
                progress(OperationProgress.indeterminate("audio_analysis_cache_ready"))
            return cached, result_path
        lock = self._acquire_producer_lock(
            self.lock_path(project_dir, state.sequence.id, snapshot_hash),
            result_path,
            sequence_id=state.sequence.id,
            snapshot_hash=snapshot_hash,
            check_cancelled=check_cancelled,
            progress=progress,
        )
        try:
            cached = self.read_metrics(
                result_path,
                expected_sequence_id=state.sequence.id,
                expected_snapshot_hash=snapshot_hash,
            )
            if cached is not None:
                self._publish_current_result(
                    state,
                    snapshot_hash=snapshot_hash,
                    result_path=result_path,
                    analysis_revision=analysis_revision,
                )
                if progress:
                    progress(
                        OperationProgress.indeterminate(
                            "audio_analysis_cache_ready"
                        )
                    )
                return cached, result_path
            produced = self._produce(
                state,
                document,
                snapshot_hash=snapshot_hash,
                graph_path=graph_path,
                result_path=result_path,
                check_cancelled=check_cancelled,
                progress=progress,
            )
            self._publish_current_result(
                state,
                snapshot_hash=snapshot_hash,
                result_path=produced[1],
                analysis_revision=analysis_revision,
            )
            return produced
        finally:
            lock.release()

    def _produce(
        self,
        state: TimelineState,
        document: MltDocument,
        *,
        snapshot_hash: str,
        graph_path: Path,
        result_path: Path,
        check_cancelled=None,
        progress=None,
    ) -> tuple[LoudnessMetrics, Path]:
        melt = self.paths.melt
        if melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        project_dir = self.compiler.repository.project_dir
        cache_dir = project_dir / "cache" / "l"
        rendered_audio = native_temporary_sibling(
            content_addressed_child_path(
                cache_dir,
                f"loudness-render:{state.sequence.id}:{snapshot_hash}",
                namespace="lw",
                suffix=".wav",
            ),
            label="loudness",
        )
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(graph_path, document.xml)
        mlt_root = self.paths.require_mlt_root()
        environment = self.paths.mlt_environment()
        total_frames = max(1, document.duration_frames)
        melt_observer = MeltProgressObserver(
            total_frames,
            lambda frame: progress(
                OperationProgress.determinate(
                    "audio_analysis_rendering",
                    completed=frame,
                    total=total_frames,
                    unit="frames",
                )
            )
            if progress
            else None,
        )
        try:
            render = run_cancellable_streaming(
                [
                    str(melt),
                    "-progress2",
                    str(graph_path),
                    "-consumer",
                    f"avformat:{rendered_audio}",
                    "f=wav",
                    "vn=1",
                    "acodec=pcm_f32le",
                    "ar=48000",
                    f"ac={state.sequence.profile.audio_channels}",
                    "terminate_on_pause=1",
                    "real_time=-1",
                ],
                cwd=mlt_root,
                env=environment,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                check_cancelled=check_cancelled,
                on_stdout_line=melt_observer,
                on_stderr_line=melt_observer,
                split_carriage_returns=True,
            )
            if (
                render.returncode != 0
                or not rendered_audio.is_file()
                or rendered_audio.stat().st_size == 0
            ):
                raise RuntimeError(
                    "Sequence audio render failed:\n"
                    + (render.stderr or render.stdout or "")
                )
            if progress:
                progress(
                    OperationProgress.determinate(
                        "audio_analysis_rendering",
                        completed=total_frames,
                        total=total_frames,
                        unit="frames",
                    )
                )

            loudness_log = self._ffmpeg_measure(
                rendered_audio,
                "ebur128=peak=true:framelog=verbose",
                check_cancelled,
                loglevel="verbose",
                message_code="audio_analysis_measuring_loudness",
                duration_seconds=total_frames / state.sequence.profile.fps,
                progress=progress,
            )
            peak_log = self._ffmpeg_measure(
                rendered_audio,
                "astats=metadata=0:reset=0",
                check_cancelled,
                loglevel="info",
                message_code="audio_analysis_measuring_peak",
                duration_seconds=total_frames / state.sequence.profile.fps,
                progress=progress,
            )
            metrics = LoudnessMetrics(
                sample_peak_dbfs=self._sample_peak(peak_log),
                true_peak_dbtp=self._last_metric(
                    loudness_log,
                    r"Peak:\s*([^\s]+)\s+dBFS",
                ),
                short_term_lufs=self._short_term(loudness_log),
                integrated_lufs=self._last_metric(
                    loudness_log,
                    r"I:\s*([^\s]+)\s+LUFS",
                ),
            )
            if progress:
                progress(OperationProgress.indeterminate("audio_analysis_saving"))
            atomic_write_text(
                result_path,
                json.dumps(
                    {
                        "schema_version": self.RESULT_SCHEMA_VERSION,
                        **asdict(metrics),
                        "sequence_id": state.sequence.id,
                        "snapshot_hash": snapshot_hash,
                        "producer_pid": os.getpid(),
                        "source_graph": str(
                            graph_path.relative_to(project_dir).as_posix()
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            return metrics, result_path
        finally:
            rendered_audio.unlink(missing_ok=True)

    def snapshot_hash(self, state: TimelineState) -> str:
        document = self.compiler.compile(state, use_proxies=False)
        return self._snapshot_hash(document.xml, document.source_paths)

    def _publish_current_result(
        self,
        state: TimelineState,
        *,
        snapshot_hash: str,
        result_path: Path,
        analysis_revision: int,
    ) -> None:
        repository = cast(ProjectRepository, self.compiler.repository)
        if repository.content_revision() != analysis_revision:
            return
        persisted = repository.timeline.load_timeline(state.sequence.id)
        if persisted.model_dump(mode="json") != state.model_dump(mode="json"):
            return
        receipt_path = self.current_result_path(repository.project_dir, state.sequence.id)
        atomic_write_text(
            receipt_path,
            json.dumps(
                {
                    "schema_version": self.CURRENT_RESULT_SCHEMA_VERSION,
                    "sequence_id": state.sequence.id,
                    "project_revision": analysis_revision,
                    "snapshot_hash": snapshot_hash,
                    "result_path": result_path.relative_to(repository.project_dir).as_posix(),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    @classmethod
    def read_current_metrics(
        cls,
        project_dir: Path,
        sequence_id: str,
        *,
        current_project_revision: int,
    ) -> LoudnessMetrics | None:
        project_root = project_dir.resolve()
        receipt_path = cls.current_result_path(project_root, sequence_id)
        if not receipt_path.is_file():
            return None
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Loudness result receipt is unreadable: {receipt_path}") from error
        if not isinstance(receipt, dict):
            raise RuntimeError(f"Loudness result receipt is invalid: {receipt_path}")
        if (
            receipt.get("schema_version") != cls.CURRENT_RESULT_SCHEMA_VERSION
            or receipt.get("sequence_id") != sequence_id
        ):
            raise RuntimeError(f"Loudness result receipt has an invalid contract: {receipt_path}")
        revision = receipt.get("project_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise RuntimeError(f"Loudness result receipt has an invalid revision: {receipt_path}")
        if revision != current_project_revision:
            return None
        snapshot_hash = receipt.get("snapshot_hash")
        relative_result = receipt.get("result_path")
        if (
            not isinstance(snapshot_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash)
            or not isinstance(relative_result, str)
            or not relative_result
        ):
            raise RuntimeError(f"Loudness result receipt has an invalid identity: {receipt_path}")
        result_path = (project_root / relative_result).resolve()
        try:
            result_path.relative_to(project_root)
        except ValueError as error:
            raise RuntimeError(f"Loudness result receipt escapes the project: {receipt_path}") from error
        metrics = cls.read_metrics(
            result_path,
            expected_sequence_id=sequence_id,
            expected_snapshot_hash=snapshot_hash,
        )
        if metrics is None:
            raise RuntimeError(f"Loudness result referenced by the receipt is unavailable: {result_path}")
        return metrics

    def _snapshot_hash(
        self,
        xml: str,
        source_paths: tuple[Path, ...],
    ) -> str:
        assets_by_path: dict[str, list[Asset]] = {}
        for asset in self.compiler.repository.assets.list_assets():
            source = self.compiler.repository.assets.resolve_asset_path(asset)
            assets_by_path.setdefault(
                os.path.normcase(str(source.resolve())),
                [],
            ).append(asset)

        source_identities: list[dict[str, object]] = []
        for source in source_paths:
            resolved = source.resolve(strict=True)
            live_fingerprint = fingerprint_file(resolved)
            stored_assets = sorted(
                assets_by_path.get(os.path.normcase(str(resolved)), []),
                key=lambda item: item.id,
            )
            for asset in stored_assets:
                if asset.fingerprint is not None and not fingerprint_matches(
                    resolved,
                    asset.fingerprint,
                ):
                    raise RuntimeError(
                        f"素材文件内容已变化，请先刷新素材后重新分析响度：{asset.name}"
                    )
            source_identities.append(
                {
                    "path": str(resolved),
                    "live_fingerprint": live_fingerprint.model_dump(mode="json"),
                    "assets": [
                        {
                            "id": asset.id,
                            "fingerprint": (
                                asset.fingerprint.model_dump(mode="json")
                                if asset.fingerprint is not None
                                else None
                            ),
                        }
                        for asset in stored_assets
                    ],
                }
            )
        payload = json.dumps(
            {
                "xml": xml,
                "sources": source_identities,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def graph_path(
        project_dir: Path,
        sequence_id: str,
        snapshot_hash: str,
    ) -> Path:
        return content_addressed_child_path(
            project_dir / "cache" / "l",
            f"loudness-graph:{sequence_id}:{snapshot_hash}",
            namespace="lg",
            suffix=".mlt",
        )

    @staticmethod
    def result_path(
        project_dir: Path,
        sequence_id: str,
        snapshot_hash: str,
    ) -> Path:
        return content_addressed_child_path(
            project_dir / "generated" / "a",
            f"loudness-result:{sequence_id}:{snapshot_hash}",
            namespace="la",
            suffix=".json",
        )

    @staticmethod
    def current_result_path(project_dir: Path, sequence_id: str) -> Path:
        return content_addressed_child_path(
            project_dir / "generated" / "a",
            f"loudness-current:{sequence_id}",
            namespace="lc",
            suffix=".json",
        )

    @staticmethod
    def lock_path(
        project_dir: Path,
        sequence_id: str,
        snapshot_hash: str,
    ) -> Path:
        return content_addressed_child_path(
            project_dir / "cache" / "l",
            f"loudness-lock:{sequence_id}:{snapshot_hash}",
            namespace="ll",
            suffix=".lock",
        )

    @classmethod
    def _acquire_producer_lock(
        cls,
        lock_path: Path,
        result_path: Path,
        *,
        sequence_id: str,
        snapshot_hash: str,
        check_cancelled=None,
        progress=None,
    ) -> ProcessFileLock:
        deadline = time.monotonic() + cls.LOCK_WAIT_TIMEOUT_SECONDS
        lock = ProcessFileLock(lock_path)
        waiting_reported = False
        while True:
            if cls.read_metrics(
                result_path,
                expected_sequence_id=sequence_id,
                expected_snapshot_hash=snapshot_hash,
            ) is not None:
                if lock.acquire():
                    return lock
            elif lock.acquire():
                return lock
            if not waiting_reported and progress:
                progress(OperationProgress.indeterminate("audio_analysis_waiting"))
                waiting_reported = True
            if check_cancelled is not None:
                check_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for loudness analysis result: "
                    f"{sequence_id}:{snapshot_hash}"
                )
            time.sleep(0.1)

    @classmethod
    def read_metrics(
        cls,
        path: Path,
        *,
        expected_sequence_id: str,
        expected_snapshot_hash: str,
    ) -> LoudnessMetrics | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if (
            payload.get("schema_version") != cls.RESULT_SCHEMA_VERSION
            or payload.get("sequence_id") != expected_sequence_id
            or payload.get("snapshot_hash") != expected_snapshot_hash
        ):
            return None
        metric_names = (
            "sample_peak_dbfs",
            "true_peak_dbtp",
            "short_term_lufs",
            "integrated_lufs",
        )
        values: dict[str, float] = {}
        for name in metric_names:
            raw = payload.get(name)
            if (
                not isinstance(raw, (int, float))
                or isinstance(raw, bool)
                or not math.isfinite(float(raw))
            ):
                return None
            values[name] = float(raw)
        return LoudnessMetrics(**values)

    def _ffmpeg_measure(
        self,
        source: Path,
        audio_filter: str,
        check_cancelled,
        *,
        loglevel: str,
        message_code: str,
        duration_seconds: float,
        progress=None,
    ) -> str:
        command = [
            "-loglevel",
            loglevel,
            "-i",
            str(source),
            "-af",
            audio_filter,
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
            raise RuntimeError("Audio measurement failed:\n" + (result.stderr or result.stdout or ""))
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    @classmethod
    def _sample_peak(cls, output: str) -> float:
        values = cls._metrics(output, r"Peak level dB:\s*([^\s]+)")
        if not values:
            raise RuntimeError("FFmpeg astats did not report a sample peak")
        return max(values)

    @classmethod
    def _short_term(cls, output: str) -> float:
        values = cls._metrics(output, r"\bS:\s*([^\s]+)")
        if not values:
            raise RuntimeError("FFmpeg ebur128 did not report short-term loudness")
        return max(values)

    @classmethod
    def _last_metric(cls, output: str, pattern: str) -> float:
        values = cls._metrics(output, pattern)
        if not values:
            raise RuntimeError(f"FFmpeg did not report metric: {pattern}")
        return values[-1]

    @staticmethod
    def _metrics(output: str, pattern: str) -> list[float]:
        values: list[float] = []
        for token in re.findall(pattern, output, flags=re.IGNORECASE):
            try:
                value = float(token)
            except ValueError:
                continue
            if math.isfinite(value):
                values.append(value)
        return values
