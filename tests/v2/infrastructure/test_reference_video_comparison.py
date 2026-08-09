from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mediaflow.automation.contracts import describe_contract
from mediaflow.domain.reference_comparison import ReferenceComparisonAcceptance
from mediaflow.infrastructure.reference_video_comparison import (
    ReferenceVideoComparisonService,
)
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths


def _generate_reference(path: Path, paths: RuntimePaths) -> None:
    completed = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=96x64:rate=6:duration=1",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert path.is_file() and path.stat().st_size > 0


def _transcode_lossy(source: Path, destination: Path, paths: RuntimePaths) -> None:
    completed = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "40",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert destination.is_file() and destination.stat().st_size > 0


def _run_cli(request_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mediaflow.cli",
            "execute",
            "--request",
            str(request_path),
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def test_reference_comparison_is_public_and_projectless() -> None:
    contract = describe_contract()
    capabilities = {item["id"] for item in contract["capabilities"]}
    operations = {item["name"]: item for item in contract["operations"]}

    assert "reference-video-comparison" in capabilities
    operation = operations["quality.reference.compare"]
    assert operation["project_access"] == "none"
    assert operation["required_capabilities"] == [
        "reference-video-comparison",
        "ffmpeg",
        "ffprobe",
    ]
    assert operation["arguments_schema"]["required"] == [
        "reference_path",
        "candidate_path",
        "output_dir",
    ]


def test_reference_comparison_proves_exact_delivery_and_writes_evidence(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    reference = tmp_path / "reference.mp4"
    _generate_reference(reference, paths)

    result = ReferenceVideoComparisonService(paths).compare(
        reference_path=reference,
        candidate_path=reference,
        output_dir=tmp_path / "exact-evidence",
        temporal_search_radius_frames=1,
        acceptance=ReferenceComparisonAcceptance(
            minimum_exact_frame_ratio=1,
            maximum_mean_absolute_error=0,
            maximum_boundary_mean_absolute_error=0,
            maximum_temporal_mismatch_count=0,
        ),
    )

    assert result.status == "passed"
    assert result.summary.compared_frame_count == 6
    assert result.summary.exact_frame_count == 6
    assert result.summary.exact_frame_ratio == 1
    assert result.summary.mean_absolute_error == 0
    assert result.summary.minimum_psnr_db is None
    assert result.summary.temporal_mismatch_count == 0
    for artifact in (
        result.artifacts.report,
        result.artifacts.contact_sheet,
        result.artifacts.worst_frame,
    ):
        path = Path(artifact.path)
        assert path.is_file()
        assert path.stat().st_size == artifact.bytes
    report = json.loads(Path(result.artifacts.report.path).read_text(encoding="utf-8"))
    assert len(report["frames"]) == 6
    assert all(frame["exact"] for frame in report["frames"])
    assert all(
        frame["reference_frame_sha256"] == frame["candidate_frame_sha256"]
        for frame in report["frames"]
    )


def test_reference_comparison_detects_one_frame_temporal_shift(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    reference = tmp_path / "reference.mp4"
    _generate_reference(reference, paths)

    result = ReferenceVideoComparisonService(paths).compare(
        reference_path=reference,
        candidate_path=reference,
        output_dir=tmp_path / "shift-evidence",
        reference_start_frame=0,
        candidate_start_frame=1,
        frame_count=5,
        temporal_search_radius_frames=1,
        acceptance=ReferenceComparisonAcceptance(
            require_same_remaining_frame_count=False,
            maximum_temporal_mismatch_count=0,
        ),
    )

    assert result.status == "failed"
    assert result.summary.frame_count_delta == 1
    assert result.summary.temporal_mismatch_count == 5
    assert result.summary.maximum_temporal_offset_frames == 1
    report = json.loads(Path(result.artifacts.report.path).read_text(encoding="utf-8"))
    assert {frame["best_offset_frames"] for frame in report["frames"]} == {1}
    assert any("temporal_mismatch_count" in item for item in result.acceptance_failures)


def test_reference_comparison_measures_encoded_frame_differences(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    reference = tmp_path / "reference.mp4"
    candidate = tmp_path / "candidate.mp4"
    _generate_reference(reference, paths)
    _transcode_lossy(reference, candidate, paths)

    result = ReferenceVideoComparisonService(paths).compare(
        reference_path=reference,
        candidate_path=candidate,
        output_dir=tmp_path / "encoded-evidence",
        temporal_search_radius_frames=1,
    )

    assert result.status == "measured"
    assert result.summary.exact_frame_ratio < 1
    assert result.summary.mean_absolute_error > 0
    assert result.summary.maximum_mean_absolute_error > 0
    assert result.summary.minimum_psnr_db is not None
    assert result.summary.temporal_mismatch_count == 0


def test_reference_comparison_runs_through_the_real_cli_process(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    reference = tmp_path / "reference.mp4"
    _generate_reference(reference, paths)
    request = tmp_path / "compare-request.json"
    request.write_text(
        json.dumps(
            {
                "protocol": "mediaflow-editor",
                "version": 4,
                "operation": "quality.reference.compare",
                "actor": {"kind": "agent", "id": "reference-comparison-test"},
                "client_id": "pytest-reference-comparison",
                "arguments": {
                    "reference_path": str(reference),
                    "candidate_path": str(reference),
                    "output_dir": str(tmp_path / "cli-evidence"),
                    "temporal_search_radius_frames": 1,
                    "acceptance": {
                        "minimum_exact_frame_ratio": 1,
                        "maximum_mean_absolute_error": 0,
                        "maximum_temporal_mismatch_count": 0,
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = _run_cli(request)

    assert completed.returncode == 0, completed.stdout
    assert completed.stderr == ""
    response = json.loads(completed.stdout)
    assert response["ok"] is True
    operation_result = response["result"]["result"]
    assert operation_result["status"] == "passed"
    assert operation_result["summary"]["exact_frame_ratio"] == 1
    assert Path(operation_result["artifacts"]["report"]["path"]).is_file()
