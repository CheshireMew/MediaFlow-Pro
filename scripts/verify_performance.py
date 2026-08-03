# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
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
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from scripts.run_artifacts import verification_run

CLIP_COUNT = 500
SUBTITLE_COUNT = 5_000
OPEN_LIMIT_SECONDS = 3.0
EDIT_LIMIT_SECONDS = 0.1


def create_fixture(root: Path) -> Path:
    project_dir = root / "Large Project"
    source = root / "fixture.mp4"
    paths = RuntimePaths.discover()
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
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.catalog.update_asset(
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
        project = repository.catalog.get_project()
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
                source_in=index * 10,
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
            follow_clips=True,
        )
        if len(placements) != SUBTITLE_COUNT:
            raise RuntimeError(f"Expected {SUBTITLE_COUNT} placements, got {len(placements)}")
    return project_dir


def verify(root: Path) -> dict:
    project_dir = create_fixture(root)
    configure_application_identity()
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        started = time.perf_counter()
        controllers.workspace.openProject(QUrl.fromLocalFile(str(project_dir)).toString())
        for _ in range(12):
            QCoreApplication.processEvents()
        open_seconds = time.perf_counter() - started
        if not controllers.workspace.hasProject:
            raise RuntimeError("The large project did not open")
        if controllers.timeline.clipsModel.rowCount() != CLIP_COUNT:
            raise RuntimeError(
                f"Only {controllers.timeline.clipsModel.rowCount()} clips reached the QML model"
            )
        if (
            controllers.subtitles.subtitleTextAtFrame(SUBTITLE_COUNT - 1)
            != f"字幕 {SUBTITLE_COUNT}"
        ):
            raise RuntimeError("The final subtitle did not reach the preview consumer")

        last_clip_id = controllers.timeline.clipsModel.get(CLIP_COUNT - 1)["clipId"]
        started = time.perf_counter()
        controllers.timeline.moveClip(last_clip_id, SUBTITLE_COUNT, "")
        edit_seconds = time.perf_counter() - started
        projected = controllers.timeline.clipsModel.get(CLIP_COUNT - 1)
        if projected["startFrame"] != SUBTITLE_COUNT:
            raise RuntimeError("The measured edit did not reach the QML model")
        sequence_id = controllers.workspace.activeSequenceId
        with ProjectRepository.open(
            project_dir,
            writable=False,
        ) as persisted_repository:
            persisted_state = persisted_repository.timeline.load_timeline(
                sequence_id
            )
        persisted_clip = next(
            (
                clip
                for clip in persisted_state.clips
                if clip.id == last_clip_id
            ),
            None,
        )
        if (
            persisted_clip is None
            or persisted_clip.timeline_start != SUBTITLE_COUNT
        ):
            raise RuntimeError(
                "The measured edit reached the model but not persistent storage"
            )

        window = engine.rootObjects()[0]
        quick_window = wrapInstance(getCppPointer(window)[0], QQuickWindow)
        screenshot = root / "large-project.png"
        if not quick_window.grabWindow().save(str(screenshot)):
            raise RuntimeError("The large-project workspace did not render")
        report = {
            "clip_count": CLIP_COUNT,
            "subtitle_count": SUBTITLE_COUNT,
            "open_seconds": open_seconds,
            "open_limit_seconds": OPEN_LIMIT_SECONDS,
            "edit_seconds": edit_seconds,
            "edit_limit_seconds": EDIT_LIMIT_SECONDS,
            "persisted_edit_start_frame": persisted_clip.timeline_start,
            "open_passed": open_seconds < OPEN_LIMIT_SECONDS,
            "edit_passed": edit_seconds < EDIT_LIMIT_SECONDS,
            "screenshot": str(screenshot),
            "project": str(project_dir),
        }
        report_path = root / "performance-report.json"
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
        if not report["open_passed"] or not report["edit_passed"]:
            raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
        return {**report, "report": str(report_path)}
    finally:
        controllers.shutdown()
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
        print(json.dumps(verify(run_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
