from __future__ import annotations

import os
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaDevices, QMediaRecorder

from mediaflow.composition import EditorApplication
from mediaflow.desktop.controllers.controller_hub import EditorControllers
from mediaflow.domain.enums import TrackKind
from mediaflow.infrastructure.project_repository import ProjectRepository
from tests.v2.desktop_application_adapter import DesktopPresentationApplication


class _Input:
    def id(self) -> QByteArray:
        return QByteArray(b"controlled-input")

    def description(self) -> str:
        return "Controlled input"


def test_home_and_project_lifecycle_do_not_open_the_audio_capture_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mediaflow.desktop.controllers import timeline_voiceover_controller

    def unexpected_capture(_parent):
        raise AssertionError("Opening or closing a project must not initialize audio capture")

    monkeypatch.setattr(timeline_voiceover_controller, "VoiceoverCapture", unexpected_capture)
    app = QGuiApplication.instance() or QGuiApplication([])
    controllers = EditorControllers(application=DesktopPresentationApplication(EditorApplication()))
    try:
        voice = controllers.timeline_voiceover
        assert not voice.recording and not voice.calibrating
        assert voice._capture_runtime is None
        controllers.session.lifecycle.create_and_open(tmp_path, "No recording")
        voice.refresh()
        assert not voice.recording
        controllers.session.lifecycle.close(close_in_background=False)
        app.processEvents()
        assert voice._capture_runtime is None
    finally:
        controllers.shutdown()


@pytest.mark.parametrize("action", ["close", "replace", "shutdown", "calibration"])
def test_recording_callbacks_are_drained_before_project_unbind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    controllers = EditorControllers(application=DesktopPresentationApplication(EditorApplication()))
    lifecycle = controllers.session.lifecycle
    lifecycle.create_and_open(tmp_path, "Original")
    original = controllers.session.state.binding.require_current()
    original_dir = original.project_dir
    sequence_id = original.get_project().main_sequence_id
    source = tmp_path / "original.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(b"\0\0" * 48_000)
    asset = original.import_external_asset(source)
    timeline = original.timeline(sequence_id)
    track = timeline.add_track(TrackKind.AUDIO, "Voice")
    timeline.add_clip(track_id=track.id, asset_id=asset.id, timeline_start=0, source_in=0, duration=25)
    cue = original.create_voiceover_cue(sequence_id, start_frame=0, end_frame=25, text="Voice")
    voice = controllers.timeline_voiceover
    assert voice._capture_runtime is None
    capture = voice._capture
    assert voice._capture is capture
    state = QMediaRecorder.RecorderState.StoppedState
    stops = []

    def record() -> None:
        nonlocal state
        source = Path(capture.recorder.outputLocation().toLocalFile())
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48_000)
            output.writeframes(b"\0\0" * 48_000)
        state = QMediaRecorder.RecorderState.RecordingState
        capture.recorder.recorderStateChanged.emit(state)

    def stop() -> None:
        def finished() -> None:
            nonlocal state
            stops.append(controllers.session.state.binding.current)
            state = QMediaRecorder.RecorderState.StoppedState
            capture.recorder.recorderStateChanged.emit(state)

        QTimer.singleShot(20, finished)

    monkeypatch.setattr(QMediaDevices, "audioInputs", lambda: [_Input()])
    monkeypatch.setattr(capture.audio_input, "setDevice", lambda _device: None)
    monkeypatch.setattr(capture.recorder, "record", record)
    monkeypatch.setattr(capture.recorder, "stop", stop)
    monkeypatch.setattr(capture.recorder, "recorderState", lambda: state)
    monkeypatch.setattr(capture.recorder, "actualLocation", capture.recorder.outputLocation)
    monkeypatch.setattr(capture.calibration_player, "play", lambda: None)
    try:
        if action == "calibration":
            voice.startLatencyCalibration(voice._device_id(_Input()), "Controlled input")
            assert voice.calibrating
        else:
            voice.startRecording(cue.id, voice._device_id(_Input()))
            assert voice.recordingCueId == cue.id
        if action == "replace":
            lifecycle.create_and_open(tmp_path, "Replacement")
            current = controllers.session.state.binding.require_current()
            assert current.list_voiceover_cues(current.get_project().main_sequence_id) == []
        elif action == "shutdown":
            controllers.shutdown()
        else:
            lifecycle.close(close_in_background=False)
        assert stops == [original]
        assert not voice.recordingCueId and not voice.calibrating
        # A duplicate/late multimedia notification cannot write through a new binding.
        capture.recorder.recorderStateChanged.emit(QMediaRecorder.RecorderState.StoppedState)
        app.processEvents()
    finally:
        controllers.shutdown()
    with ProjectRepository.open(original_dir) as repository:
        saved = repository.voiceover.list_cues(sequence_id)[0]
        assert saved.status != "recording"
        assert len(saved.takes) == (0 if action == "calibration" else 1)
