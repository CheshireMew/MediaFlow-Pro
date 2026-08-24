# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from shiboken6 import getCppPointer, wrapInstance

from mediaflow.atomic_file import atomic_write_text
from mediaflow.desktop.app import (
    configure_application_font,
    configure_application_identity,
    create_engine,
)
from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind
from mediaflow.domain.project import MediaMetadata
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.timeline import Clip, Track
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.service.client import shutdown_sync_service
from scripts.run_artifacts import verification_run, verification_workspace_root

CLIP_COUNT = 5_000
SUBTITLE_COUNT = 5_000
OPEN_LIMIT_SECONDS = 6.0
VISIBLE_LIMIT_SECONDS = 8.0
EDIT_LIMIT_SECONDS = 0.08
STRUCTURAL_EDIT_LIMIT_SECONDS = 0.5
SUBTITLE_EDIT_LIMIT_SECONDS = 0.75
INTERACTIVE_CLIP_LIMIT = 640
MEMORY_DELTA_LIMIT_BYTES = 512 * 1024 * 1024
MAX_PERFORMANCE_ATTEMPTS = 2


def create_fixture(root: Path) -> Path:
    project_dir = root / "Large Project"
    source = root / "fixture.mp4"
    paths = RuntimeContext.discover().paths
    generated = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=#203040:s=160x90:r=30:d=167",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-g",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if generated.returncode != 0:
        raise RuntimeError(generated.stderr.decode(errors="replace"))
    with ProjectRepository.create(project_dir, "Large Project") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": MediaMetadata(
                        duration_frames=SUBTITLE_COUNT,
                        width=1920,
                        height=1080,
                        fps_numerator=30,
                        fps_denominator=1,
                        has_video=True,
                    )
                }
            )
        )
        project = repository.projects.get_project()
        state = repository.timeline.load_timeline(project.main_sequence_id)
        video_track = Track(
            sequence_id=project.main_sequence_id,
            name="Video 1",
            kind=TrackKind.VIDEO,
            position=0,
        )
        subtitle_track = Track(
            sequence_id=project.main_sequence_id,
            name="Subtitles 1",
            kind=TrackKind.SUBTITLE,
            position=1,
        )
        state.tracks = [video_track, subtitle_track]
        state.clips = [
            Clip(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=index * 10,
                source_in=0,
                duration=10,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            )
            for index in range(CLIP_COUNT)
        ]
        repository.timeline.save_timeline(state)
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="zh-CN",
        )
        segments = [
            SubtitleSegment(
                document_id=document.id,
                start_frame=index,
                end_frame=index + 1,
                text=f"字幕 {index + 1}",
            )
            for index in range(SUBTITLE_COUNT)
        ]
        repository.subtitles.create_subtitle_document(document, segments)
        placements = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )
        if len(placements) != SUBTITLE_COUNT:
            raise RuntimeError(f"Expected {SUBTITLE_COUNT} placements, got {len(placements)}")
    return project_dir


def _performance_passed(report: dict[str, object]) -> bool:
    return all(
        report[key] is True
        for key in (
            "open_passed",
            "visible_passed",
            "edit_passed",
            "structural_edits_passed",
            "subtitle_edit_passed",
            "memory_passed",
        )
    )


def verify(
    root: Path,
    *,
    evidence_root: Path | None = None,
    enforce_limits: bool = True,
) -> dict:
    evidence = evidence_root or root
    os.environ["MEDIAFLOW_SERVICE_STATE_DIR"] = str(root / "editor-service")
    project_dir = create_fixture(root)
    configure_application_identity()
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        process = psutil.Process()
        memory_before_open = process.memory_info().rss
        started = time.perf_counter()
        controllers.workspace_project.openProject(QUrl.fromLocalFile(str(project_dir)).toString())
        open_seconds = time.perf_counter() - started
        for _ in range(12):
            QCoreApplication.processEvents()
        visible_seconds = time.perf_counter() - started
        memory_after_open = process.memory_info().rss
        memory_delta_bytes = max(0, memory_after_open - memory_before_open)
        if not controllers.workspace.hasProject:
            raise RuntimeError("The large project did not open")
        if controllers.timeline_view.clipsModel.rowCount() != CLIP_COUNT:
            raise RuntimeError(
                f"Only {controllers.timeline_view.clipsModel.rowCount()} clips reached the QML model"
            )
        if controllers.subtitle_view.subtitleTextAtFrame(SUBTITLE_COUNT - 1) != f"字幕 {SUBTITLE_COUNT}":
            raise RuntimeError("The final subtitle did not reach the preview consumer")
        interactive_clip_count = len(
            engine.rootObjects()[0].findChildren(QObject, "timelineClip")
        )
        if interactive_clip_count > INTERACTIVE_CLIP_LIMIT:
            raise RuntimeError(
                f"Timeline instantiated {interactive_clip_count} interactive clip delegates"
            )

        last_clip_id = controllers.timeline_view.clipsModel.get(CLIP_COUNT - 1)["clipId"]
        snap_probe_started = time.perf_counter()
        controllers.session._timeline_snapping.snap(
            CLIP_COUNT * 10,
            controllers.session._snap_tolerance_frames(3.0),
            [last_clip_id],
            0,
        )
        snap_probe_seconds = time.perf_counter() - snap_probe_started
        snap_target_count = controllers.session._timeline_snapping.target_count
        started = time.perf_counter()
        moved_start_frame = CLIP_COUNT * 10
        controllers.timeline_clips.moveClip(last_clip_id, moved_start_frame, "")
        edit_seconds = time.perf_counter() - started
        projected = controllers.timeline_view.clipsModel.get(CLIP_COUNT - 1)
        if projected["startFrame"] != moved_start_frame:
            raise RuntimeError("The measured edit did not reach the QML model")
        sequence_id = controllers.workspace.activeSequenceId
        with ProjectRepository.open(
            project_dir,
            writable=False,
        ) as persisted_repository:
            persisted_state = persisted_repository.timeline.load_timeline(sequence_id)
        persisted_clip = next(
            (clip for clip in persisted_state.clips if clip.id == last_clip_id),
            None,
        )
        if persisted_clip is None or persisted_clip.timeline_start != moved_start_frame:
            raise RuntimeError("The measured edit reached the model but not persistent storage")

        started = time.perf_counter()
        controllers.timeline_clips.duplicateClip(last_clip_id, 3.0, 0)
        duplicate_seconds = time.perf_counter() - started
        copied_id = controllers.session.state.selection.clip_ids[0]
        copied_row = controllers.timeline_view.clipsModel.get(
            controllers.timeline_view.clipsModel.findRow("clipId", copied_id)
        )
        if not copied_row or controllers.timeline_view.clipsModel.rowCount() != CLIP_COUNT + 1:
            raise RuntimeError("The measured duplicate did not reach the QML model")

        started = time.perf_counter()
        controllers.timeline_clips.splitClip(
            copied_id,
            int(copied_row["startFrame"]) + 5,
        )
        split_seconds = time.perf_counter() - started
        if controllers.timeline_view.clipsModel.rowCount() != CLIP_COUNT + 2:
            raise RuntimeError("The measured split did not reach the QML model")

        started = time.perf_counter()
        controllers.timeline_clips.deleteSelectedClips(False)
        delete_seconds = time.perf_counter() - started
        if controllers.timeline_view.clipsModel.rowCount() != CLIP_COUNT + 1:
            raise RuntimeError("The measured delete did not reach the QML model")

        document_id = controllers.subtitle_view.subtitleDocumentsModel.get(0)["documentId"]
        controllers.subtitle_view.selectSubtitleDocument(document_id)
        subtitle_row = controllers.subtitle_view.subtitleSegmentsModel.get(SUBTITLE_COUNT - 1)
        updated_subtitle_text = f"{subtitle_row['text']} / 已修改"
        started = time.perf_counter()
        controllers.subtitle_editing.updateSubtitleSegment(
            subtitle_row["segmentId"],
            subtitle_row["startFrame"],
            subtitle_row["endFrame"],
            updated_subtitle_text,
        )
        subtitle_edit_seconds = time.perf_counter() - started
        projected_subtitle = controllers.subtitle_view.subtitleSegmentsModel.get(
            controllers.subtitle_view.subtitleSegmentsModel.findRow(
                "segmentId",
                subtitle_row["segmentId"],
            )
        )
        if projected_subtitle.get("text") != updated_subtitle_text:
            raise RuntimeError("The measured subtitle edit did not reach the QML model")

        with ProjectRepository.open(project_dir, writable=False) as persisted_repository:
            persisted_after_edits = persisted_repository.timeline.load_timeline(sequence_id)
            persisted_subtitle = persisted_repository.subtitles.get_subtitle_segment(
                document_id,
                subtitle_row["segmentId"],
            )
        if len(persisted_after_edits.clips) != CLIP_COUNT + 1:
            raise RuntimeError("The measured structural edits did not reach persistent storage")
        if persisted_subtitle.text != updated_subtitle_text:
            raise RuntimeError("The measured subtitle edit did not reach persistent storage")

        window = engine.rootObjects()[0]
        quick_window = wrapInstance(getCppPointer(window)[0], QQuickWindow)
        screenshot = evidence / "large-project.png"
        if not quick_window.grabWindow().save(str(screenshot)):
            raise RuntimeError("The large-project workspace did not render")
        report = {
            "clip_count": CLIP_COUNT,
            "subtitle_count": SUBTITLE_COUNT,
            "open_seconds": open_seconds,
            "open_limit_seconds": OPEN_LIMIT_SECONDS,
            "visible_seconds": visible_seconds,
            "visible_limit_seconds": VISIBLE_LIMIT_SECONDS,
            "edit_seconds": edit_seconds,
            "edit_limit_seconds": EDIT_LIMIT_SECONDS,
            "duplicate_seconds": duplicate_seconds,
            "split_seconds": split_seconds,
            "delete_seconds": delete_seconds,
            "structural_edit_limit_seconds": STRUCTURAL_EDIT_LIMIT_SECONDS,
            "subtitle_edit_seconds": subtitle_edit_seconds,
            "subtitle_edit_limit_seconds": SUBTITLE_EDIT_LIMIT_SECONDS,
            "snap_probe_seconds": snap_probe_seconds,
            "snap_target_count": snap_target_count,
            "interactive_clip_count": interactive_clip_count,
            "interactive_clip_limit": INTERACTIVE_CLIP_LIMIT,
            "memory_before_open_bytes": memory_before_open,
            "memory_after_open_bytes": memory_after_open,
            "memory_delta_bytes": memory_delta_bytes,
            "memory_delta_limit_bytes": MEMORY_DELTA_LIMIT_BYTES,
            "persisted_edit_start_frame": persisted_clip.timeline_start,
            "open_passed": open_seconds < OPEN_LIMIT_SECONDS,
            "visible_passed": visible_seconds < VISIBLE_LIMIT_SECONDS,
            "edit_passed": edit_seconds < EDIT_LIMIT_SECONDS,
            "structural_edits_passed": max(
                duplicate_seconds,
                split_seconds,
                delete_seconds,
            )
            < STRUCTURAL_EDIT_LIMIT_SECONDS,
            "subtitle_edit_passed": subtitle_edit_seconds < SUBTITLE_EDIT_LIMIT_SECONDS,
            "memory_passed": memory_delta_bytes < MEMORY_DELTA_LIMIT_BYTES,
            "screenshot": str(screenshot),
            "project": str(project_dir),
        }
        report_path = evidence / "performance-report.json"
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
        if enforce_limits and not _performance_passed(report):
            raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
        return {**report, "report": str(report_path)}
    finally:
        try:
            controllers.shutdown()
        finally:
            shutdown_sync_service()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    arguments = parser.parse_args(argv)
    with verification_run(
        "performance",
        explicit_root=arguments.root,
    ) as run_dir:
        attempts = []
        for attempt_number in range(1, MAX_PERFORMANCE_ATTEMPTS + 1):
            attempt_root = run_dir / f"attempt-{attempt_number}"
            attempt_root.mkdir()
            report = verify(
                verification_workspace_root(attempt_root),
                evidence_root=attempt_root,
                enforce_limits=False,
            )
            attempts.append(report)
            if _performance_passed(report):
                summary = {
                    "attempt_count": attempt_number,
                    "passed_attempt": attempt_number,
                    "attempts": attempts,
                }
                summary_path = run_dir / "performance-summary.json"
                atomic_write_text(
                    summary_path,
                    json.dumps(summary, ensure_ascii=False, indent=2),
                )
                print(
                    json.dumps(
                        {**summary, "summary": str(summary_path)},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
        summary = {
            "attempt_count": len(attempts),
            "passed_attempt": None,
            "attempts": attempts,
        }
        summary_path = run_dir / "performance-summary.json"
        atomic_write_text(
            summary_path,
            json.dumps(summary, ensure_ascii=False, indent=2),
        )
        raise RuntimeError(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
