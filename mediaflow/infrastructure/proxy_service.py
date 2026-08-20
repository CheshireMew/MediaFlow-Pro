from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mediaflow.application.ports import AssetProcessingDocuments
from mediaflow.domain.enums import ColorMode
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import (
    Asset,
    AssetFingerprint,
    ProjectProfile,
)
from mediaflow.domain.storage_names import (
    content_addressed_child_path,
    require_windows_interop_path,
)

from .ffmpeg_runner import FfmpegRunner
from .output_reservation import output_set_transaction
from .proxy_encoding import build_proxy_command
from .runtime_paths import RuntimePaths
from .storage_budget import (
    estimate_proxy_peak_bytes,
    finalize_storage_receipt,
    require_project_artifact_budget,
    start_storage_receipt,
)


@dataclass(frozen=True, slots=True)
class ProxyDecision:
    required: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedProxyGeneration:
    asset_id: str
    expected_fingerprint: AssetFingerprint | None
    proxy_path: Path
    sdr_preview_proxy_path: Path | None
    replaced_outputs: tuple[tuple[Path, Path], ...]
    storage_receipt_path: Path


class ProxyService:
    def __init__(
        self,
        repository: AssetProcessingDocuments,
        paths: RuntimePaths,
    ):
        self.repository = repository
        self.paths = paths
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)

    @staticmethod
    def decision(asset: Asset, *, dropped_frames: int = 0, manual: bool = False) -> ProxyDecision:
        metadata = asset.metadata
        reasons: list[str] = []
        if manual:
            reasons.append("manual")
        if (metadata.width or 0) > 1920 or (metadata.height or 0) > 1080:
            reasons.append("high_resolution")
        if (metadata.bitrate or 0) > 60_000_000:
            reasons.append("high_bitrate")
        if metadata.variable_frame_rate:
            reasons.append("variable_frame_rate")
        if metadata.fps_numerator and metadata.fps_denominator:
            if metadata.fps_numerator / metadata.fps_denominator > 60:
                reasons.append("high_frame_rate")
        if metadata.pixel_format and any(token in metadata.pixel_format for token in ("10", "12", "p010")):
            reasons.append("high_bit_depth")
        if dropped_frames >= 3:
            reasons.append("decoder_drops")
        return ProxyDecision(required=bool(reasons), reasons=tuple(reasons))

    def prepare(
        self,
        asset: Asset,
        profile: ProjectProfile,
        *,
        progress: Callable[[OperationProgress], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PreparedProxyGeneration:
        source = require_windows_interop_path(
            self.repository.assets.resolve_asset_path(asset)
        )
        if not source.is_file():
            raise FileNotFoundError(source)
        output_dir = self.repository.project_dir / "proxies"
        duration_seconds = asset.metadata.duration_frames / profile.fps
        expected_peak = estimate_proxy_peak_bytes(
            duration_seconds,
            output_count=(2 if profile.color_mode == ColorMode.HDR10_BT2020_PQ else 1),
        )
        preflight = require_project_artifact_budget(
            self.repository.project_dir,
            output_dir,
            expected_new_bytes=expected_peak,
            label=f"MediaFlow proxy for {asset.name}",
        )
        receipt = start_storage_receipt(
            self.paths.runtime_dir,
            producer="project-proxy",
            operation_id=f"{uuid.uuid4().hex}:{asset.id}",
            owned_root=output_dir,
            preflight=preflight,
        )
        try:
            return self._prepare_outputs(
                asset,
                profile,
                storage_receipt_path=receipt,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        except BaseException as error:
            finalize_storage_receipt(
                receipt,
                status=("interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed"),
                error=str(error),
            )
            raise

    def _prepare_outputs(
        self,
        asset: Asset,
        profile: ProjectProfile,
        *,
        storage_receipt_path: Path,
        progress: Callable[[OperationProgress], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PreparedProxyGeneration:
        source = self.repository.assets.resolve_asset_path(asset)
        if not source.is_file():
            raise FileNotFoundError(source)
        source = require_windows_interop_path(source)
        output_dir = self.repository.project_dir / "proxies"
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_key = self._cache_key(asset, source, profile)
        output = content_addressed_child_path(
            output_dir,
            f"proxy:v3:{asset.id}:{cache_key}",
            namespace="p",
            suffix=".mp4",
        )
        sdr_preview_output = (
            content_addressed_child_path(
                output_dir,
                f"proxy-sdr:v3:{asset.id}:{cache_key}",
                namespace="ps",
                suffix=".mp4",
            )
            if profile.color_mode == ColorMode.HDR10_BT2020_PQ
            else None
        )
        destinations = [output]
        if sdr_preview_output is not None:
            destinations.append(sdr_preview_output)
        with output_set_transaction(
            destinations,
            overwrite=True,
            runtime_dir=self.paths.runtime_dir,
        ) as publication:
            temporary_output = publication.temporary_path(
                output,
                "proxy",
            )
            temporary_sdr_output = (
                publication.temporary_path(
                    sdr_preview_output,
                    "proxy-sdr",
                )
                if sdr_preview_output is not None
                else None
            )
            duration_seconds = asset.metadata.duration_frames / profile.fps
            self._encode_proxy_output(
                build_proxy_command(
                    source,
                    temporary_output,
                    asset,
                    profile,
                ),
                temporary_output,
                message_code="proxy_encoding",
                failure_label="Proxy",
                duration_seconds=duration_seconds,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            if temporary_sdr_output is not None:
                self._encode_proxy_output(
                    build_proxy_command(
                        source,
                        temporary_sdr_output,
                        asset,
                        profile,
                        force_sdr=True,
                    ),
                    temporary_sdr_output,
                    message_code="proxy_sdr_encoding",
                    failure_label="SDR preview proxy",
                    duration_seconds=duration_seconds,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            if progress:
                progress(OperationProgress.indeterminate("proxy_registering"))
            if check_cancelled:
                check_cancelled()
            publication.publish()
            publication.finalize(
                archive_replaced_to=(self.repository.project_dir / "archive" / "replaced-proxies")
            )
            return PreparedProxyGeneration(
                asset_id=asset.id,
                expected_fingerprint=asset.fingerprint,
                proxy_path=output,
                sdr_preview_proxy_path=sdr_preview_output,
                replaced_outputs=tuple(
                    publication.replaced_output_archives.items()
                ),
                storage_receipt_path=storage_receipt_path,
            )

    def commit_prepared(self, prepared: PreparedProxyGeneration) -> Asset:
        try:
            result = self.repository.assets.set_asset_proxy_paths(
                prepared.asset_id,
                expected_fingerprint=prepared.expected_fingerprint,
                proxy_path=prepared.proxy_path,
                sdr_preview_proxy_path=prepared.sdr_preview_proxy_path,
            )
        except BaseException as error:
            self._rollback_prepared(prepared, error)
            raise
        outputs = (prepared.proxy_path,) + (
            (prepared.sdr_preview_proxy_path,)
            if prepared.sdr_preview_proxy_path is not None
            else ()
        )
        finalize_storage_receipt(
            prepared.storage_receipt_path,
            status="passed",
            outputs=outputs,
        )
        return result

    def generate(
        self,
        asset: Asset,
        profile: ProjectProfile,
        *,
        progress: Callable[[OperationProgress], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Asset:
        return self.commit_prepared(
            self.prepare(
                asset,
                profile,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        )

    def _rollback_prepared(
        self,
        prepared: PreparedProxyGeneration,
        error: BaseException,
    ) -> None:
        retained: list[Path] = []
        destinations = (
            prepared.proxy_path,
            *(
                (prepared.sdr_preview_proxy_path,)
                if prepared.sdr_preview_proxy_path is not None
                else ()
            ),
        )
        for destination in destinations:
            if not destination.is_file():
                continue
            failed = content_addressed_child_path(
                self.repository.project_dir / "archive" / "failed-proxies",
                f"proxy-commit-failed:{prepared.asset_id}:{destination.name}",
                namespace="proxy",
                suffix=destination.suffix,
            )
            try:
                failed.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(failed)
                retained.append(failed)
            except OSError as archive_error:
                error.add_note(
                    f"未登记代理文件无法移入失败归档：{archive_error}"
                )
        for destination, archived in prepared.replaced_outputs:
            if not archived.is_file():
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                archived.replace(destination)
            except OSError as restore_error:
                error.add_note(
                    f"代理登记失败后无法恢复原文件 {destination}：{restore_error}"
                )
        finalize_storage_receipt(
            prepared.storage_receipt_path,
            status="failed",
            outputs=tuple(retained),
            error=str(error),
        )

    @staticmethod
    def _cache_key(asset: Asset, source: Path, profile: ProjectProfile) -> str:
        source_stat = source.stat()
        payload = {
            "version": 3,
            "asset_id": asset.id,
            "fingerprint": (
                asset.fingerprint.model_dump(mode="json") if asset.fingerprint is not None else None
            ),
            "source_size": source_stat.st_size,
            "source_modified_ns": source_stat.st_mtime_ns,
            "profile": profile.model_dump(mode="json"),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _encode_proxy_output(
        self,
        command: list[str],
        output: Path,
        *,
        message_code: str,
        failure_label: str,
        duration_seconds: float,
        progress: Callable[[OperationProgress], None] | None,
        check_cancelled: Callable[[], None] | None,
    ) -> None:
        result = self._encode(
            command,
            message_code=message_code,
            duration_seconds=duration_seconds,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(
                f"{failure_label} generation failed: {result.stderr.strip()}"
            )

    def _encode(
        self,
        command: list[str],
        *,
        message_code: str,
        duration_seconds: float,
        progress: Callable[[OperationProgress], None] | None,
        check_cancelled: Callable[[], None] | None,
    ):
        on_position: Callable[[float], None] | None = None
        if duration_seconds > 0 and progress is not None:

            def report_position(position: float) -> None:
                progress(
                    OperationProgress.determinate(
                        message_code,
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )

            on_position = report_position
        elif duration_seconds <= 0 and progress is not None:
            progress(OperationProgress.indeterminate(message_code))
        return self.ffmpeg.run_progress(
            command,
            total_seconds=duration_seconds,
            on_position=on_position,
            timeout=600,
            check_cancelled=check_cancelled,
        )
