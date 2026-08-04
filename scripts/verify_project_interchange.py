# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.environment import test_run_root

# Cross-platform software encoders and font rasterizers are not bit-identical.
# These bounds reject structural, timing, subtitle, and visibly material drift
# while allowing the small color/antialiasing differences measured across the
# reviewed Windows, Linux, and macOS runtimes.
CROSS_PLATFORM_VISUAL_ACCEPTANCE: dict[str, bool | float | int] = {
    "require_same_remaining_frame_count": True,
    "maximum_mean_absolute_error": 3.0,
    "maximum_boundary_mean_absolute_error": 1.0,
    "minimum_psnr_db": 25.0,
    "maximum_temporal_mismatch_count": 12,
}


def _configure_run_root(root: Path) -> None:
    resolved = root.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    values = {
        "MEDIAFLOW_PROJECT_ROOT": resolved / "projects",
        "MEDIAFLOW_MEDIA_ROOT": resolved / "media",
        "MEDIAFLOW_SERVICE_STATE_DIR": resolved / "service",
        "MEDIAFLOW_TEST_ROOT": resolved / "tests",
    }
    for name, path in values.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)


def _completed(project: Any, task: Any, *, timeout: float = 600) -> Any:
    completed = project.wait_for_task(task.id, timeout=timeout)
    if completed.status.value != "completed":
        raise RuntimeError(
            f"Task {completed.id} ended as {completed.status.value}: {completed.error}"
        )
    return completed


def _generate_source(output: Path) -> None:
    from mediaflow.infrastructure.runtime_context import RuntimeContext

    paths = RuntimeContext.discover().paths
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(paths.ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x1B365D:s=640x360:r=25:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-y",
        str(output),
    ]
    subprocess.run(command, check=True)


def _preset(subtitle_track_id: str):
    from mediaflow.domain.enums import ExportFormat
    from mediaflow.domain.exports import ExportPreset, SubtitleStyle, VideoEncoderPolicy

    return ExportPreset(
        name="Portable H.264 with bundled subtitles",
        format=ExportFormat.H264,
        container="mp4",
        encoder_policy=VideoEncoderPolicy(mode="software"),
        audio_codec="aac",
        pixel_format="yuv420p",
        quality_value=18,
        preset="veryfast",
        gop_frames=25,
        burn_subtitle_track_id=subtitle_track_id,
        subtitle_style=SubtitleStyle(font_family="LXGW WenKai"),
    )


def _export(project: Any, sequence_id: str, subtitle_track_id: str, output: Path) -> Any:
    from mediaflow.domain.enums import ExportFormat
    from mediaflow.domain.task_commands import ExportSequenceCommand

    task = project.start_task(
        ExportSequenceCommand(
            sequence_id=sequence_id,
            output_path=str(output),
            format=ExportFormat.H264,
            preset=_preset(subtitle_track_id),
            overwrite=False,
        ),
        sequence_id=sequence_id,
        idempotency_key=f"interchange-export-{uuid.uuid4().hex}",
    )
    completed = _completed(project, task)
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(f"Export did not create a usable file: {output}")
    return completed


def _history(project_dir: Path) -> dict[str, Any]:
    from mediaflow.service.client import call_sync

    value = call_sync("history.list", {"project": str(project_dir)})
    if not isinstance(value, dict):
        raise RuntimeError("Editor Service returned an invalid history document")
    return value


def _probe_video(path: Path) -> dict[str, Any]:
    from mediaflow.infrastructure.runtime_context import RuntimeContext

    ffprobe = RuntimeContext.discover().paths.ffprobe
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    value = json.loads(completed.stdout)
    streams = value.get("streams") or []
    if not any(item.get("codec_type") == "video" for item in streams):
        raise RuntimeError(f"Export contains no video stream: {path}")
    if float(value.get("format", {}).get("duration") or 0) <= 0:
        raise RuntimeError(f"Export has no positive duration: {path}")
    return value


def _compare(reference: Path, candidate: Path, output_dir: Path) -> dict[str, Any]:
    from mediaflow.service.client import execute_sync

    request = {
        "protocol": "mediaflow-editor",
        "version": 3,
        "operation": "quality.reference.compare",
        "arguments": {
            "reference_path": str(reference),
            "candidate_path": str(candidate),
            "output_dir": str(output_dir),
            "temporal_search_radius_frames": 1,
            "boundary_frame_count": 3,
            "contact_sheet_rows": 8,
            "acceptance": CROSS_PLATFORM_VISUAL_ACCEPTANCE,
        },
        "request_id": f"interchange-compare-{uuid.uuid4().hex}",
        "actor": {"kind": "agent", "id": "project-interchange"},
        "client_id": "project-interchange",
    }
    response = execute_sync(request)
    result = response.get("result")
    if not isinstance(result, dict) or result.get("status") != "passed":
        failures = result.get("acceptance_failures") if isinstance(result, dict) else result
        raise RuntimeError(f"Cross-platform reference comparison failed: {failures}")
    return result


def _project_strings(project_file: Path) -> list[str]:
    values: list[str] = []
    with closing(sqlite3.connect(f"file:{project_file.as_posix()}?mode=ro", uri=True)) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            for row in connection.execute(f"SELECT * FROM {quoted}"):
                values.extend(value for value in row if isinstance(value, str))
    return values


def _assert_no_machine_runtime_paths(project_file: Path, forbidden: list[Path]) -> None:
    normalized = "\n".join(_project_strings(project_file)).replace("\\", "/").casefold()
    leaks = [
        str(path.resolve()).replace("\\", "/").casefold()
        for path in forbidden
        if str(path.resolve()).replace("\\", "/").casefold() in normalized
    ]
    if leaks:
        raise RuntimeError(f"Project truth contains machine runtime/cache paths: {leaks}")


def _close_application(project: Any, application: Any) -> None:
    from mediaflow.service.client import call_sync

    try:
        project.close(timeout=30)
    finally:
        try:
            application.close_client_transport()
        finally:
            call_sync("service.shutdown", start_if_needed=False)


def produce(bundle: Path, work_root: Path, platform_key: str) -> dict[str, Any]:
    from mediaflow.domain.enums import TrackKind
    from mediaflow.infrastructure.runtime_context import RuntimeContext
    from mediaflow.service.desktop_proxy import DesktopEditorApplication

    target = RuntimeContext.discover().target.key
    if target != platform_key:
        raise RuntimeError(f"Producer target mismatch: expected {platform_key}, got {target}")
    bundle = bundle.expanduser().resolve()
    if bundle.exists() and any(bundle.iterdir()):
        raise FileExistsError(f"Interchange bundle must start empty: {bundle}")
    bundle.mkdir(parents=True, exist_ok=True)
    _configure_run_root(work_root)
    sources = bundle / "sources"
    video_source = sources / "interchange-source.mp4"
    subtitle_source = sources / "interchange-source.srt"
    _generate_source(video_source)
    subtitle_source.write_text(
        "1\n00:00:00,200 --> 00:00:01,800\nMEDIAFLOW 跨平台字幕\n",
        encoding="utf-8",
    )
    application = DesktopEditorApplication()
    project = application.create_project(
        bundle / "project",
        f"MediaFlow interchange {platform_key}",
    )
    try:
        project_record = project.get_project()
        sequence_id = project_record.main_sequence_id
        imported_video = _completed(
            project,
            project.import_asset(
                video_source,
                sequence_id=sequence_id,
                idempotency_key=f"interchange-video-{platform_key}",
            ),
        )
        if imported_video.outcome is None:
            raise RuntimeError("Video import produced no durable outcome")
        video_asset_id = imported_video.outcome.asset_id
        project.adopt_main_profile_from_video(video_asset_id)
        timeline = project.timeline(sequence_id)
        video_track = timeline.add_track(TrackKind.VIDEO, "Portable video")
        subtitle_track = timeline.add_track(TrackKind.SUBTITLE, "Bundled-font subtitles")
        asset = project.get_asset(video_asset_id)
        timeline.add_clip(
            track_id=video_track.id,
            asset_id=video_asset_id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        imported_subtitle = _completed(
            project,
            project.import_asset(
                subtitle_source,
                sequence_id=sequence_id,
                purpose="subtitle",
                language="zh",
                media_asset_id=video_asset_id,
                idempotency_key=f"interchange-subtitle-{platform_key}",
            ),
        )
        outcome = imported_subtitle.outcome
        if outcome is None or outcome.document_id is None:
            raise RuntimeError("Subtitle import produced no durable document outcome")
        project.place_subtitle_document(outcome.document_id, subtitle_track.id)
        reference = bundle / "reference.mp4"
        _export(project, sequence_id, subtitle_track.id, reference)
        # This intentionally remains the newest reversible edit. Consumers must
        # prove the foreign history can undo and redo it before making their edit.
        timeline.add_track(TrackKind.AUDIO, f"Producer history {platform_key}")
        history = _history(project.project_dir)
        if not history.get("can_undo"):
            raise RuntimeError("Produced project has no portable undo history")
        project_revision = project.content_revision()
        project_events = project.list_project_events(after_cursor=0)
        _probe_video(reference)
        _assert_no_machine_runtime_paths(
            project.project_dir / "project.mfp",
            [
                RuntimeContext.discover().paths.runtime_dir,
                work_root / "media",
                work_root / "tests",
                Path(os.environ["MEDIAFLOW_SERVICE_STATE_DIR"]),
            ],
        )
        manifest = {
            "schema": "mediaflow-project-interchange/v1",
            "producer": platform_key,
            "project": "project",
            "reference": "reference.mp4",
            "sources": {
                "interchange-source.mp4": "sources/interchange-source.mp4",
                "interchange-source.srt": "sources/interchange-source.srt",
            },
            "project_id": project_record.id,
            "sequence_id": sequence_id,
            "subtitle_track_id": subtitle_track.id,
            "project_revision": project_revision,
            "project_event_count": len(project_events),
            "undo_item_count": len(history.get("items") or []),
            "font_family": "LXGW WenKai",
            "runtime_contract_digest": RuntimeContext.discover().contract_digest,
        }
        (bundle / "interchange.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest
    finally:
        _close_application(project, application)


def consume(
    bundle: Path,
    work_root: Path,
    producer_key: str,
    consumer_key: str,
) -> dict[str, Any]:
    from mediaflow.domain.enums import TrackKind
    from mediaflow.infrastructure.runtime_context import RuntimeContext
    from mediaflow.service.desktop_proxy import DesktopEditorApplication

    target = RuntimeContext.discover().target.key
    if target != consumer_key:
        raise RuntimeError(f"Consumer target mismatch: expected {consumer_key}, got {target}")
    bundle = bundle.expanduser().resolve(strict=True)
    manifest = json.loads((bundle / "interchange.json").read_text(encoding="utf-8"))
    if manifest.get("producer") != producer_key:
        raise RuntimeError(
            f"Bundle producer mismatch: expected {producer_key}, got {manifest.get('producer')}"
        )
    _configure_run_root(work_root)
    project_dir = bundle / str(manifest["project"])
    application = DesktopEditorApplication()
    project = application.open_project(project_dir)
    try:
        record = project.get_project()
        if record.id != manifest["project_id"]:
            raise RuntimeError("Foreign project identity changed while opening")
        if project.content_revision() < int(manifest["project_revision"]):
            raise RuntimeError("Foreign project revision regressed while opening")
        events_before = project.list_project_events(after_cursor=0)
        if len(events_before) < int(manifest["project_event_count"]):
            raise RuntimeError("Foreign project events were lost while opening")
        history_before = _history(project_dir)
        if not history_before.get("can_undo"):
            raise RuntimeError("Foreign undo history was not restored")
        sequence_id = str(manifest["sequence_id"])
        timeline = project.timeline(sequence_id)
        producer_track = f"Producer history {producer_key}"
        project.undo()
        if producer_track in {track.name for track in timeline.reload().tracks}:
            raise RuntimeError("Foreign undo history did not remove the producer edit")
        project.redo()
        if producer_track not in {track.name for track in timeline.reload().tracks}:
            raise RuntimeError("Foreign redo history did not restore the producer edit")
        source_map = {
            name: bundle / relative
            for name, relative in dict(manifest["sources"]).items()
        }
        for asset in project.list_assets():
            replacement = source_map.get(asset.name)
            if replacement is None:
                raise RuntimeError(f"Bundle has no source mapping for asset {asset.name}")
            project.relink_asset(asset.id, replacement)
        timeline.add_track(TrackKind.AUDIO, f"Consumer edit {consumer_key}")
        candidate = work_root / "exports" / f"{producer_key}-to-{consumer_key}.mp4"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        _export(
            project,
            sequence_id,
            str(manifest["subtitle_track_id"]),
            candidate,
        )
        probe = _probe_video(candidate)
        comparison = _compare(
            bundle / str(manifest["reference"]),
            candidate,
            work_root / "comparison",
        )
        events_after = project.list_project_events(after_cursor=0)
        history_after = _history(project_dir)
        if len(events_after) <= len(events_before):
            raise RuntimeError("Consumer edit did not create a durable project event")
        if len(history_after.get("items") or []) <= len(history_before.get("items") or []):
            raise RuntimeError("Consumer edit did not extend durable undo history")
        _assert_no_machine_runtime_paths(
            project_dir / "project.mfp",
            [
                RuntimeContext.discover().paths.runtime_dir,
                work_root / "media",
                work_root / "tests",
                Path(os.environ["MEDIAFLOW_SERVICE_STATE_DIR"]),
            ],
        )
        report = {
            "schema": "mediaflow-project-interchange-result/v1",
            "status": "passed",
            "producer": producer_key,
            "consumer": consumer_key,
            "project_id": record.id,
            "project_revision": project.content_revision(),
            "project_event_count": len(events_after),
            "undo_item_count": len(history_after.get("items") or []),
            "candidate": str(candidate),
            "candidate_bytes": candidate.stat().st_size,
            "video_stream_count": len(
                [item for item in probe["streams"] if item.get("codec_type") == "video"]
            ),
            "comparison": comparison,
        }
        report_path = work_root / "interchange-result.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["report"] = str(report_path)
        return report
    finally:
        _close_application(project, application)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    producer = subparsers.add_parser("produce")
    producer.add_argument("--bundle", type=Path, required=True)
    producer.add_argument("--platform", required=True)
    producer.add_argument("--work-root", type=Path)
    consumer = subparsers.add_parser("consume")
    consumer.add_argument("--bundle", type=Path, required=True)
    consumer.add_argument("--producer", required=True)
    consumer.add_argument("--consumer", required=True)
    consumer.add_argument("--work-root", type=Path)
    arguments = parser.parse_args(argv)
    suffix = f"{arguments.command}-{uuid.uuid4().hex[:8]}"
    work_root = (arguments.work_root or test_run_root() / "project-interchange" / suffix).resolve()
    if arguments.command == "produce":
        report = produce(arguments.bundle, work_root, arguments.platform)
    else:
        report = consume(
            arguments.bundle,
            work_root,
            arguments.producer,
            arguments.consumer,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
