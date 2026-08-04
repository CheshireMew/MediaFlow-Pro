from __future__ import annotations

import hashlib
import json
import math
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
from .runtime_paths import RuntimePaths


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
        source = self.repository.catalog.resolve_asset_path(asset)
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
            fps = f"{profile.fps_numerator}/{profile.fps_denominator}"
            gop = max(1, math.ceil(profile.fps))
            scale = "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'"
            source_hdr = asset.metadata.color_primaries == "bt2020" and asset.metadata.color_transfer in {
                "smpte2084",
                "arib-std-b67",
            }
            if profile.color_mode == ColorMode.HDR10_BT2020_PQ and not source_hdr:
                color = (
                    "zscale=pin=bt709:tin=bt709:min=bt709:p=bt2020:"
                    "t=smpte2084:m=bt2020nc:r=tv:npl=203:"
                    "d=error_diffusion"
                )
            elif profile.color_mode == ColorMode.SDR_BT709 and source_hdr:
                color = (
                    "zscale=t=linear:npl=100,format=gbrpf32le,"
                    "tonemap=tonemap=mobius:param=0.3:desat=2:peak=10,"
                    "zscale=p=bt709:t=bt709:m=bt709:r=tv:"
                    "d=error_diffusion"
                )
            else:
                color = ""
            filters = ",".join(item for item in (color, scale, f"fps={fps}") if item)
            command = [
                "-n",
                "-v",
                "error",
                "-i",
                str(source),
                "-vf",
                filters,
            ]
            if profile.color_mode == ColorMode.HDR10_BT2020_PQ:
                command.extend(
                    [
                        "-c:v",
                        "libx265",
                        "-profile:v",
                        "main10",
                        "-pix_fmt",
                        "yuv420p10le",
                        "-color_primaries",
                        "bt2020",
                        "-color_trc",
                        "smpte2084",
                        "-colorspace",
                        "bt2020nc",
                        "-crf",
                        "25",
                    ]
                )
            else:
                command.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-crf",
                        "24",
                    ]
                )
            command.extend(
                [
                    "-preset",
                    "veryfast",
                    "-g",
                    str(gop),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(temporary_output),
                ]
            )
            duration_seconds = asset.metadata.duration_frames / profile.fps
            result = self._encode(
                command,
                message_code="proxy_encoding",
                duration_seconds=duration_seconds,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            if (
                result.returncode != 0
                or not temporary_output.is_file()
                or temporary_output.stat().st_size == 0
            ):
                raise RuntimeError(f"Proxy generation failed: {result.stderr.strip()}")
            if temporary_sdr_output is not None:
                sdr_color = (
                    "zscale=t=linear:npl=100,format=gbrpf32le,"
                    "tonemap=tonemap=mobius:param=0.3:desat=2:peak=10,"
                    "zscale=p=bt709:t=bt709:m=bt709:r=tv:d=error_diffusion"
                    if source_hdr
                    else ""
                )
                sdr_filters = ",".join(item for item in (sdr_color, scale, f"fps={fps}") if item)
                sdr_result = self._encode(
                    [
                        "-n",
                        "-v",
                        "error",
                        "-i",
                        str(source),
                        "-vf",
                        sdr_filters,
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-color_primaries",
                        "bt709",
                        "-color_trc",
                        "bt709",
                        "-colorspace",
                        "bt709",
                        "-x264-params",
                        "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                        "-crf",
                        "24",
                        "-preset",
                        "veryfast",
                        "-g",
                        str(gop),
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        str(temporary_sdr_output),
                    ],
                    message_code="proxy_sdr_encoding",
                    duration_seconds=duration_seconds,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
                if (
                    sdr_result.returncode != 0
                    or not temporary_sdr_output.is_file()
                    or temporary_sdr_output.stat().st_size == 0
                ):
                    raise RuntimeError(f"SDR preview proxy generation failed: {sdr_result.stderr.strip()}")
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
            )

    def commit_prepared(self, prepared: PreparedProxyGeneration) -> Asset:
        try:
            return self.repository.catalog.set_asset_proxy_paths(
                prepared.asset_id,
                expected_fingerprint=prepared.expected_fingerprint,
                proxy_path=prepared.proxy_path,
                sdr_preview_proxy_path=prepared.sdr_preview_proxy_path,
            )
        except BaseException as error:
            self._rollback_prepared(prepared, error)
            raise

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
