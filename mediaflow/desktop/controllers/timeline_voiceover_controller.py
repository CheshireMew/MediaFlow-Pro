from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Property, QCoreApplication, QEventLoop, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import (
    QMediaDevices,
    QMediaRecorder,
)

from mediaflow.application.voiceover_latency import (
    CALIBRATION_MARKER_OFFSET_SAMPLES,
    write_calibration_signal,
)
from mediaflow.desktop.voiceover_capture import VoiceoverCapture
from mediaflow.domain.voiceover import VoiceoverCue, VoiceoverCueStatus

from .controller_facet import ControllerFacet, report_ui_errors
from .timeline_controller_scopes import TimelineVoiceoverScope

if TYPE_CHECKING:
    from mediaflow.desktop.session_state import DesktopProject


class TimelineVoiceoverController(ControllerFacet[TimelineVoiceoverScope]):
    projectStateChanged = Signal()
    recordingCompleted = Signal(str)

    def __init__(self, scope: TimelineVoiceoverScope) -> None:
        super().__init__(scope)
        self._media_devices = QMediaDevices(self)
        self._capture_runtime: VoiceoverCapture | None = None
        self._auto_stop = QTimer(self)
        self._auto_stop.setSingleShot(True)
        self._auto_stop.timeout.connect(self.stopRecording)
        self._calibration_stop = QTimer(self)
        self._calibration_stop.setSingleShot(True)
        self._calibration_stop.timeout.connect(self._stop_capture)
        self._recording_cue_id = ""
        self._recording_path: Path | None = None
        self._recording_duration_ms = 0
        self._recording_device_id = ""
        self._status_before_recording: VoiceoverCueStatus = "planned"
        self._finalizing = False
        self._calibrating = False
        self._discard_calibration = False
        self._calibration_device_id = ""
        self._calibration_device_name = ""
        self._calibration_recording_path: Path | None = None
        self._capture_project: DesktopProject | None = None
        self._capture_generation = -1
        self._media_devices.audioInputsChanged.connect(self.projectStateChanged.emit)

    @property
    def _capture(self) -> VoiceoverCapture:
        if self._capture_runtime is None:
            capture = VoiceoverCapture(self)
            capture.recorder.durationChanged.connect(self._on_duration_changed)
            capture.recorder.recorderStateChanged.connect(self._on_recorder_state_changed)
            capture.recorder.errorOccurred.connect(self._on_recorder_error)
            self._capture_runtime = capture
        return self._capture_runtime

    def _stop_capture(self) -> None:
        if self._capture_runtime is not None:
            self._capture_runtime.recorder.stop()

    @Property(list, notify=projectStateChanged)
    def cues(self) -> list[dict[str, Any]]:
        current = self._session.project_binding.current
        sequence_id = self._session.project_binding.active_sequence_id
        if current is None or not sequence_id:
            return []
        return [self._cue_row(item) for item in current.list_voiceover_cues(sequence_id)]

    @Property(list, notify=projectStateChanged)
    def audioInputs(self) -> list[dict[str, Any]]:
        current = self._session.project_binding.current
        rows: list[dict[str, Any]] = []
        for device in QMediaDevices.audioInputs():
            device_id = bytes(device.id().toHex().data()).decode("ascii")
            calibration = (
                current.get_voiceover_latency_calibration(device_id) if current is not None else None
            )
            rows.append(
                {
                    "deviceId": device_id,
                    "name": device.description(),
                    "calibrated": calibration is not None,
                    "latencySamples": (calibration.latency_samples if calibration is not None else 0),
                    "latencyMs": (
                        round(
                            calibration.latency_samples * 1000 / calibration.sample_rate,
                            2,
                        )
                        if calibration is not None
                        else 0.0
                    ),
                    "confidence": calibration.confidence if calibration is not None else 0.0,
                    "method": calibration.method if calibration is not None else "",
                }
            )
        return rows

    @Property(bool, notify=projectStateChanged)
    def recording(self) -> bool:
        return (
            self._capture_runtime is not None
            and self._capture_runtime.recorder.recorderState() == QMediaRecorder.RecorderState.RecordingState
        )

    @Property(str, notify=projectStateChanged)
    def recordingCueId(self) -> str:
        return self._recording_cue_id

    @Property(int, notify=projectStateChanged)
    def recordingDurationMs(self) -> int:
        return self._recording_duration_ms

    @Property(bool, notify=projectStateChanged)
    def calibrating(self) -> bool:
        return self._calibrating

    @Slot()
    def refresh(self) -> None:
        self.projectStateChanged.emit()

    @Slot(int, int, str, str, str)
    @report_ui_errors
    def createCue(
        self,
        start_frame: int,
        end_frame: int,
        text: str,
        speaker: str,
        notes: str,
    ) -> None:
        self._session._require_writable()
        current = self._session.project_binding.require_current()
        current.create_voiceover_cue(
            self._session.project_binding.active_sequence_id,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
            speaker=speaker,
            notes=notes,
        )
        self._after_change()
        self._session._set_status("旁白 / ADR 提示已创建")

    @Slot(str, int, int, int, str, str, str, str)
    @report_ui_errors
    def updateCue(
        self,
        cue_id: str,
        expected_revision: int,
        start_frame: int,
        end_frame: int,
        text: str,
        speaker: str,
        notes: str,
        status: str,
    ) -> None:
        self._session._require_writable()
        self._session.project_binding.require_current().update_voiceover_cue(
            cue_id,
            expected_revision=expected_revision,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
            speaker=speaker,
            notes=notes,
            status=cast(VoiceoverCueStatus, status),
        )
        self._after_change()
        self._session._set_status("旁白 / ADR 提示已更新")

    @Slot(str, int)
    @report_ui_errors
    def archiveCue(self, cue_id: str, expected_revision: int) -> None:
        self._session._require_writable()
        if self._recording_cue_id == cue_id:
            raise RuntimeError("请先停止当前录音")
        self._session.project_binding.require_current().archive_voiceover_cue(
            cue_id,
            expected_revision=expected_revision,
        )
        self._after_change()
        self._session._set_status("旁白 / ADR 提示已归档")

    @Slot(str, str)
    @report_ui_errors
    def importTake(self, cue_id: str, source_url: str) -> None:
        self._session._require_writable()
        source = self._local_path(source_url)
        self._session.project_binding.require_current().add_voiceover_take(cue_id, source)
        self._after_change(refresh_assets=True)
        self._session._set_status("旁白 take 已导入")

    @Slot(str, str, str, int)
    @report_ui_errors
    def updateTake(self, take_id: str, name: str, notes: str, rating: int) -> None:
        self._session._require_writable()
        self._session.project_binding.require_current().update_voiceover_take(
            take_id,
            name=name,
            notes=notes,
            rating=rating,
        )
        self._after_change()
        self._session._set_status("旁白 take 已更新")

    @Slot(str, str, int)
    @report_ui_errors
    def selectTake(self, cue_id: str, take_id: str, expected_revision: int) -> None:
        self._session._require_writable()
        self._session.project_binding.require_current().select_voiceover_take(
            cue_id,
            take_id,
            expected_revision=expected_revision,
        )
        self._after_change()
        self._session._set_status("已选择旁白 take")

    @Slot(str)
    @report_ui_errors
    def archiveTake(self, take_id: str) -> None:
        self._session._require_writable()
        self._session.project_binding.require_current().archive_voiceover_take(take_id)
        self._after_change()
        self._session._set_status("旁白 take 已归档")

    @Slot(str, int)
    @report_ui_errors
    def placeTake(self, cue_id: str, expected_revision: int) -> None:
        self._session._require_writable()
        self._session.project_binding.require_current().place_voiceover_take(
            cue_id,
            expected_revision=expected_revision,
        )
        self._session.refresh_timeline()
        self._session.preview.schedule()
        self._after_change()
        self._session._set_status("选中的旁白 take 已放入时间线")

    @Slot(str, str)
    @report_ui_errors
    def startRecording(self, cue_id: str, device_id: str) -> None:
        self._session._require_writable()
        if self._recording_cue_id or self._finalizing or self._calibrating:
            raise RuntimeError("已有录音正在进行或写入 take")
        devices = QMediaDevices.audioInputs()
        if not devices:
            raise RuntimeError("没有检测到录音输入设备")
        selected = next(
            (item for item in devices if bytes(item.id().toHex().data()).decode("ascii") == device_id),
            devices[0],
        )
        cue = self._cue(cue_id)
        self._capture.audio_input.setDevice(selected)
        output = (
            self._session.project_binding.require_current().project_dir
            / "cache"
            / "voiceover-capture"
            / cue.id
            / f"{uuid.uuid4().hex}.wav"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        self._capture.configure(output)
        self._status_before_recording = cue.status
        current = self._session.project_binding.require_current()
        current.update_voiceover_cue(
            cue.id,
            expected_revision=cue.revision,
            start_frame=cue.start_frame,
            end_frame=cue.end_frame,
            text=cue.text,
            speaker=cue.speaker,
            notes=cue.notes,
            status="recording",
        )
        self._recording_cue_id = cue.id
        self._capture_project = current
        self._capture_generation = self._session.project_binding.generation
        selected_device_id = self._device_id(selected)
        calibration = current.get_voiceover_latency_calibration(selected_device_id)
        self._recording_device_id = selected_device_id if calibration is not None else ""
        self._recording_path = output
        self._recording_duration_ms = 0
        self._capture.recorder.record()
        profile = self._session.project_binding.require_timeline().state.sequence.profile
        duration_ms = round(
            (cue.end_frame - cue.start_frame) * 1000 * profile.fps_denominator / profile.fps_numerator
        )
        self._auto_stop.start(max(1, duration_ms))
        self.projectStateChanged.emit()
        self._session.updates.commit(project=True, history=True)
        self._session._set_status("旁白录音已开始")

    @Slot(str, str)
    @report_ui_errors
    def startLatencyCalibration(self, device_id: str, device_name: str) -> None:
        self._session._require_writable()
        if self._recording_cue_id or self._finalizing or self._calibrating:
            raise RuntimeError("已有录音或延迟校准正在进行")
        devices = QMediaDevices.audioInputs()
        selected = next((item for item in devices if self._device_id(item) == device_id), None)
        if selected is None:
            raise RuntimeError("所选录音设备已不可用")
        root = self._session.project_binding.require_current().project_dir / "cache" / "voiceover-calibration"
        token = uuid.uuid4().hex
        recording = root / f"{token}-capture.wav"
        signal = write_calibration_signal(root / f"{token}-signal.wav")
        self._capture.audio_input.setDevice(selected)
        self._capture.configure(recording)
        self._capture.calibration_player.setSource(QUrl.fromLocalFile(str(signal)))
        self._calibration_device_id = device_id
        self._calibration_device_name = device_name or selected.description()
        self._calibration_recording_path = recording
        self._discard_calibration = False
        self._calibrating = True
        self._capture_project = self._session.project_binding.require_current()
        self._capture_generation = self._session.project_binding.generation
        self._capture.recorder.record()
        self.projectStateChanged.emit()
        self._session._set_status("延迟校准中：扬声器将播放一段短测试声")

    @Slot()
    def cancelLatencyCalibration(self) -> None:
        if not self._calibrating:
            return
        self._discard_calibration = True
        self._calibration_stop.stop()
        self._capture.calibration_player.stop()
        self._capture.recorder.stop()

    @Slot(str, str, float)
    @report_ui_errors
    def setLatencyCalibration(
        self,
        device_id: str,
        device_name: str,
        latency_ms: float,
    ) -> None:
        self._session._require_writable()
        if latency_ms < 0 or latency_ms > 1_000:
            raise ValueError("录音延迟必须在 0 到 1000 毫秒之间")
        self._session.project_binding.require_current().set_voiceover_latency_calibration(
            device_id=device_id,
            device_name=device_name,
            latency_samples=round(latency_ms * 48_000 / 1000),
            sample_rate=48_000,
        )
        self._after_change()
        self._session._set_status("录音延迟已设为 %1 毫秒", f"{latency_ms:.1f}")

    @Slot()
    def stopRecording(self) -> None:
        if not self._recording_cue_id:
            return
        self._auto_stop.stop()
        self._capture.recorder.stop()

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._recording_duration_ms = max(0, int(duration_ms))
        self.projectStateChanged.emit()

    def _on_recorder_state_changed(self, state: QMediaRecorder.RecorderState) -> None:
        self.projectStateChanged.emit()
        if self._calibrating:
            if state == QMediaRecorder.RecorderState.RecordingState:
                self._capture.calibration_player.play()
                self._calibration_stop.start(2_400)
            elif state == QMediaRecorder.RecorderState.StoppedState:
                self._finalize_latency_calibration()
            return
        if state == QMediaRecorder.RecorderState.StoppedState and self._recording_cue_id:
            self._finalize_recording()

    def _on_recorder_error(self, _error: QMediaRecorder.Error, message: str) -> None:
        if self._calibrating:
            self._calibration_stop.stop()
            self._capture.calibration_player.stop()
            self._clear_calibration()
            self._session.updates.report_error(message or self._capture.recorder.errorString())
            return
        if not self._recording_cue_id:
            return
        self._auto_stop.stop()
        self._restore_recording_status()
        self._clear_recording()
        self._session.updates.report_error(message or self._capture.recorder.errorString())

    def _finalize_recording(self) -> None:
        if self._finalizing:
            return
        self._finalizing = True
        self._auto_stop.stop()
        cue_id = self._recording_cue_id
        actual_url = self._capture.recorder.actualLocation()
        actual_path = Path(actual_url.toLocalFile()) if actual_url.isLocalFile() else None
        source = actual_path if actual_path and actual_path.exists() else self._recording_path
        try:
            if source is None or not source.is_file() or source.stat().st_size <= 0:
                raise RuntimeError("录音没有生成可用的音频文件")
            self._capture_owner().add_voiceover_take(
                cue_id,
                source,
                calibration_device_id=(self._recording_device_id or None),
            )
            self._after_change(refresh_assets=True)
            self._session._set_status("旁白录音已保存为新 take")
            self.recordingCompleted.emit(cue_id)
        except Exception as error:
            self._restore_recording_status()
            self._session.updates.report_error(str(error))
        finally:
            self._clear_recording()

    def _restore_recording_status(self) -> None:
        try:
            current = self._capture_owner()
            cue = self._cue(self._recording_cue_id)
            current.update_voiceover_cue(
                cue.id,
                expected_revision=cue.revision,
                start_frame=cue.start_frame,
                end_frame=cue.end_frame,
                text=cue.text,
                speaker=cue.speaker,
                notes=cue.notes,
                status=self._status_before_recording,
            )
            self._session.updates.commit(project=True, history=True)
        except Exception:
            return

    def _clear_recording(self) -> None:
        self._recording_cue_id = ""
        self._recording_path = None
        self._recording_duration_ms = 0
        self._recording_device_id = ""
        self._finalizing = False
        self._capture_project = None
        self._capture_generation = -1
        self.projectStateChanged.emit()

    def _finalize_latency_calibration(self) -> None:
        self._calibration_stop.stop()
        self._capture.calibration_player.stop()
        if self._discard_calibration:
            self._clear_calibration()
            return
        actual_url = self._capture.recorder.actualLocation()
        actual_path = Path(actual_url.toLocalFile()) if actual_url.isLocalFile() else None
        source = (
            actual_path
            if actual_path is not None and actual_path.is_file()
            else self._calibration_recording_path
        )
        try:
            if source is None or not source.is_file() or source.stat().st_size <= 0:
                raise RuntimeError("延迟校准没有生成可用的录音")
            calibration = self._capture_owner().analyze_voiceover_latency_calibration(
                source,
                device_id=self._calibration_device_id,
                device_name=self._calibration_device_name,
                marker_offset_samples=CALIBRATION_MARKER_OFFSET_SAMPLES,
            )
            self._after_change()
            latency_ms = calibration.latency_samples * 1000 / calibration.sample_rate
            self._session._set_status(
                "延迟校准完成：%1 毫秒，可信度 %2%",
                f"{latency_ms:.1f}",
                round(calibration.confidence * 100),
            )
        except Exception as error:
            self._session.updates.report_error(str(error))
        finally:
            self._clear_calibration()

    def _clear_calibration(self) -> None:
        self._calibrating = False
        self._discard_calibration = False
        self._calibration_device_id = ""
        self._calibration_device_name = ""
        self._calibration_recording_path = None
        self._capture_project = None
        self._capture_generation = -1
        self.projectStateChanged.emit()

    def _capture_owner(self) -> DesktopProject:
        binding = self._session.project_binding
        if (
            self._capture_project is None
            or binding.current is not self._capture_project
            or binding.generation != self._capture_generation
        ):
            raise RuntimeError("录音所属项目已关闭；原始录音保留在原项目缓存中")
        return self._capture_project

    def prepare_project_unbind(self) -> None:
        """Drain recorder callbacks while their original project is still bound."""
        self._auto_stop.stop()
        self._calibration_stop.stop()
        if self._capture_runtime is None:
            return
        self._capture.calibration_player.stop()
        if self._calibrating:
            self._discard_calibration = True
        if self._recording_cue_id or self._calibrating:
            self._capture.recorder.stop()
            deadline = time.monotonic() + 5.0
            while self._capture.recorder.recorderState() != QMediaRecorder.RecorderState.StoppedState:
                if time.monotonic() >= deadline:
                    raise RuntimeError("录音尚未停止，项目保持打开，请稍后重试")
                QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 20)
            if self._calibrating:
                self._finalize_latency_calibration()
            elif self._recording_cue_id:
                self._finalize_recording()

    def _cue(self, cue_id: str) -> VoiceoverCue:
        current = self._session.project_binding.require_current()
        for cue in current.list_voiceover_cues(
            self._session.project_binding.active_sequence_id,
            include_archived=True,
        ):
            if cue.id == cue_id:
                return cue
        raise KeyError(cue_id)

    def _after_change(self, *, refresh_assets: bool = False) -> None:
        if refresh_assets:
            self._session.refresh_assets()
        self._session.updates.commit(project=True, history=True, selection=True)
        self.projectStateChanged.emit()

    @staticmethod
    def _cue_row(cue: VoiceoverCue) -> dict[str, Any]:
        return {
            "cueId": cue.id,
            "startFrame": cue.start_frame,
            "endFrame": cue.end_frame,
            "text": cue.text,
            "speaker": cue.speaker,
            "notes": cue.notes,
            "status": cue.status,
            "selectedTakeId": cue.selected_take_id or "",
            "placedClipId": cue.placed_clip_id or "",
            "revision": cue.revision,
            "takes": [
                {
                    "takeId": take.id,
                    "name": take.name,
                    "durationFrames": take.duration_frames,
                    "notes": take.notes,
                    "rating": take.rating,
                    "latencySamples": take.latency_compensation_samples,
                    "latencyMs": round(
                        take.latency_compensation_samples * 1000 / take.latency_sample_rate,
                        2,
                    ),
                    "calibrationDeviceId": take.calibration_device_id or "",
                    "selected": take.id == cue.selected_take_id,
                }
                for take in cue.takes
                if not take.archived
            ],
        }

    @staticmethod
    def _local_path(source_url: str) -> Path:
        url = QUrl(source_url)
        value = url.toLocalFile() if url.isLocalFile() else source_url
        return Path(value).expanduser().resolve(strict=True)

    @staticmethod
    def _device_id(device) -> str:
        return bytes(device.id().toHex().data()).decode("ascii")
