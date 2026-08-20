# ruff: noqa: E402

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

from mediaflow.environment import test_run_root
from scripts.run_artifacts import verification_workspace_root

DEFAULT_RUN_ROOT = test_run_root() / "reference-comparison-chain"


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
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": operation,
        "arguments": arguments,
        "request_id": f"reference-chain-{operation}-{uuid.uuid4().hex}",
        "actor": {"kind": "agent", "id": "reference-comparison-chain"},
        "client_id": "reference-comparison-chain",
    }
    if project is not None:
        request["project"] = str(project)
    return request


def verify(package: Path, run_dir: Path) -> dict[str, object]:
    from mediaflow.automation.contracts import describe_contract
    from mediaflow.automation.operation_registry import OPERATIONS
    from mediaflow.domain.project import ProjectProfile
    from mediaflow.service.client import call_sync, execute_sync

    source = package.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"editable-media package must be a directory: {source}")
    workspace = verification_workspace_root(run_dir)
    os.environ["MEDIAFLOW_PROJECT_ROOT"] = str(workspace / "projects")
    os.environ["MEDIAFLOW_MEDIA_ROOT"] = str(workspace / "media")
    os.environ["MEDIAFLOW_SERVICE_STATE_DIR"] = str(workspace / "service")
    observed_revisions: dict[str, int] = {}

    def execute_request(request: dict[str, object]) -> dict[str, object]:
        operation = str(request["operation"])
        definition = OPERATIONS[operation]
        project_value = request.get("project")
        project_text = str(Path(str(project_value)).resolve()) if project_value else None
        if definition.project_access == "write":
            if project_text is None:
                raise RuntimeError(f"{operation} requires a project")
            revision = observed_revisions.get(project_text)
            if revision is None:
                descriptor = call_sync(
                    "project.snapshot",
                    {"project": project_text},
                )
                revision = int(descriptor["project_revision"])
            request["base_revision"] = revision
        response = execute_sync(request)
        result = response["result"]
        revision_value = response.get("project_revision")
        if project_text is None and operation == "project.create":
            project_text = str(Path(str(result["path"])).resolve())
        if project_text is not None and revision_value is not None:
            observed_revisions[project_text] = int(revision_value)
        return result

    def wait_for_task(receipt: dict[str, object], project: Path) -> dict[str, object]:
        task = receipt.get("task")
        if not isinstance(task, dict) or not task.get("id"):
            raise RuntimeError("Task-backed operation returned no task receipt")
        waited = execute_request(
            _request(
                "task.wait",
                {"task_id": str(task["id"]), "timeout": 600},
                project=project,
            )
        )
        completed = waited.get("task")
        if not isinstance(completed, dict) or completed.get("status") != "completed":
            raise RuntimeError(f"Task did not complete: {completed}")
        return completed
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
                "profile": ProjectProfile().model_dump(
                    mode="json",
                    exclude_computed_fields=True,
                ),
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
    render_receipt = execute_request(
        _request(
            "web.clip.render",
            {"sequence_id": sequence_id, "clip_id": clip_id, "timeout": 600},
            project=project,
        )
    )
    output = run_dir / "exports" / "editable-media-final.mp4"
    wait_for_task(render_receipt, project)
    export_receipt = execute_request(
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
    wait_for_task(export_receipt, project)
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
    try:
        report = verify(arguments.package, run_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        from mediaflow.service.client import EditorServiceUnavailable, call_sync

        try:
            call_sync("service.shutdown", start_if_needed=False)
        except EditorServiceUnavailable:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
