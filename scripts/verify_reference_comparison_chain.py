from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_RUN_ROOT = Path(
    "D:/Tools/MediaFlow/test-runs/reference-comparison-chain"
)


def _new_run_dir(root: Path) -> Path:
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = root / f"r-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(exist_ok=False)
    return run_dir


def _request(
    operation: str,
    arguments: dict[str, object],
    *,
    project: Path | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "protocol": "mediaflow-cli",
        "version": 2,
        "operation": operation,
        "arguments": arguments,
        "request_id": f"reference-chain-{operation}-{uuid.uuid4().hex}",
    }
    if project is not None:
        request["project"] = str(project)
    return request


def verify(package: Path, run_dir: Path) -> dict[str, object]:
    from mediaflow.automation.contracts import describe_contract
    from mediaflow.automation.dispatcher import execute_request

    source = package.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"editable-media package must be a directory: {source}")
    os.environ["MEDIAFLOW_PROJECT_ROOT"] = str(run_dir)
    contract = describe_contract()
    if contract["product"] != "MediaFlow Pro":
        raise RuntimeError("describe did not identify MediaFlow Pro")
    operations = {item["name"]: item for item in contract["operations"]}
    comparison_contract = operations.get("quality.reference.compare")
    if comparison_contract is None:
        raise RuntimeError("quality.reference.compare is missing from describe")
    if comparison_contract["project_access"] != "none":
        raise RuntimeError("quality.reference.compare must remain projectless")
    runtime = execute_request(_request("runtime.inspect", {}))
    runtime_status = {item["id"]: item["status"] for item in runtime["capabilities"]}
    for capability in ("ffmpeg", "ffprobe", "chromium"):
        if runtime_status.get(capability) != "ready":
            raise RuntimeError(
                f"Required runtime capability is not ready: {capability}="
                f"{runtime_status.get(capability, 'missing')}"
            )

    created = execute_request(
        _request(
            "project.create",
            {
                "name": "Reference comparison real chain",
                "directory_name": "reference-comparison-project",
            },
        )
    )
    project = Path(str(created["path"]))
    sequence_id = str(created["project"]["main_sequence_id"])
    imported = execute_request(
        _request("web.import", {"source": str(source)}, project=project)
    )
    asset = imported["asset"]
    asset_id = str(asset["id"])
    duration_frames = int(asset["metadata"]["duration_frames"])
    if duration_frames <= 0:
        raise RuntimeError("The real producer package imported with no duration")
    track = execute_request(
        _request(
            "timeline.track.add",
            {"sequence_id": sequence_id, "kind": "video"},
            project=project,
        )
    )
    clip = execute_request(
        _request(
            "timeline.clip.add",
            {
                "sequence_id": sequence_id,
                "track_id": str(track["track"]["id"]),
                "asset_id": asset_id,
                "timeline_start": 0,
                "source_in": 0,
                "duration": duration_frames,
            },
            project=project,
        )
    )
    clip_id = str(clip["clip"]["id"])
    updated = execute_request(
        _request(
            "web.clip.update",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "scene_id": "opening",
                "updates": {
                    "title": {"content": "Reference comparison real chain"}
                },
                "expected_revision": 0,
                "actor": "automation",
            },
            project=project,
        )
    )
    revision = int(updated["web_clip_state"]["revision"])
    selected = execute_request(
        _request(
            "web.clip.variant.select",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "variant_id": "landscape",
                "expected_revision": revision,
            },
            project=project,
        )
    )
    revision = int(selected["web_clip_state"]["revision"])
    execute_request(
        _request(
            "web.clip.render",
            {"sequence_id": sequence_id, "clip_id": clip_id, "timeout": 600},
            project=project,
        )
    )
    output = run_dir / "exports" / "editable-media-final.mp4"
    execute_request(
        _request(
            "web.clip.export",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "output_path": str(output),
                "format": "video",
                "overwrite": False,
                "timeout": 600,
            },
            project=project,
        )
    )
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("MediaFlow Pro did not create the real web export")
    reopened = execute_request(
        _request("web.clip.get", {"clip_id": clip_id}, project=project)
    )
    reopened_state = reopened["web_clip_state"]
    if int(reopened_state["revision"]) != revision:
        raise RuntimeError("Reopened web clip revision does not match the rendered state")
    if (
        reopened_state["scenes"]["opening"]["layers"]["title"]["content"]
        != "Reference comparison real chain"
    ):
        raise RuntimeError("Reopened web state lost the producer edit")

    comparison = execute_request(
        _request(
            "quality.reference.compare",
            {
                "reference_path": str(output),
                "candidate_path": str(output),
                "output_dir": str(run_dir / "comparison"),
                "temporal_search_radius_frames": 1,
                "boundary_frame_count": 3,
                "contact_sheet_rows": 8,
                "acceptance": {
                    "minimum_exact_frame_ratio": 1,
                    "maximum_mean_absolute_error": 0,
                    "maximum_boundary_mean_absolute_error": 0,
                    "maximum_temporal_mismatch_count": 0,
                },
            },
        )
    )
    if comparison["status"] != "passed":
        raise RuntimeError(
            "The real exported video did not pass exact self-comparison: "
            + json.dumps(comparison["acceptance_failures"], ensure_ascii=False)
        )
    report = {
        "schema": "mediaflow-reference-comparison-chain/v1",
        "status": "passed",
        "source_package": str(source),
        "project": str(project),
        "sequence_id": sequence_id,
        "asset_id": asset_id,
        "clip_id": clip_id,
        "web_clip_revision": revision,
        "duration_frames": duration_frames,
        "export": str(output),
        "export_bytes": output.stat().st_size,
        "comparison": comparison,
    }
    report_path = run_dir / "reference-comparison-chain-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["report"] = str(report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_RUN_ROOT)
    arguments = parser.parse_args(argv)
    run_dir = _new_run_dir(arguments.root)
    report = verify(arguments.package, run_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
