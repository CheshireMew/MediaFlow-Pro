from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import (
    QAudioInput,
    QAudioOutput,
    QMediaCaptureSession,
    QMediaFormat,
    QMediaPlayer,
    QMediaRecorder,
)


class VoiceoverCapture(QObject):
    """Own the audio backend, created only when recording or calibrating."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.audio_input = QAudioInput(self)
        self.session = QMediaCaptureSession(self)
        self.recorder = QMediaRecorder(self)
        self.session.setAudioInput(self.audio_input)
        self.session.setRecorder(self.recorder)
        self.calibration_player = QMediaPlayer(self)
        self.calibration_output = QAudioOutput(self)
        self.calibration_player.setAudioOutput(self.calibration_output)
        self.calibration_output.setVolume(0.7)

    def configure(self, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        media_format = QMediaFormat()
        media_format.setFileFormat(QMediaFormat.FileFormat.Wave)
        media_format.setAudioCodec(QMediaFormat.AudioCodec.Wave)
        self.recorder.setMediaFormat(media_format)
        self.recorder.setAudioSampleRate(48_000)
        self.recorder.setAudioChannelCount(1)
        self.recorder.setQuality(QMediaRecorder.Quality.HighQuality)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(output)))
