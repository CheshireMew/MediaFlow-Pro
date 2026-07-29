# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import TrackKind
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from scripts.run_artifacts import verification_run

FIXTURE_ROOT = Path("D:/Tools/MediaFlow/test-fixtures")
PREVIEW_FPS = 30
OPEN_LIMIT_SECONDS = 0.8
STARTUP_LIMIT_SECONDS = 1.0
POSITION_TOLERANCE_FRAMES = 3


def preview_requirements_met(
    *,
    open_seconds: float,
    startup_seconds: float,
    first_window_advanced_frames: int,
    first_window_expected_frames: int,
    first_window_dropped_frames: int,
    final_advanced_frames: int,
    final_expected_frames: int,
    final_dropped_frames: int,
) -> bool:
    """Evaluate every observed playback window, including the final position.

    The final progress check is intentionally independent of the first window:
    an implementation that plays normally for ten seconds and then freezes must
    fail even when its drop counter remains unchanged.
    """

    return (
        open_seconds <= OPEN_LIMIT_SECONDS
        and startup_seconds <= STARTUP_LIMIT_SECONDS
        and first_window_advanced_frames
        >= first_window_expected_frames - POSITION_TOLERANCE_FRAMES
        and first_window_dropped_frames == 0
        and final_advanced_frames
        >= final_expected_frames - POSITION_TOLERANCE_FRAMES
        and final_dropped_frames == 0
    )


def pump_until(predicate, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise TimeoutError("Preview condition was not reached before the deadline")


def ensure_fixture(path: Path, paths: RuntimePaths, duration_seconds: int) -> None:
    if path.is_file():
        metadata = MediaProbe(paths).probe(path).metadata
        if (
            metadata.width == 1920
            and metadata.height == 1080
            and metadata.duration_frames >= duration_seconds * 30
        ):
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(paths.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            str(duration_seconds),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "180",
            "-keyint_min",
            "180",
            "-sc_threshold",
            "0",
            "-c:a",
            "libopus",
            "-b:a",
            "128k",
            "-shortest",
            str(path),
        ],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--playback-check-seconds", type=int, default=10)
    arguments = parser.parse_args(argv)
    with verification_run("preview-performance") as run_dir:
        return verify(arguments, run_dir)


def verify(arguments: argparse.Namespace, run_dir: Path) -> int:
    if arguments.duration_seconds <= 0:
        raise ValueError("The playback duration must be positive")
    if arguments.playback_check_seconds <= 0:
        raise ValueError("The first playback check must be positive")
    if arguments.duration_seconds < arguments.playback_check_seconds:
        raise ValueError("The media duration must cover the playback check")

    paths = RuntimePaths.discover()
    if paths.melt is None or paths.native_qml is None:
        raise RuntimeError("MLT and the native QML plugin must be installed")
    media_seconds = arguments.duration_seconds + 5
    fixture = FIXTURE_ROOT / f"preview-motion-tone-long-gop-1080p30-{media_seconds}s.mkv"
    ensure_fixture(fixture, paths, media_seconds)

    with ProjectRepository.create(run_dir / "Preview Performance", "Preview Performance") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(fixture)
        editor = TimelineEditor(repository, repository.catalog.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=media_seconds * 30,
        )
        graph = repository.project_dir / "cache/mlt/preview-performance.mlt"
        TimelineCompiler(repository).write(editor.state, graph, native_preview=True)

        _app = QGuiApplication.instance() or QGuiApplication([])
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(paths.native_qml))
        engine.loadData(
            b"""
import QtQuick
import QtQuick.Controls
import MediaFlow.Native 1.0
ApplicationWindow {
    visible: true
    width: 960
    height: 540
    color: "black"
    MltPreviewItem { objectName: "preview"; anchors.fill: parent }
}
""",
            QUrl(),
        )
        if not engine.rootObjects():
            raise RuntimeError("The native preview verification window did not load")
        window = engine.rootObjects()[0]
        preview = window.findChild(QObject, "preview")
        if preview is None:
            raise RuntimeError("The native preview item was not created")
        preview.setProperty("runtimeRoot", str(paths.melt.parent))
        open_started = time.monotonic()
        preview.setProperty("source", str(graph))
        pump_until(lambda: int(preview.property("duration")) > 0, 10)
        open_seconds = time.monotonic() - open_started
        if preview.property("errorString"):
            raise RuntimeError(str(preview.property("errorString")))

        preview.play()
        startup_started = time.monotonic()
        pump_until(
            lambda: bool(preview.property("playing")) and int(preview.property("position")) > 0,
            3,
        )
        startup_seconds = time.monotonic() - startup_started
        playback_start_position = int(preview.property("position"))
        check_started = time.monotonic()
        pump_until(
            lambda: time.monotonic() - check_started >= arguments.playback_check_seconds,
            arguments.playback_check_seconds + 5,
        )
        first_window_dropped = int(preview.property("droppedFrames"))
        first_window_position = int(preview.property("position"))
        first_window_advanced = first_window_position - playback_start_position

        remaining = arguments.duration_seconds - arguments.playback_check_seconds
        if remaining > 0:
            pump_until(
                lambda: time.monotonic() - check_started >= arguments.duration_seconds,
                remaining + 10,
            )
        final_position = int(preview.property("position"))
        final_advanced = final_position - playback_start_position
        final_dropped = int(preview.property("droppedFrames"))
        preview.pause()

        first_window_expected = arguments.playback_check_seconds * PREVIEW_FPS
        final_expected = arguments.duration_seconds * PREVIEW_FPS
        report = {
            "fixture": str(fixture),
            "resolution": "1920x1080",
            "fps": "30/1",
            "source_video": "moving test pattern, H.264, 180-frame GOP",
            "source_audio": "440 Hz tone, Opus, 48 kHz",
            "duration_seconds": arguments.duration_seconds,
            "open_seconds": open_seconds,
            "open_limit_seconds": OPEN_LIMIT_SECONDS,
            "startup_seconds": startup_seconds,
            "startup_limit_seconds": STARTUP_LIMIT_SECONDS,
            "playback_start_position": playback_start_position,
            "first_window_seconds": arguments.playback_check_seconds,
            "first_window_position": first_window_position,
            "first_window_advanced_frames": first_window_advanced,
            "first_window_expected_frames": first_window_expected,
            "first_window_dropped_frames": first_window_dropped,
            "final_position": final_position,
            "final_position_advanced_frames": final_advanced,
            "final_position_expected_frames": final_expected,
            "final_dropped_frames": final_dropped,
        }
        report["passed"] = preview_requirements_met(
            open_seconds=open_seconds,
            startup_seconds=startup_seconds,
            first_window_advanced_frames=first_window_advanced,
            first_window_expected_frames=first_window_expected,
            first_window_dropped_frames=first_window_dropped,
            final_advanced_frames=final_advanced,
            final_expected_frames=final_expected,
            final_dropped_frames=final_dropped,
        )
        report_path = run_dir / "preview-performance-report.json"
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
        print(report_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))

        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()
        if not report["passed"]:
            raise RuntimeError("Native preview performance requirements were not met")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
