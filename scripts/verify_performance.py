from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from shiboken6 import getCppPointer, wrapInstance

from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.project import MediaMetadata
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.timeline import Clip
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths

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
        asset = repository.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.update_asset(
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
        project = repository.get_project()
        state = repository.load_timeline(project.main_sequence_id)
        video_track = next(track for track in state.tracks if track.kind == TrackKind.VIDEO)
        subtitle_track = next(track for track in state.tracks if track.kind == TrackKind.SUBTITLE)
        state.clips = [
            Clip(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=index * 10,
                source_in=index * 10,
                duration=10,
            )
            for index in range(CLIP_COUNT)
        ]
        repository.save_timeline(state)
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
        repository.create_subtitle_document(document, segments)
        placements = repository.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=True,
        )
        if len(placements) != SUBTITLE_COUNT:
            raise RuntimeError(f"Expected {SUBTITLE_COUNT} placements, got {len(placements)}")
    return project_dir


def verify(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    project_dir = create_fixture(root)
    os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(root / "runtime")
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controller = create_engine(app)
    try:
        started = time.perf_counter()
        controller.openProject(QUrl.fromLocalFile(str(project_dir)).toString())
        for _ in range(12):
            QCoreApplication.processEvents()
        open_seconds = time.perf_counter() - started
        if not controller.hasProject:
            raise RuntimeError("The large project did not open")
        if controller.clipsModel.rowCount() != CLIP_COUNT:
            raise RuntimeError(f"Only {controller.clipsModel.rowCount()} clips reached the QML model")
        if controller.subtitleTextAtFrame(SUBTITLE_COUNT - 1) != f"字幕 {SUBTITLE_COUNT}":
            raise RuntimeError("The final subtitle did not reach the preview consumer")

        last_clip_id = controller.clipsModel.get(CLIP_COUNT - 1)["clipId"]
        started = time.perf_counter()
        controller.moveClip(last_clip_id, SUBTITLE_COUNT, "")
        edit_seconds = time.perf_counter() - started
        persisted = controller.clipsModel.get(CLIP_COUNT - 1)
        if persisted["startFrame"] != SUBTITLE_COUNT:
            raise RuntimeError("The measured edit was not persisted and reflected in the model")

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
            "open_passed": open_seconds < OPEN_LIMIT_SECONDS,
            "edit_passed": edit_seconds < EDIT_LIMIT_SECONDS,
            "screenshot": str(screenshot),
            "project": str(project_dir),
        }
        report_path = root / "performance-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not report["open_passed"] or not report["edit_passed"]:
            raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
        return {**report, "report": str(report_path)}
    finally:
        controller.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def main() -> None:
    root = Path("D:/Tools/MediaFlow/test-runs") / ("performance-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    print(json.dumps(verify(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
