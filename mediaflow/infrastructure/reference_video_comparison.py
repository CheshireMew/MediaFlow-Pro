from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

from mediaflow.domain.reference_comparison import (
    ComparedMediaIdentity,
    ReferenceComparisonAcceptance,
    ReferenceComparisonArtifact,
    ReferenceComparisonArtifacts,
    ReferenceComparisonResult,
    ReferenceComparisonSummary,
)
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.ffmpeg_runner import FfmpegOutputPipe, FfmpegRunner
from mediaflow.infrastructure.ffprobe_runner import FfprobeRunner
from mediaflow.infrastructure.runtime_paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class _VideoProbe:
    path: Path
    codec: str
    pixel_format: str
    width: int
    height: int
    frame_rate: Fraction
    frame_count: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class _FramePair:
    sequence_index: int
    reference_frame_index: int
    candidate_frame_index: int
    reference: np.ndarray
    candidate: np.ndarray
    mae: float
    psnr_db: float | None
    best_reference_frame_index: int
    best_offset_frames: int
    best_mae: float


class _RawFrameReader:
    def __init__(
        self,
        ffmpeg: FfmpegRunner,
        source: Path,
        *,
        start_frame: int,
        frame_count: int,
        width: int,
        height: int,
    ) -> None:
        self.frame_bytes = width * height * 3
        self.shape = (height, width, 3)
        self.expected_frames = frame_count
        self.read_frames = 0
        end_frame = start_frame + frame_count
        self.pipe: FfmpegOutputPipe = ffmpeg.open_output_pipe(
            [
                "-v",
                "error",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-vf",
                f"trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS",
                "-an",
                "-sn",
                "-vsync",
                "0",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ]
        )

    def read(self) -> np.ndarray:
        data = self.pipe.read(self.frame_bytes)
        if len(data) != self.frame_bytes:
            self._raise_decode_error(len(data))
        self.read_frames += 1
        return np.frombuffer(data, dtype=np.uint8).reshape(self.shape).copy()

    def finish(self) -> None:
        if self.read_frames != self.expected_frames:
            raise RuntimeError(
                "FFmpeg decoded an incomplete frame range: "
                f"expected {self.expected_frames}, read {self.read_frames}"
            )
        trailing = self.pipe.read(1)
        if trailing:
            self.pipe.abort()
            raise RuntimeError("FFmpeg decoded more frames than the requested range")
        result = self.pipe.finish()
        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg frame decoding failed: " + result.stderr.strip()[-2000:]
            )

    def close_after_error(self) -> None:
        self.pipe.abort()

    def _raise_decode_error(self, received_bytes: int) -> None:
        result = self.pipe.finish()
        raise RuntimeError(
            "FFmpeg did not decode the requested frame: "
            f"expected {self.frame_bytes} bytes, received {received_bytes}. "
            + result.stderr.strip()[-2000:]
        )


class ReferenceVideoComparisonService:
    REPORT_NAME = "reference-comparison.json"
    CONTACT_SHEET_NAME = "reference-comparison-contact-sheet.png"
    WORST_FRAME_NAME = "reference-comparison-worst-frame.png"

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.ffmpeg = FfmpegRunner(paths.ffmpeg)
        self.ffprobe = FfprobeRunner(paths.ffprobe)

    def compare(
        self,
        *,
        reference_path: str | Path,
        candidate_path: str | Path,
        output_dir: str | Path,
        reference_start_frame: int = 0,
        candidate_start_frame: int = 0,
        frame_count: int | None = None,
        temporal_search_radius_frames: int = 0,
        boundary_frame_count: int = 3,
        contact_sheet_rows: int = 8,
        acceptance: ReferenceComparisonAcceptance | None = None,
        overwrite: bool = False,
    ) -> ReferenceComparisonResult:
        reference = self._probe(reference_path)
        candidate = self._probe(candidate_path)
        if not 0 <= temporal_search_radius_frames <= 5:
            raise ValueError("temporal_search_radius_frames must be between 0 and 5")
        if not 1 <= boundary_frame_count <= 30:
            raise ValueError("boundary_frame_count must be between 1 and 30")
        if not 1 <= contact_sheet_rows <= 20:
            raise ValueError("contact_sheet_rows must be between 1 and 20")
        self._validate_compatible(reference, candidate)
        self._validate_start(reference, reference_start_frame, "reference")
        self._validate_start(candidate, candidate_start_frame, "candidate")
        reference_remaining = reference.frame_count - reference_start_frame
        candidate_remaining = candidate.frame_count - candidate_start_frame
        compared_frames = (
            min(reference_remaining, candidate_remaining)
            if frame_count is None
            else frame_count
        )
        if compared_frames <= 0:
            raise ValueError("The selected comparison range contains no video frames")
        if compared_frames > reference_remaining:
            raise ValueError("frame_count exceeds the remaining reference frames")
        if compared_frames > candidate_remaining:
            raise ValueError("frame_count exceeds the remaining candidate frames")

        destination = Path(output_dir).expanduser().resolve()
        artifact_paths = {
            "report": destination / self.REPORT_NAME,
            "contact_sheet": destination / self.CONTACT_SHEET_NAME,
            "worst_frame": destination / self.WORST_FRAME_NAME,
        }
        conflicts = [path for path in artifact_paths.values() if path.exists()]
        if conflicts and not overwrite:
            raise FileExistsError(
                "Reference comparison outputs already exist: "
                + ", ".join(str(path) for path in conflicts)
            )
        destination.mkdir(parents=True, exist_ok=True)

        metrics, retained_pairs, frame_metrics = self._compare_frames(
            reference,
            candidate,
            reference_start_frame=reference_start_frame,
            candidate_start_frame=candidate_start_frame,
            frame_count=compared_frames,
            temporal_search_radius_frames=temporal_search_radius_frames,
            boundary_frame_count=boundary_frame_count,
            contact_sheet_rows=contact_sheet_rows,
        )
        metrics = metrics.model_copy(
            update={
                "frame_count_delta": reference_remaining - candidate_remaining
            }
        )
        acceptance_failures = self._acceptance_failures(
            acceptance,
            metrics,
            reference_remaining=reference_remaining,
            candidate_remaining=candidate_remaining,
        )
        status: Literal["measured", "passed", "failed"] = (
            "measured"
            if acceptance is None
            else "failed" if acceptance_failures else "passed"
        )
        reference_identity = self._identity(
            reference,
            start_frame=reference_start_frame,
            selected_frame_count=compared_frames,
            remaining_frame_count=reference_remaining,
        )
        candidate_identity = self._identity(
            candidate,
            start_frame=candidate_start_frame,
            selected_frame_count=compared_frames,
            remaining_frame_count=candidate_remaining,
        )

        worst_pair = max(retained_pairs, key=lambda item: item.mae)
        self._write_pair_image(artifact_paths["worst_frame"], worst_pair)
        self._write_contact_sheet(
            artifact_paths["contact_sheet"],
            retained_pairs,
        )
        report_document = {
            "protocol": "mediaflow-reference-comparison",
            "version": 1,
            "status": status,
            "reference": reference_identity.model_dump(mode="json"),
            "candidate": candidate_identity.model_dump(mode="json"),
            "summary": metrics.model_dump(mode="json"),
            "acceptance": (
                acceptance.model_dump(mode="json") if acceptance is not None else None
            ),
            "acceptance_failures": acceptance_failures,
            "frames": frame_metrics,
            "artifacts": {
                "contact_sheet": str(artifact_paths["contact_sheet"]),
                "worst_frame": str(artifact_paths["worst_frame"]),
            },
        }
        artifact_paths["report"].write_text(
            json.dumps(report_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifacts = ReferenceComparisonArtifacts(
            report=self._artifact(artifact_paths["report"]),
            contact_sheet=self._artifact(artifact_paths["contact_sheet"]),
            worst_frame=self._artifact(artifact_paths["worst_frame"]),
        )
        return ReferenceComparisonResult(
            status=status,
            reference=reference_identity,
            candidate=candidate_identity,
            summary=metrics,
            acceptance=acceptance,
            acceptance_failures=acceptance_failures,
            artifacts=artifacts,
        )

    def _probe(self, source: str | Path) -> _VideoProbe:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Video file not found: {path}")
        result = self.ffprobe.run(
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                (
                    "stream=codec_name,pix_fmt,width,height,r_frame_rate,"
                    "avg_frame_rate,nb_frames,nb_read_frames,duration"
                ),
                "-of",
                "json",
                str(path),
            ],
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"FFprobe failed for {path}: {result.stderr.strip()[-2000:]}"
            )
        document = json.loads(result.stdout)
        streams = document.get("streams") or []
        if len(streams) != 1:
            raise ValueError(f"No primary video stream found: {path}")
        stream = streams[0]
        frame_count = self._integer(stream.get("nb_read_frames")) or self._integer(
            stream.get("nb_frames")
        )
        if frame_count is None or frame_count <= 0:
            raise ValueError(f"Unable to determine decoded frame count: {path}")
        frame_rate_text = str(stream.get("avg_frame_rate") or "")
        nominal_frame_rate_text = str(stream.get("r_frame_rate") or "")
        try:
            frame_rate = Fraction(frame_rate_text)
            nominal_frame_rate = Fraction(nominal_frame_rate_text)
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError(
                f"Invalid video frame rate for {path}: "
                f"average={frame_rate_text}, nominal={nominal_frame_rate_text}"
            ) from error
        if frame_rate <= 0 or nominal_frame_rate <= 0:
            raise ValueError(f"Invalid video frame rate for {path}: {frame_rate_text}")
        if frame_rate != nominal_frame_rate:
            raise ValueError(
                "Variable-frame-rate video requires normalization before frame-index "
                f"comparison: {path}"
            )
        duration = self._float(stream.get("duration"))
        if duration is None:
            duration = frame_count / float(frame_rate)
        return _VideoProbe(
            path=path,
            codec=str(stream.get("codec_name") or "unknown"),
            pixel_format=str(stream.get("pix_fmt") or "unknown"),
            width=int(stream.get("width") or 0),
            height=int(stream.get("height") or 0),
            frame_rate=frame_rate,
            frame_count=frame_count,
            duration_seconds=max(0.0, duration),
        )

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _validate_compatible(reference: _VideoProbe, candidate: _VideoProbe) -> None:
        if (reference.width, reference.height) != (candidate.width, candidate.height):
            raise ValueError(
                "Reference and candidate dimensions must match for frame comparison"
            )
        if reference.frame_rate != candidate.frame_rate:
            raise ValueError(
                "Reference and candidate frame rates must match for frame comparison"
            )

    @staticmethod
    def _validate_start(video: _VideoProbe, start_frame: int, label: str) -> None:
        if start_frame < 0 or start_frame >= video.frame_count:
            raise ValueError(
                f"{label}_start_frame must select an existing decoded frame"
            )

    def _compare_frames(
        self,
        reference: _VideoProbe,
        candidate: _VideoProbe,
        *,
        reference_start_frame: int,
        candidate_start_frame: int,
        frame_count: int,
        temporal_search_radius_frames: int,
        boundary_frame_count: int,
        contact_sheet_rows: int,
    ) -> tuple[ReferenceComparisonSummary, list[_FramePair], list[dict[str, Any]]]:
        expanded_reference_start = max(
            0, reference_start_frame - temporal_search_radius_frames
        )
        expanded_reference_end = min(
            reference.frame_count,
            reference_start_frame
            + frame_count
            + temporal_search_radius_frames,
        )
        reference_reader = _RawFrameReader(
            self.ffmpeg,
            reference.path,
            start_frame=expanded_reference_start,
            frame_count=expanded_reference_end - expanded_reference_start,
            width=reference.width,
            height=reference.height,
        )
        candidate_reader = _RawFrameReader(
            self.ffmpeg,
            candidate.path,
            start_frame=candidate_start_frame,
            frame_count=frame_count,
            width=candidate.width,
            height=candidate.height,
        )
        reference_buffer: dict[int, np.ndarray] = {}
        next_reference_index = expanded_reference_start
        exact_frames = 0
        mae_total = 0.0
        maximum_mae = -1.0
        maximum_mae_frame = 0
        minimum_psnr: float | None = None
        minimum_psnr_frame: int | None = None
        temporal_mismatches = 0
        maximum_temporal_offset = 0
        first_boundary: list[_FramePair] = []
        last_boundary: deque[_FramePair] = deque(maxlen=boundary_frame_count)
        retained_heap: list[tuple[float, int, _FramePair]] = []
        frame_metrics: list[dict[str, Any]] = []
        try:
            for sequence_index in range(frame_count):
                reference_index = reference_start_frame + sequence_index
                upper = min(
                    expanded_reference_end - 1,
                    reference_index + temporal_search_radius_frames,
                )
                while next_reference_index <= upper:
                    reference_buffer[next_reference_index] = reference_reader.read()
                    next_reference_index += 1
                lower = max(
                    expanded_reference_start,
                    reference_index - temporal_search_radius_frames,
                )
                for old_index in [
                    index for index in reference_buffer if index < lower
                ]:
                    del reference_buffer[old_index]
                candidate_frame = candidate_reader.read()
                reference_frame = reference_buffer[reference_index]
                aligned_mae, aligned_psnr = self._frame_error(
                    reference_frame, candidate_frame
                )
                best_reference_index = reference_index
                best_mae = aligned_mae
                for nearby_index in range(lower, upper + 1):
                    if nearby_index == reference_index:
                        continue
                    nearby_mae, _ = self._frame_error(
                        reference_buffer[nearby_index], candidate_frame
                    )
                    if nearby_mae < best_mae - 1e-12:
                        best_mae = nearby_mae
                        best_reference_index = nearby_index
                best_offset = best_reference_index - reference_index
                if best_offset != 0:
                    temporal_mismatches += 1
                maximum_temporal_offset = max(
                    maximum_temporal_offset, abs(best_offset)
                )
                if aligned_mae == 0:
                    exact_frames += 1
                mae_total += aligned_mae
                if aligned_mae > maximum_mae:
                    maximum_mae = aligned_mae
                    maximum_mae_frame = sequence_index
                if aligned_psnr is not None and (
                    minimum_psnr is None or aligned_psnr < minimum_psnr
                ):
                    minimum_psnr = aligned_psnr
                    minimum_psnr_frame = sequence_index
                pair = _FramePair(
                    sequence_index=sequence_index,
                    reference_frame_index=reference_index,
                    candidate_frame_index=candidate_start_frame + sequence_index,
                    reference=reference_frame.copy(),
                    candidate=candidate_frame.copy(),
                    mae=aligned_mae,
                    psnr_db=aligned_psnr,
                    best_reference_frame_index=best_reference_index,
                    best_offset_frames=best_offset,
                    best_mae=best_mae,
                )
                if sequence_index < boundary_frame_count:
                    first_boundary.append(pair)
                last_boundary.append(pair)
                self._retain_worst(retained_heap, pair, contact_sheet_rows)
                frame_metrics.append(
                    {
                        "sequence_index": sequence_index,
                        "reference_frame_index": reference_index,
                        "candidate_frame_index": candidate_start_frame + sequence_index,
                        "reference_frame_sha256": self._frame_sha256(reference_frame),
                        "candidate_frame_sha256": self._frame_sha256(candidate_frame),
                        "exact": aligned_mae == 0,
                        "mean_absolute_error": aligned_mae,
                        "psnr_db": aligned_psnr,
                        "best_reference_frame_index": best_reference_index,
                        "best_offset_frames": best_offset,
                        "best_mean_absolute_error": best_mae,
                    }
                )
            reference_reader.finish()
            candidate_reader.finish()
        except Exception:
            reference_reader.close_after_error()
            candidate_reader.close_after_error()
            raise

        boundary_pairs = {
            pair.sequence_index: pair for pair in [*first_boundary, *last_boundary]
        }
        worst_pairs = [
            pair
            for _, _, pair in sorted(
                retained_heap,
                key=lambda item: (item[0], item[1]),
                reverse=True,
            )
        ]
        retained_by_index: dict[int, _FramePair] = {}
        for pair in [
            *worst_pairs[:1],
            *first_boundary,
            *last_boundary,
            *worst_pairs[1:],
        ]:
            retained_by_index.setdefault(pair.sequence_index, pair)
            if len(retained_by_index) >= contact_sheet_rows:
                break
        retained = sorted(retained_by_index.values(), key=lambda item: item.sequence_index)
        boundary_maximum = max(
            (pair.mae for pair in boundary_pairs.values()),
            default=0.0,
        )
        summary = ReferenceComparisonSummary(
            compared_frame_count=frame_count,
            frame_count_delta=reference.frame_count - candidate.frame_count,
            exact_frame_count=exact_frames,
            exact_frame_ratio=exact_frames / frame_count,
            mean_absolute_error=mae_total / frame_count,
            maximum_mean_absolute_error=max(0.0, maximum_mae),
            maximum_mean_absolute_error_frame=maximum_mae_frame,
            minimum_psnr_db=minimum_psnr,
            minimum_psnr_frame=minimum_psnr_frame,
            maximum_boundary_mean_absolute_error=boundary_maximum,
            temporal_search_radius_frames=temporal_search_radius_frames,
            temporal_mismatch_count=temporal_mismatches,
            maximum_temporal_offset_frames=maximum_temporal_offset,
        )
        return summary, retained, frame_metrics

    @staticmethod
    def _frame_error(
        reference: np.ndarray,
        candidate: np.ndarray,
    ) -> tuple[float, float | None]:
        difference = cv2.absdiff(reference, candidate)
        mae = float(np.mean(difference, dtype=np.float64))
        if mae == 0:
            return 0.0, None
        squared = np.square(
            reference.astype(np.float32) - candidate.astype(np.float32)
        )
        mse = float(np.mean(squared, dtype=np.float64))
        psnr = 20.0 * math.log10(255.0 / math.sqrt(mse))
        return mae, psnr

    @staticmethod
    def _frame_sha256(frame: np.ndarray) -> str:
        return hashlib.sha256(frame.tobytes(order="C")).hexdigest()

    @staticmethod
    def _retain_worst(
        heap: list[tuple[float, int, _FramePair]],
        pair: _FramePair,
        limit: int,
    ) -> None:
        item = (pair.mae, pair.sequence_index, pair)
        if len(heap) < limit:
            heapq.heappush(heap, item)
        elif item[:2] > heap[0][:2]:
            heapq.heapreplace(heap, item)

    @staticmethod
    def _acceptance_failures(
        acceptance: ReferenceComparisonAcceptance | None,
        summary: ReferenceComparisonSummary,
        *,
        reference_remaining: int,
        candidate_remaining: int,
    ) -> list[str]:
        if acceptance is None:
            return []
        failures: list[str] = []
        if (
            acceptance.require_same_remaining_frame_count
            and reference_remaining != candidate_remaining
        ):
            failures.append(
                "remaining_frame_count differs: "
                f"reference={reference_remaining}, candidate={candidate_remaining}"
            )
        if (
            acceptance.minimum_exact_frame_ratio is not None
            and summary.exact_frame_ratio < acceptance.minimum_exact_frame_ratio
        ):
            failures.append(
                f"exact_frame_ratio {summary.exact_frame_ratio:.9f} is below "
                f"{acceptance.minimum_exact_frame_ratio:.9f}"
            )
        if (
            acceptance.maximum_mean_absolute_error is not None
            and summary.mean_absolute_error
            > acceptance.maximum_mean_absolute_error
        ):
            failures.append(
                f"mean_absolute_error {summary.mean_absolute_error:.9f} exceeds "
                f"{acceptance.maximum_mean_absolute_error:.9f}"
            )
        if (
            acceptance.maximum_boundary_mean_absolute_error is not None
            and summary.maximum_boundary_mean_absolute_error
            > acceptance.maximum_boundary_mean_absolute_error
        ):
            failures.append(
                "maximum_boundary_mean_absolute_error "
                f"{summary.maximum_boundary_mean_absolute_error:.9f} exceeds "
                f"{acceptance.maximum_boundary_mean_absolute_error:.9f}"
            )
        if acceptance.minimum_psnr_db is not None:
            actual = summary.minimum_psnr_db
            if actual is not None and actual < acceptance.minimum_psnr_db:
                failures.append(
                    f"minimum_psnr_db {actual:.9f} is below "
                    f"{acceptance.minimum_psnr_db:.9f}"
                )
        if (
            acceptance.maximum_temporal_mismatch_count is not None
            and summary.temporal_mismatch_count
            > acceptance.maximum_temporal_mismatch_count
        ):
            failures.append(
                f"temporal_mismatch_count {summary.temporal_mismatch_count} exceeds "
                f"{acceptance.maximum_temporal_mismatch_count}"
            )
        return failures

    def _identity(
        self,
        video: _VideoProbe,
        *,
        start_frame: int,
        selected_frame_count: int,
        remaining_frame_count: int,
    ) -> ComparedMediaIdentity:
        return ComparedMediaIdentity(
            path=str(video.path),
            sha256=sha256_file(video.path),
            codec=video.codec,
            pixel_format=video.pixel_format,
            width=video.width,
            height=video.height,
            frame_rate_numerator=video.frame_rate.numerator,
            frame_rate_denominator=video.frame_rate.denominator,
            total_frame_count=video.frame_count,
            duration_seconds=video.duration_seconds,
            selected_start_frame=start_frame,
            selected_frame_count=selected_frame_count,
            remaining_frame_count=remaining_frame_count,
        )

    def _write_pair_image(self, path: Path, pair: _FramePair) -> None:
        row = self._pair_row(pair)
        self._write_png(path, row)

    def _write_contact_sheet(self, path: Path, pairs: list[_FramePair]) -> None:
        rows = [self._pair_row(pair) for pair in pairs]
        width = max(row.shape[1] for row in rows)
        normalized = [
            cv2.copyMakeBorder(
                row,
                0,
                0,
                0,
                width - row.shape[1],
                cv2.BORDER_CONSTANT,
                value=(12, 12, 12),
            )
            for row in rows
        ]
        self._write_png(path, np.vstack(normalized))

    @staticmethod
    def _pair_row(pair: _FramePair) -> np.ndarray:
        reference = ReferenceVideoComparisonService._preview(pair.reference)
        candidate = ReferenceVideoComparisonService._preview(pair.candidate)
        height = max(reference.shape[0], candidate.shape[0])
        reference = ReferenceVideoComparisonService._pad_height(reference, height)
        candidate = ReferenceVideoComparisonService._pad_height(candidate, height)
        row = np.hstack([reference, candidate])
        label_height = 34
        canvas = np.zeros((height + label_height, row.shape[1], 3), dtype=np.uint8)
        canvas[label_height:, :, :] = cv2.cvtColor(row, cv2.COLOR_RGB2BGR)
        left_label = f"ref {pair.reference_frame_index}"
        right_label = (
            f"cand {pair.candidate_frame_index} mae={pair.mae:.4f} "
            f"offset={pair.best_offset_frames:+d}"
        )
        cv2.putText(
            canvas,
            left_label,
            (8, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            right_label,
            (reference.shape[1] + 8, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
        return canvas

    @staticmethod
    def _preview(frame: np.ndarray) -> np.ndarray:
        max_width = 480
        max_height = 270
        scale = min(max_width / frame.shape[1], max_height / frame.shape[0], 1.0)
        if scale == 1.0:
            return frame
        return cv2.resize(
            frame,
            (max(1, round(frame.shape[1] * scale)), max(1, round(frame.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _pad_height(frame: np.ndarray, height: int) -> np.ndarray:
        if frame.shape[0] == height:
            return frame
        return cv2.copyMakeBorder(
            frame,
            0,
            height - frame.shape[0],
            0,
            0,
            cv2.BORDER_CONSTANT,
            value=(12, 12, 12),
        )

    @staticmethod
    def _write_png(path: Path, image: np.ndarray) -> None:
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError(f"OpenCV could not encode comparison image: {path}")
        path.write_bytes(encoded.tobytes())

    def _artifact(self, path: Path) -> ReferenceComparisonArtifact:
        return ReferenceComparisonArtifact(
            path=str(path),
            sha256=sha256_file(path),
            bytes=path.stat().st_size,
        )
