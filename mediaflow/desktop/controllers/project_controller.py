from __future__ import annotations

import json
import sqlite3
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Property, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from mediaflow.application.asset_service import AssetService
from mediaflow.application.events import TaskEvent
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.subtitle_service import SubtitleService
from mediaflow.application.task_service import TaskContext, TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.translation_service import TranslationService
from mediaflow.application.workflow_coordinator import WorkflowCoordinator
from mediaflow.desktop.models import (
    AssetListModel,
    AudioBusListModel,
    AudioEffectListModel,
    AudioEffectParameterListModel,
    ClipListModel,
    HighlightListModel,
    RecentProjectListModel,
    SequenceListModel,
    SubtitleDocumentListModel,
    SubtitlePlacementListModel,
    SubtitleSegmentListModel,
    TaskListModel,
    TimelineMarkerListModel,
    TimelineRangeListModel,
    TrackListModel,
    TransitionListModel,
)
from mediaflow.domain.audio_effect_presets import audio_effect_preset, audio_effect_preset_ids
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    AudioEffectKind,
    ColorMode,
    ExportFormat,
    TaskKind,
    TaskStatus,
    TrackKind,
    TransitionKind,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.models import (
    AudioBus,
    AudioEffect,
    ClipAudio,
    ClipTransform,
    ExportPreset,
    ProjectProfile,
    Task,
    audio_effect_parameter_schema,
)
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.domain.timebase import (
    frames_to_seconds,
    seconds_to_frames,
    source_frames_for_timeline_frames,
)
from mediaflow.infrastructure.asr_engine import FasterWhisperProcessEngine
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import LoudnessAnalysisService, MltExportService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.waveform_service import WaveformService
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService


class _TaskSignalBridge(QObject):
    eventReceived = Signal(object)


_AUDIO_PARAMETER_SPECS: dict[AudioEffectKind, list[dict]] = {
    AudioEffectKind.PARAMETRIC_EQ: [
        {"key": "low_db", "step": 0.5, "unit": "dB"},
        {"key": "low_mid_db", "step": 0.5, "unit": "dB"},
        {"key": "high_mid_db", "step": 0.5, "unit": "dB"},
        {"key": "high_db", "step": 0.5, "unit": "dB"},
    ],
    AudioEffectKind.HIGH_PASS: [{"key": "frequency_hz", "step": 10.0, "unit": "Hz"}],
    AudioEffectKind.LOW_PASS: [{"key": "frequency_hz", "step": 10.0, "unit": "Hz"}],
    AudioEffectKind.COMPRESSOR: [
        {"key": "threshold_db", "step": 0.5, "unit": "dB"},
        {"key": "ratio", "step": 0.1, "unit": ":1"},
        {"key": "attack_ms", "step": 1.0, "unit": "ms"},
        {"key": "release_ms", "step": 5.0, "unit": "ms"},
    ],
    AudioEffectKind.LIMITER: [{"key": "ceiling_db", "step": 0.1, "unit": "dB"}],
    AudioEffectKind.NOISE_GATE: [{"key": "threshold_db", "step": 0.5, "unit": "dB"}],
    AudioEffectKind.RNNOISE: [{"key": "mix", "step": 0.05, "unit": ""}],
    AudioEffectKind.CHANNEL_MAP: [{"key": "layout", "valueType": "layout", "step": 0.0, "unit": ""}],
    AudioEffectKind.LOUDNESS_NORMALIZE: [
        {"key": "target_lufs", "step": 0.5, "unit": "LUFS"},
        {"key": "true_peak_db", "step": 0.1, "unit": "dBTP"},
    ],
    AudioEffectKind.DUCKING: [
        {"key": "driver_bus_id", "valueType": "bus", "step": 0.0, "unit": ""},
        {"key": "threshold_db", "step": 0.5, "unit": "dB"},
        {"key": "reduction_db", "step": 0.5, "unit": "dB"},
        {"key": "attack_ms", "step": 5.0, "unit": "ms"},
        {"key": "release_ms", "step": 5.0, "unit": "ms"},
    ],
}


class ProjectController(QObject):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    taskDrawerChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadAnalysisChanged = Signal()
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.paths = RuntimePaths.discover()
        self.settings_repository = SettingsRepository()
        self.settings = self.settings_repository.load()
        self._repository: ProjectRepository | None = None
        self._assets: AssetService | None = None
        self._editor: TimelineEditor | None = None
        self._tasks: TaskService | None = None
        self._workflows: WorkflowCoordinator | None = None
        self._active_sequence_id = ""
        self._selected_asset_id = ""
        self._selected_clip_id = ""
        self._selected_document_id = ""
        self._selected_subtitle_placement_id = ""
        self._selected_highlight_id = ""
        self._selected_audio_bus_id = ""
        self._selected_audio_effect_id = ""
        self._selected_transition_id = ""
        self._selected_marker_id = ""
        self._selected_range_id = ""
        self._range_in_frame: int | None = None
        self._status_message = ""
        self._task_drawer_open = False
        self._preview_graph_path = ""
        self._hdr_preview_active = False
        self._pending_profile_asset_id = ""
        self._pending_profile_label = ""
        self._pending_relink_asset_id = ""
        self._pending_relink_path = ""
        self._preview_subtitles: list[tuple[int, int, str]] = []
        self._waveform_cache: dict[str, tuple[int, dict]] = {}
        self._audio_metrics: dict = {}
        self._video_encoder_options = EncoderDiscoveryService(self.paths).video_options()
        self._home_summary: dict = {
            "runningTaskCount": 0,
            "failedTaskCount": 0,
            "offlineAssetCount": 0,
            "pendingWorkflowCount": 0,
            "recentArtifactCount": 0,
        }
        self._download_analysis: dict = {}
        self._pending_preview_range: tuple[int, int] | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._compile_preview_graph)
        self._task_bridge = _TaskSignalBridge(self)
        self._task_bridge.eventReceived.connect(self._on_task_event, Qt.QueuedConnection)

        self._asset_model = AssetListModel(self)
        self._sequence_model = SequenceListModel(self)
        self._recent_project_model = RecentProjectListModel(self)
        self._track_model = TrackListModel(self)
        self._clip_model = ClipListModel(self)
        self._transition_model = TransitionListModel(self)
        self._marker_model = TimelineMarkerListModel(self)
        self._range_model = TimelineRangeListModel(self)
        self._task_model = TaskListModel(self)
        self._document_model = SubtitleDocumentListModel(self)
        self._segment_model = SubtitleSegmentListModel(self)
        self._subtitle_placement_model = SubtitlePlacementListModel(self)
        self._highlight_model = HighlightListModel(self)
        self._audio_bus_model = AudioBusListModel(self)
        self._audio_effect_model = AudioEffectListModel(self)
        self._audio_effect_parameter_model = AudioEffectParameterListModel(self)
        self._refresh_recent_projects()

    @Property(QObject, constant=True)
    def assetsModel(self) -> QObject:
        return self._asset_model

    @Property(QObject, constant=True)
    def sequencesModel(self) -> QObject:
        return self._sequence_model

    @Property(QObject, constant=True)
    def recentProjectsModel(self) -> QObject:
        return self._recent_project_model

    @Property("QVariantMap", notify=projectStateChanged)
    def homeSummary(self) -> dict:
        return self._home_summary

    @Property(QObject, constant=True)
    def tracksModel(self) -> QObject:
        return self._track_model

    @Property(QObject, constant=True)
    def clipsModel(self) -> QObject:
        return self._clip_model

    @Property(QObject, constant=True)
    def transitionsModel(self) -> QObject:
        return self._transition_model

    @Property(QObject, constant=True)
    def timelineMarkersModel(self) -> QObject:
        return self._marker_model

    @Property(QObject, constant=True)
    def timelineRangesModel(self) -> QObject:
        return self._range_model

    @Property(QObject, constant=True)
    def tasksModel(self) -> QObject:
        return self._task_model

    @Property(QObject, constant=True)
    def subtitleDocumentsModel(self) -> QObject:
        return self._document_model

    @Property(QObject, constant=True)
    def subtitleSegmentsModel(self) -> QObject:
        return self._segment_model

    @Property(QObject, constant=True)
    def subtitlePlacementsModel(self) -> QObject:
        return self._subtitle_placement_model

    @Property(QObject, constant=True)
    def highlightsModel(self) -> QObject:
        return self._highlight_model

    @Property(QObject, constant=True)
    def audioBusesModel(self) -> QObject:
        return self._audio_bus_model

    @Property(QObject, constant=True)
    def audioEffectsModel(self) -> QObject:
        return self._audio_effect_model

    @Property(QObject, constant=True)
    def audioEffectParametersModel(self) -> QObject:
        return self._audio_effect_parameter_model

    @Property("QVariantList", constant=True)
    def videoEncoderOptions(self) -> list[dict]:
        return [
            {**item, "label": self._localized_encoder_label(item["labelKey"])}
            for item in self._video_encoder_options
        ]

    @Property("QVariantList", notify=projectStateChanged)
    def subtitleTrackOptions(self) -> list[dict]:
        no_burn = {"zh_CN": "不烧录", "en": "Do not burn in", "ja": "焼き付けない"}
        values = [{"label": no_burn[self.settings.ui.language], "value": ""}]
        if not self._editor:
            return values
        values.extend(
            {"label": self._localized_default_name(track.name), "value": track.id}
            for track in self._editor.state.tracks
            if track.kind == TrackKind.SUBTITLE and track.enabled
        )
        return values

    @Property("QVariantMap", notify=projectStateChanged)
    def exportPresetData(self) -> dict:
        if not self._repository or not self._active_sequence_id:
            return {}
        preset = self._repository.get_sequence(self._active_sequence_id).export_preset
        return preset.model_dump(mode="json") if preset else {}

    @Property(bool, notify=downloadAnalysisChanged)
    def downloadAnalysisReady(self) -> bool:
        return bool(self._download_analysis)

    @Property("QVariantMap", notify=downloadAnalysisChanged)
    def downloadAnalysisData(self) -> dict:
        return self._download_analysis

    @Slot(str, result=bool)
    def isTransitionAvailable(self, kind: str) -> bool:
        if not self._editor:
            return False
        try:
            return transition_is_available(
                TransitionKind(kind),
                self._editor.state.sequence.profile.color_mode,
            )
        except ValueError:
            return False

    @Property(bool, notify=projectStateChanged)
    def hasProject(self) -> bool:
        return self._repository is not None

    @Property(str, notify=projectStateChanged)
    def projectName(self) -> str:
        return self._repository.get_project().name if self._repository else ""

    @Property(str, notify=projectStateChanged)
    def projectPath(self) -> str:
        return str(self._repository.project_dir) if self._repository else ""

    @Property(str, notify=projectStateChanged)
    def activeSequenceId(self) -> str:
        return self._active_sequence_id

    @Property(str, notify=projectStateChanged)
    def profileLabel(self) -> str:
        if not self._repository or not self._active_sequence_id:
            return ""
        profile = self._repository.get_sequence(self._active_sequence_id).profile
        fps = profile.fps_numerator / profile.fps_denominator
        return f"{profile.width}×{profile.height}  {fps:.3f} fps".replace(".000", "")

    @Property(str, notify=projectStateChanged)
    def colorMode(self) -> str:
        if not self._repository or not self._active_sequence_id:
            return ""
        return self._repository.get_sequence(self._active_sequence_id).profile.color_mode.value

    @Property(int, notify=projectStateChanged)
    def profileWidth(self) -> int:
        if not self._repository or not self._active_sequence_id:
            return 0
        return self._repository.get_sequence(self._active_sequence_id).profile.width

    @Property(int, notify=projectStateChanged)
    def profileHeight(self) -> int:
        if not self._repository or not self._active_sequence_id:
            return 0
        return self._repository.get_sequence(self._active_sequence_id).profile.height

    @Property(int, notify=projectStateChanged)
    def profileFpsNumerator(self) -> int:
        if not self._repository or not self._active_sequence_id:
            return 0
        return self._repository.get_sequence(self._active_sequence_id).profile.fps_numerator

    @Property(int, notify=projectStateChanged)
    def profileFpsDenominator(self) -> int:
        if not self._repository or not self._active_sequence_id:
            return 1
        return self._repository.get_sequence(self._active_sequence_id).profile.fps_denominator

    @Property(int, notify=projectStateChanged)
    def profileAudioChannels(self) -> int:
        if not self._repository or not self._active_sequence_id:
            return 2
        return self._repository.get_sequence(self._active_sequence_id).profile.audio_channels

    @Property(int, notify=historyChanged)
    def timelineDurationFrames(self) -> int:
        if not self._editor:
            return 0
        state = self._editor.state
        values = [clip.timeline_end for clip in state.clips]
        values.extend(marker.frame + 1 for marker in state.markers)
        values.extend(item.end_frame for item in state.ranges)
        return max(values, default=0)

    @Property(bool, notify=projectStateChanged)
    def readOnly(self) -> bool:
        return bool(self._repository and self._repository.read_only)

    @Property(int, notify=projectStateChanged)
    def offlineAssetCount(self) -> int:
        if not self._repository:
            return 0
        return sum(asset.status.value == "offline" for asset in self._repository.list_assets())

    @Property(str, notify=workflowChanged)
    def projectWorkflowMode(self) -> str:
        if not self._repository:
            return "inherit"
        value = self._repository.get_project().workflow_auto_continue
        return "inherit" if value is None else "auto" if value else "confirm"

    @Property(bool, notify=workflowChanged)
    def workflowPending(self) -> bool:
        return self._active_workflow_run() is not None

    @Property(str, notify=workflowChanged)
    def workflowRunId(self) -> str:
        run = self._active_workflow_run()
        return run.id if run else ""

    @Property(str, notify=workflowChanged)
    def workflowStage(self) -> str:
        run = self._active_workflow_run()
        return run.stage.value if run else ""

    @Property(str, notify=workflowChanged)
    def workflowStatus(self) -> str:
        run = self._active_workflow_run()
        return run.status.value if run else ""

    @Property(str, notify=workflowChanged)
    def workflowMessageCode(self) -> str:
        run = self._active_workflow_run()
        return run.message_code if run else ""

    @Property(str, notify=selectionChanged)
    def selectedAssetId(self) -> str:
        return self._selected_asset_id

    @Property(str, notify=selectionChanged)
    def selectedClipId(self) -> str:
        return self._selected_clip_id

    @Property(str, notify=selectionChanged)
    def selectedDocumentId(self) -> str:
        return self._selected_document_id

    @Property(str, notify=selectionChanged)
    def selectedSubtitlePlacementId(self) -> str:
        return self._selected_subtitle_placement_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedSubtitlePlacementData(self) -> dict:
        row = self._subtitle_placement_model.findRow(
            "placementId", self._selected_subtitle_placement_id
        )
        return self._subtitle_placement_model.get(row)

    @Property(str, notify=selectionChanged)
    def selectedHighlightId(self) -> str:
        return self._selected_highlight_id

    @Property(str, notify=selectionChanged)
    def selectedAudioBusId(self) -> str:
        return self._selected_audio_bus_id

    @Property(str, notify=selectionChanged)
    def selectedAudioEffectId(self) -> str:
        return self._selected_audio_effect_id

    @Property(str, notify=selectionChanged)
    def selectedTransitionId(self) -> str:
        return self._selected_transition_id

    @Property(str, notify=selectionChanged)
    def selectedMarkerId(self) -> str:
        return self._selected_marker_id

    @Property(str, notify=selectionChanged)
    def selectedRangeId(self) -> str:
        return self._selected_range_id

    @Property(int, notify=selectionChanged)
    def rangeInFrame(self) -> int:
        return -1 if self._range_in_frame is None else self._range_in_frame

    @Property("QVariantMap", notify=selectionChanged)
    def selectedTransitionData(self) -> dict:
        row = self._transition_model.findRow("transitionId", self._selected_transition_id)
        return self._transition_model.get(row)

    @Property("QVariantMap", notify=audioMetricsChanged)
    def audioMetrics(self) -> dict:
        return self._audio_metrics

    @Property(bool, notify=audioMetricsChanged)
    def audioAnalysisRunning(self) -> bool:
        if not self._tasks or not self._active_sequence_id:
            return False
        return any(
            task.kind == TaskKind.ANALYZE
            and task.sequence_id == self._active_sequence_id
            and task.status.value in {"pending", "running"}
            for task in self._tasks.repository.list()
        )

    @Property("QVariantMap", notify=selectionChanged)
    def selectedAssetData(self) -> dict:
        row = self._asset_model.findRow("assetId", self._selected_asset_id)
        return self._asset_model.get(row)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedClipData(self) -> dict:
        row = self._clip_model.findRow("clipId", self._selected_clip_id)
        return self._clip_model.get(row)

    @Property(bool, notify=historyChanged)
    def canUndo(self) -> bool:
        return bool(self._editor and self._editor.can_undo)

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return bool(self._editor and self._editor.can_redo)

    @Property(str, notify=statusChanged)
    def statusMessage(self) -> str:
        return self._status_message

    @Property(bool, notify=taskDrawerChanged)
    def taskDrawerOpen(self) -> bool:
        return self._task_drawer_open

    @Property(str, notify=previewGraphChanged)
    def previewGraphPath(self) -> str:
        return self._preview_graph_path

    @Property(str, constant=True)
    def mltRuntimeRoot(self) -> str:
        return str(self.paths.melt.parent) if self.paths.melt else ""

    @Property(bool, notify=profileConfirmationChanged)
    def profileConfirmationPending(self) -> bool:
        return bool(self._pending_profile_asset_id)

    @Property(str, notify=profileConfirmationChanged)
    def pendingProfileLabel(self) -> str:
        return self._pending_profile_label

    @Property("QVariantMap", notify=settingsChanged)
    def settingsData(self) -> dict:
        provider = next(
            (item for item in self.settings.llm_providers if item.enabled),
            None,
        )
        return {
            "language": self.settings.ui.language,
            "theme": self.settings.ui.theme,
            "defaultImportDirectory": self.settings.ui.default_import_directory or "",
            "windowWidth": self.settings.ui.window_width,
            "windowHeight": self.settings.ui.window_height,
            "leftPanelWidth": self.settings.ui.left_panel_width,
            "inspectorWidth": self.settings.ui.inspector_width,
            "timelineHeight": self.settings.ui.timeline_height,
            "autoContinue": self.settings.workflow.auto_continue,
            "downloadResolution": self.settings.download.resolution,
            "cookieFile": self.settings.download.cookie_file or "",
            "browserCookies": self.settings.download.browser_cookies or "",
            "asrModel": self.settings.asr.model,
            "asrDevice": self.settings.asr.device,
            "asrComputeType": self.settings.asr.compute_type,
            "asrLanguage": self.settings.asr.language,
            "translationTargetLanguage": self.settings.translation.target_language,
            "automaticProxy": self.settings.preview.automatic_proxy,
            "previewQuality": self.settings.preview.preview_quality,
            "hdrPreview": self.settings.preview.hdr_preview,
            "loudnessTarget": self.settings.audio.loudness_target_lufs,
            "truePeak": self.settings.audio.true_peak_db,
            "audioLayout": self.settings.audio.default_layout,
            "llmName": provider.name if provider else "",
            "llmBaseUrl": provider.base_url if provider else "",
            "llmApiKey": provider.api_key if provider else "",
            "llmModel": provider.model if provider else "",
        }

    @Property(str, notify=settingsChanged)
    def defaultTranslationLanguage(self) -> str:
        return self.settings.translation.target_language

    @Property(QUrl, notify=settingsChanged)
    def defaultImportDirectoryUrl(self) -> QUrl:
        path = self.settings.ui.default_import_directory
        return QUrl.fromLocalFile(path) if path and Path(path).is_dir() else QUrl()

    @Property(bool, notify=relinkConfirmationChanged)
    def relinkConfirmationPending(self) -> bool:
        return bool(self._pending_relink_asset_id)

    @Property(str, notify=relinkConfirmationChanged)
    def pendingRelinkPath(self) -> str:
        return self._pending_relink_path

    @Slot("QVariantMap")
    def saveSettings(self, values: dict) -> None:
        try:
            self.settings.ui.language = str(values.get("language", "zh_CN"))
            self.settings.ui.theme = str(values.get("theme", "dark"))
            self.settings.ui.default_import_directory = (
                str(values.get("defaultImportDirectory") or "").strip() or None
            )
            self.settings.workflow.auto_continue = bool(values.get("autoContinue", False))
            self.settings.download.resolution = str(values.get("downloadResolution", "best"))
            self.settings.download.cookie_file = str(values.get("cookieFile") or "") or None
            browser = str(values.get("browserCookies") or "")
            self.settings.download.browser_cookies = browser or None
            self.settings.asr.model = str(values.get("asrModel", "large-v3-turbo"))
            self.settings.asr.device = str(values.get("asrDevice", "auto"))
            self.settings.asr.compute_type = str(values.get("asrComputeType", "float16"))
            self.settings.asr.language = str(values.get("asrLanguage", "auto"))
            self.settings.translation.target_language = str(
                values.get("translationTargetLanguage") or ""
            )
            self.settings.preview.automatic_proxy = bool(values.get("automaticProxy", True))
            self.settings.preview.preview_quality = str(values.get("previewQuality", "auto"))
            self.settings.preview.hdr_preview = bool(values.get("hdrPreview", True))
            self.settings.audio.loudness_target_lufs = float(values.get("loudnessTarget", -14.0))
            self.settings.audio.true_peak_db = float(values.get("truePeak", -1.0))
            self.settings.audio.default_layout = str(values.get("audioLayout", "stereo"))

            base_url = str(values.get("llmBaseUrl") or "").strip()
            model = str(values.get("llmModel") or "").strip()
            if base_url or model:
                if not base_url or not model:
                    raise ValueError("LLM Base URL 和模型名称需要同时填写")
                current = self.settings.llm_providers[0] if self.settings.llm_providers else None
                provider = (
                    LlmProviderSettings(
                        id=current.id if current else None,
                        name=str(values.get("llmName") or "默认 LLM").strip(),
                        base_url=base_url,
                        api_key=str(values.get("llmApiKey") or ""),
                        model=model,
                        enabled=True,
                    )
                    if current
                    else LlmProviderSettings(
                        name=str(values.get("llmName") or "默认 LLM").strip(),
                        base_url=base_url,
                        api_key=str(values.get("llmApiKey") or ""),
                        model=model,
                        enabled=True,
                    )
                )
                self.settings.llm_providers = [provider]
            else:
                self.settings.llm_providers = []
            self.settings_repository.save(self.settings)
            if self._repository:
                self._workflows = WorkflowCoordinator(
                    self._repository,
                    global_auto_continue=self.settings.workflow.auto_continue,
                )
                self.workflowChanged.emit()
            self.settingsChanged.emit()
            self._set_status("设置已保存；界面语言将在下次启动时生效")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def setProjectWorkflowMode(self, mode: str) -> None:
        try:
            self._require_writable()
            values = {"inherit": None, "confirm": False, "auto": True}
            if mode not in values:
                raise ValueError("未知的项目工作流模式")
            self._repository.set_workflow_auto_continue(values[mode])
            self._workflows = WorkflowCoordinator(
                self._repository,
                global_auto_continue=self.settings.workflow.auto_continue,
            )
            self.workflowChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, int)
    def saveWindowSize(self, width: int, height: int) -> None:
        try:
            self.settings.ui.window_width = max(1180, int(width))
            self.settings.ui.window_height = max(720, int(height))
            self.settings_repository.save(self.settings)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, int, int)
    def savePanelLayout(self, left: int, inspector: int, timeline: int) -> None:
        try:
            self.settings.ui.left_panel_width = max(220, min(520, int(left)))
            self.settings.ui.inspector_width = max(250, min(520, int(inspector)))
            self.settings.ui.timeline_height = max(210, min(640, int(timeline)))
            self.settings_repository.save(self.settings)
            self.settingsChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def continueWorkflow(self, run_id: str, target_language: str = "") -> None:
        try:
            self._require_writable()
            self._continue_workflow(run_id, target_language=target_language)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def cancelWorkflow(self, run_id: str) -> None:
        try:
            self._require_writable()
            run = self._repository.get_workflow_run(run_id)
            for task_id in run.payload.get("task_ids", []):
                try:
                    task = self._tasks.repository.get(str(task_id))
                    if task.status in {
                        TaskStatus.PENDING,
                        TaskStatus.RUNNING,
                        TaskStatus.PAUSED,
                    }:
                        self._tasks.cancel(task.id)
                except KeyError:
                    continue
            self._workflows.cancel(run_id)
            self.workflowChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def createProject(self, parent_url: str, name: str) -> None:
        try:
            parent = self._local_path(parent_url)
            if not name.strip():
                raise ValueError("请输入项目名称")
            root = parent / self._safe_project_name(name)
            self._close_current()
            self._bind(ProjectRepository.create(root, name.strip()))
            self._set_status("项目已创建")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def openProject(self, path_url: str) -> None:
        try:
            path = self._local_path(path_url)
            root = path.parent if path.name == "project.mfp" else path
            self._close_current()
            self._bind(ProjectRepository.open(root, writable=True))
            self._set_status("项目已打开" if not self.readOnly else "项目正被其他窗口使用，已只读打开")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def closeProject(self) -> None:
        self._close_current()
        self._refresh_recent_projects()
        self.projectStateChanged.emit()

    @Slot(str)
    def importMedia(self, path_url: str) -> None:
        try:
            self._require_writable()
            asset = self._assets.import_external(self._local_path(path_url))
            self._selected_asset_id = asset.id
            run = self._workflows.begin(
                sequence_id=self._active_sequence_id,
                stage=WorkflowStage.PREPARE_MEDIA,
                asset_ids=[asset.id],
            )
            self._refresh_all()
            self.selectionChanged.emit()
            self._continue_if_configured(run)
            self._set_status(f"已导入 {asset.name}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def relinkMedia(self, asset_id: str, path_url: str) -> None:
        try:
            self._require_writable()
            replacement = self._local_path(path_url)
            try:
                asset = self._assets.relink(asset_id, replacement)
            except ValueError as error:
                if "does not match" not in str(error):
                    raise
                self._pending_relink_asset_id = asset_id
                self._pending_relink_path = str(replacement)
                self.relinkConfirmationChanged.emit()
                return
            self._selected_asset_id = asset.id
            self._refresh_all()
            self.selectionChanged.emit()
            self._set_status("离线素材已重新关联")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(bool)
    def resolveRelinkReplacement(self, replace: bool) -> None:
        asset_id = self._pending_relink_asset_id
        replacement = self._pending_relink_path
        self._pending_relink_asset_id = ""
        self._pending_relink_path = ""
        self.relinkConfirmationChanged.emit()
        if not replace or not asset_id:
            return
        try:
            self._require_writable()
            self._assets.relink(
                asset_id,
                replacement,
                allow_different_content=True,
            )
            self._selected_asset_id = asset_id
            self._refresh_all()
            self.selectionChanged.emit()
            self._set_status("已按用户确认替换素材内容，旧代理和波形已失效")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def relinkOfflineMedia(self, directory_url: str) -> None:
        try:
            self._require_writable()
            relinked, unresolved = self._assets.relink_offline_from_directory(self._local_path(directory_url))
            self._refresh_assets()
            self.projectStateChanged.emit()
            self.selectionChanged.emit()
            self._set_status(
                f"已重新关联 {len(relinked)} 个素材"
                + (f"，仍有 {len(unresolved)} 个未找到" if unresolved else "")
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def selectAsset(self, asset_id: str) -> None:
        self._selected_asset_id = asset_id
        self._selected_document_id = ""
        self._refresh_documents()
        self.selectionChanged.emit()

    @Slot(str)
    def selectClip(self, clip_id: str) -> None:
        self._selected_clip_id = clip_id
        self.selectionChanged.emit()

    @Slot(str)
    def selectSubtitleDocument(self, document_id: str) -> None:
        self._selected_document_id = document_id
        self._refresh_segments()
        self.selectionChanged.emit()

    @Slot(str)
    def selectSubtitlePlacement(self, placement_id: str) -> None:
        self._selected_subtitle_placement_id = placement_id
        self.selectionChanged.emit()

    @Slot(str)
    def selectHighlight(self, highlight_id: str) -> None:
        self._selected_highlight_id = highlight_id
        self.selectionChanged.emit()

    @Slot(str)
    def selectAudioBus(self, bus_id: str) -> None:
        self._selected_audio_bus_id = bus_id
        self._selected_audio_effect_id = ""
        self._refresh_audio_effects()
        self.selectionChanged.emit()

    @Slot(str)
    def selectAudioEffect(self, effect_id: str) -> None:
        self._selected_audio_effect_id = effect_id
        self._refresh_audio_effect_parameters()
        self.selectionChanged.emit()

    @Slot(str)
    def selectSequence(self, sequence_id: str) -> None:
        if not self._repository:
            return
        try:
            self._repository.get_sequence(sequence_id)
            self._active_sequence_id = sequence_id
            self._editor = TimelineEditor(self._repository, sequence_id)
            self._selected_clip_id = ""
            self._refresh_timeline()
            self._refresh_audio_metrics()
            self._refresh_preview_subtitles()
            self.projectStateChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def createShortSequence(self, name: str) -> None:
        try:
            self._require_writable()
            sequence = self._repository.create_short_sequence(name.strip() or "短视频")
            self._active_sequence_id = sequence.id
            self._editor = TimelineEditor(self._repository, sequence.id)
            self._refresh_all()
            self._set_status("短视频序列已创建")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def addAssetToTimeline(self, asset_id: str) -> None:
        try:
            self._require_writable()
            asset = self._repository.get_asset(asset_id)
            project = self._repository.get_project()
            if asset.kind == AssetKind.VIDEO and self._active_sequence_id == project.main_sequence_id:
                state = self._editor.state
                assets = {item.id: item for item in self._repository.list_assets()}
                has_timeline_video = any(
                    assets[item.asset_id].kind == AssetKind.VIDEO for item in state.clips
                )
                if not has_timeline_video:
                    suggested = self._assets.suggested_profile(asset.id)
                    if suggested and suggested != state.sequence.profile:
                        if state.clips:
                            fps = suggested.fps_numerator / suggested.fps_denominator
                            mode = "HDR10" if suggested.color_mode == ColorMode.HDR10_BT2020_PQ else "SDR"
                            self._pending_profile_asset_id = asset.id
                            self._pending_profile_label = (
                                f"{suggested.width}×{suggested.height}  {fps:.3f} fps  {mode}"
                            ).replace(".000", "")
                            self.profileConfirmationChanged.emit()
                            return
                        self._assets.adopt_main_profile_from_video(asset.id)
                        self._editor = TimelineEditor(self._repository, self._active_sequence_id)
                        asset = self._repository.get_asset(asset.id)
                        self.projectStateChanged.emit()
            self._append_asset_to_timeline(asset)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(bool)
    def resolveProfileAdoption(self, adopt: bool) -> None:
        asset_id = self._pending_profile_asset_id
        self._pending_profile_asset_id = ""
        self._pending_profile_label = ""
        self.profileConfirmationChanged.emit()
        if not asset_id:
            return
        try:
            self._require_writable()
            if adopt:
                self._assets.adopt_main_profile_from_video(asset_id)
                self._editor = TimelineEditor(self._repository, self._active_sequence_id)
                self._refresh_all()
            self._append_asset_to_timeline(self._repository.get_asset(asset_id))
        except Exception as error:
            self.errorOccurred.emit(str(error))

    def _append_asset_to_timeline(self, asset) -> None:
        target_kind = {
            AssetKind.VIDEO: TrackKind.VIDEO,
            AssetKind.IMAGE: TrackKind.VIDEO,
            AssetKind.AUDIO: TrackKind.AUDIO,
            AssetKind.SUBTITLE: TrackKind.SUBTITLE,
        }[asset.kind]
        track = next(track for track in self._editor.state.tracks if track.kind == target_kind)
        clips = self._editor.state.clips_for_track(track.id)
        start = max((clip.timeline_end for clip in clips), default=0)
        duration = asset.metadata.duration_frames or 150
        project = self._repository.get_project()
        if self._active_sequence_id != project.main_sequence_id and asset.metadata.duration_frames:
            main_profile = self._repository.get_sequence(project.main_sequence_id).profile
            active_profile = self._editor.state.sequence.profile
            duration = seconds_to_frames(
                frames_to_seconds(
                    asset.metadata.duration_frames,
                    main_profile.fps_numerator,
                    main_profile.fps_denominator,
                ),
                active_profile.fps_numerator,
                active_profile.fps_denominator,
            )
        clip = self._editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=start,
            source_in=0,
            duration=duration,
        )
        self._selected_clip_id = clip.id
        self._refresh_all()
        self.selectionChanged.emit()
        self._schedule_asset_background(asset, dropped_frames=0)

    def _schedule_asset_background(self, asset, *, dropped_frames: int) -> None:
        if not self._tasks:
            return
        if self._repository and any(
            run.stage == WorkflowStage.PREPARE_MEDIA and asset.id in run.asset_ids
            for run in self._repository.list_workflow_runs(active_only=True)
        ):
            return
        active = {
            (task.kind, tuple(task.input_asset_ids))
            for task in self._tasks.repository.list()
            if task.status.value in {"pending", "running", "paused"}
        }
        proxy_key = (TaskKind.PROXY, (asset.id,))
        decision = ProxyService.decision(asset, dropped_frames=dropped_frames)
        if (
            self.settings.preview.automatic_proxy
            and not asset.proxy_path
            and decision.required
            and proxy_key not in active
        ):
            self._start_task(
                TaskKind.PROXY,
                "自动生成代理",
                {"asset_id": asset.id, "reasons": list(decision.reasons)},
                [asset.id],
            )
        waveform_key = (TaskKind.WAVEFORM, (asset.id,))
        if asset.metadata.has_audio and not asset.waveform_path and waveform_key not in active:
            self._start_task(
                TaskKind.WAVEFORM,
                "生成波形",
                {"asset_id": asset.id},
                [asset.id],
            )

    @Slot(int)
    def reportPreviewDroppedFrames(self, dropped_frames: int) -> None:
        if (
            dropped_frames < self.settings.preview.dropped_frame_proxy_threshold
            or not self._repository
            or not self._editor
        ):
            return
        asset_ids = {clip.asset_id for clip in self._editor.state.clips}
        for asset_id in asset_ids:
            asset = self._repository.get_asset(asset_id)
            if not asset.proxy_path:
                self._schedule_asset_background(asset, dropped_frames=dropped_frames)

    @Slot(bool)
    def reportHdrPreviewActive(self, active: bool) -> None:
        if self._hdr_preview_active == active:
            return
        self._hdr_preview_active = active
        self._schedule_preview_graph()

    @Slot(str)
    def addTrack(self, kind: str) -> None:
        try:
            self._require_writable()
            track_kind = TrackKind(kind)
            audio_bus_id = None
            if track_kind in {TrackKind.VIDEO, TrackKind.AUDIO}:
                buses = self._repository.list_audio_buses(self._active_sequence_id)
                audio_bus_id = next(
                    (
                        bus.id
                        for bus in buses
                        if bus.name == ("音乐" if track_kind == TrackKind.AUDIO else "对白")
                    ),
                    next((bus.id for bus in buses if bus.parent_bus_id is None), None),
                )
            self._editor.add_track(track_kind, audio_bus_id=audio_bus_id)
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, bool, bool, bool, bool, str)
    def updateTrack(
        self,
        track_id: str,
        enabled: bool,
        locked: bool,
        muted: bool,
        solo: bool,
        audio_bus_id: str,
    ) -> None:
        try:
            self._require_writable()
            self._editor.set_track_state(
                track_id,
                enabled=enabled,
                locked=locked,
                muted=muted,
                solo=solo,
                audio_bus_id=audio_bus_id or None,
            )
            self._refresh_timeline()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int)
    def moveTrack(self, track_id: str, position: int) -> None:
        try:
            self._require_writable()
            self._editor.move_track(track_id, position)
            self._refresh_timeline()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, int, int, int, str, int)
    def updateSequenceProfile(
        self,
        width: int,
        height: int,
        fps_numerator: int,
        fps_denominator: int,
        color_mode: str,
        audio_channels: int,
    ) -> None:
        try:
            self._require_writable()
            mode = ColorMode(color_mode)
            self._editor.set_sequence_profile(
                ProjectProfile(
                    width=width,
                    height=height,
                    fps_numerator=fps_numerator,
                    fps_denominator=fps_denominator,
                    color_mode=mode,
                    bit_depth=10 if mode == ColorMode.HDR10_BT2020_PQ else 8,
                    audio_channels=audio_channels,
                )
            )
            self._refresh_assets()
            self._refresh_sequences()
            self._refresh_timeline()
            self.projectStateChanged.emit()
            self.historyChanged.emit()
            self._set_status("序列配置已更新")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, str)
    @Slot(str, int, str, float, int)
    def moveClip(
        self,
        clip_id: str,
        start_frame: int,
        track_id: str,
        pixels_per_frame: float = 3.0,
        playhead_frame: int = 0,
    ) -> None:
        try:
            self._require_writable()
            targets = self._timeline_snap_targets(clip_id, playhead_frame)
            self._editor.move_clip(
                clip_id,
                timeline_start=max(0, start_frame),
                track_id=track_id or None,
                snap_targets=targets,
                snap_tolerance_frames=self._snap_tolerance_frames(pixels_per_frame),
                transition_from_overlap=True,
            )
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, int)
    def copyClip(self, clip_id: str, pixels_per_frame: float, playhead_frame: int) -> None:
        try:
            self._require_writable()
            source = next(item for item in self._editor.state.clips if item.id == clip_id)
            copied = self._editor.copy_clip(
                clip_id,
                timeline_start=source.timeline_end,
                snap_targets=self._timeline_snap_targets(clip_id, playhead_frame),
                snap_tolerance_frames=self._snap_tolerance_frames(pixels_per_frame),
            )
            self._selected_clip_id = copied.id
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int)
    def splitClip(self, clip_id: str, frame: int) -> None:
        try:
            self._require_writable()
            _, right = self._editor.split_clip(clip_id, frame)
            self._selected_clip_id = right.id
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, bool)
    def deleteClip(self, clip_id: str, ripple: bool = False) -> None:
        try:
            self._require_writable()
            self._editor.delete_clip(clip_id, ripple=ripple)
            self._selected_clip_id = ""
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, int)
    def addTransitionAfter(self, clip_id: str, kind: str, duration: int) -> None:
        try:
            self._require_writable()
            state = self._editor.state
            left = next(clip for clip in state.clips if clip.id == clip_id)
            right = next(
                clip
                for clip in state.clips_for_track(left.track_id)
                if clip.timeline_start == left.timeline_end
            )
            transition = self._editor.create_transition(
                left.id,
                right.id,
                TransitionKind(kind),
                max(1, duration),
            )
            self._selected_transition_id = transition.id
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
            self._set_status("转场已添加")
        except StopIteration:
            self.errorOccurred.emit("所选片段后没有同轨道的相邻片段")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def selectTransition(self, transition_id: str) -> None:
        self._selected_transition_id = transition_id
        self._selected_clip_id = ""
        self.selectionChanged.emit()

    @Slot(str)
    def selectTimelineRange(self, range_id: str) -> None:
        self._selected_range_id = range_id
        self.selectionChanged.emit()

    @Slot(str, str, int)
    def updateTransition(self, transition_id: str, kind: str, duration: int) -> None:
        try:
            self._require_writable()
            self._editor.update_transition(
                transition_id,
                kind=TransitionKind(kind),
                duration=max(1, duration),
            )
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeTransition(self, transition_id: str) -> None:
        try:
            self._require_writable()
            self._editor.remove_transition(transition_id)
            self._selected_transition_id = ""
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def addTimelineMarker(self, frame: int) -> None:
        try:
            self._require_writable()
            marker = self._editor.add_marker(max(0, frame), f"标记 {len(self._editor.state.markers) + 1}")
            self._selected_marker_id = marker.id
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeTimelineMarker(self, marker_id: str) -> None:
        try:
            self._require_writable()
            self._editor.remove_marker(marker_id)
            self._selected_marker_id = ""
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def setRangeIn(self, frame: int) -> None:
        self._range_in_frame = max(0, frame)
        self.selectionChanged.emit()

    @Slot(int)
    def commitTimelineRange(self, frame: int) -> None:
        try:
            self._require_writable()
            if self._range_in_frame is None:
                raise ValueError("请先设置选区入点")
            start_frame, end_frame = sorted((self._range_in_frame, max(0, frame)))
            if start_frame == end_frame:
                raise ValueError("选区必须包含至少一帧")
            item = self._editor.add_range(
                start_frame,
                end_frame,
                f"选区 {len(self._editor.state.ranges) + 1}",
            )
            self._selected_range_id = item.id
            self._range_in_frame = None
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeTimelineRange(self, range_id: str) -> None:
        try:
            self._require_writable()
            self._editor.remove_range(range_id)
            self._selected_range_id = ""
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def createShortFromRange(self, range_id: str) -> None:
        try:
            self._require_writable()
            sequence = SequenceService(self._repository).create_short_from_range(
                self._active_sequence_id,
                range_id,
            )
            self._active_sequence_id = sequence.id
            self._editor = TimelineEditor(self._repository, sequence.id)
            self._selected_range_id = ""
            self._refresh_all()
            self._set_status("已从时间线选区创建短视频序列")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def saveProject(self) -> None:
        if not self._repository:
            return
        self._set_status("项目已保存")

    @Slot(str, int, int)
    def trimClip(self, clip_id: str, source_in: int, duration: int) -> None:
        try:
            self._require_writable()
            clip = next(item for item in self._editor.state.clips if item.id == clip_id)
            self._editor.trim_clip(
                clip_id,
                timeline_start=clip.timeline_start,
                source_in=max(0, source_in),
                duration=max(1, duration),
            )
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, int, bool)
    def trimClipEdges(
        self,
        clip_id: str,
        timeline_start: int,
        duration: int,
        trim_left: bool,
    ) -> None:
        try:
            self._require_writable()
            clip = next(item for item in self._editor.state.clips if item.id == clip_id)
            source_in = clip.source_in
            if trim_left:
                delta = timeline_start - clip.timeline_start
                source_delta = source_frames_for_timeline_frames(
                    delta,
                    clip.speed_numerator,
                    clip.speed_denominator,
                )
                source_in = (
                    clip.source_in + source_delta
                    if clip.speed_numerator > 0
                    else clip.source_in - source_delta
                )
            self._editor.trim_clip(
                clip_id,
                timeline_start=max(0, timeline_start),
                source_in=max(0, source_in),
                duration=max(1, duration),
            )
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, bool)
    def setClipSpeed(self, clip_id: str, speed: float, pitch_compensation: bool) -> None:
        try:
            self._require_writable()
            if abs(speed) < 0.25 or abs(speed) > 4.0:
                raise ValueError("速度必须在 0.25×～4× 或 -0.25×～-4×之间")
            fraction = Fraction(str(speed)).limit_denominator(1000)
            self._editor.set_clip_speed(
                clip_id,
                speed_numerator=fraction.numerator,
                speed_denominator=fraction.denominator,
                pitch_compensation=pitch_compensation,
            )
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, float, float, float, float, float, float, float, float, float)
    def setClipTransform(
        self,
        clip_id: str,
        x: float,
        y: float,
        scale_x: float,
        scale_y: float,
        rotation: float,
        crop_left: float,
        crop_top: float,
        crop_right: float,
        crop_bottom: float,
        opacity: float,
    ) -> None:
        try:
            self._require_writable()
            self._editor.set_clip_transform(
                clip_id,
                ClipTransform(
                    x=x,
                    y=y,
                    scale_x=max(0.01, scale_x),
                    scale_y=max(0.01, scale_y),
                    rotation=rotation,
                    crop_left=crop_left,
                    crop_top=crop_top,
                    crop_right=crop_right,
                    crop_bottom=crop_bottom,
                    opacity=opacity,
                ),
            )
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, float, int, int)
    def setClipAudio(
        self,
        clip_id: str,
        gain_db: float,
        pan: float,
        fade_in_frames: int,
        fade_out_frames: int,
    ) -> None:
        try:
            self._require_writable()
            self._editor.set_clip_audio(
                clip_id,
                ClipAudio(
                    gain_db=max(-60.0, min(24.0, gain_db)),
                    pan=pan,
                    fade_in_frames=max(0, fade_in_frames),
                    fade_out_frames=max(0, fade_out_frames),
                ),
            )
            self._refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def undo(self) -> None:
        try:
            self._editor.undo()
            self._refresh_assets()
            self._refresh_sequences()
            self._refresh_timeline()
            self.projectStateChanged.emit()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def redo(self) -> None:
        try:
            self._editor.redo()
            self._refresh_assets()
            self._refresh_sequences()
            self._refresh_timeline()
            self.projectStateChanged.emit()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def generateProxy(self, asset_id: str) -> None:
        self._start_task(TaskKind.PROXY, "生成代理", {"asset_id": asset_id}, [asset_id])

    @Slot(str)
    def generateWaveform(self, asset_id: str) -> None:
        self._start_task(TaskKind.WAVEFORM, "生成波形", {"asset_id": asset_id}, [asset_id])

    @Slot()
    def transcribeSelectedAsset(self) -> None:
        if not self._selected_asset_id:
            self.errorOccurred.emit("请先选择一个视频或音频素材")
            return
        self._start_task(
            TaskKind.TRANSCRIBE,
            "转录字幕",
            {"asset_id": self._selected_asset_id},
            [self._selected_asset_id],
        )

    @Slot(str, str)
    def translateDocument(self, document_id: str, target_language: str) -> None:
        if not document_id:
            self.errorOccurred.emit("请先选择源字幕文档")
            return
        language = target_language.strip() or self.settings.translation.target_language
        if not language:
            self.errorOccurred.emit("请选择目标语言")
            return
        self._start_task(
            TaskKind.TRANSLATE,
            "翻译字幕",
            {"document_id": document_id, "target_language": language},
        )

    @Slot(str)
    def analyzeHighlights(self, document_id: str) -> None:
        if not document_id:
            self.errorOccurred.emit("请先选择字幕文档")
            return
        self._start_task(
            TaskKind.HIGHLIGHT,
            "AI 高光分析",
            {"document_id": document_id},
        )

    @Slot(str)
    def placeSubtitleDocument(self, document_id: str) -> None:
        try:
            self._require_writable()
            subtitle_track = next(
                track for track in self._editor.state.tracks if track.kind == TrackKind.SUBTITLE
            )
            document = self._repository.get_subtitle_document(document_id)
            matching_clips = [clip for clip in self._editor.state.clips if clip.asset_id == document.asset_id]
            if matching_clips:
                placements = self._repository.place_subtitle_document(
                    document_id,
                    subtitle_track.id,
                    follow_clips=True,
                )
            else:
                placements = self._repository.place_subtitle_document(document_id, subtitle_track.id)
            self._refresh_preview_subtitles()
            self._set_status(f"已放入 {len(placements)} 条字幕")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, bool)
    def updateSubtitlePlacementText(
        self,
        placement_id: str,
        text: str,
        apply_to_document: bool,
    ) -> None:
        try:
            self._require_writable()
            if apply_to_document:
                self._repository.apply_subtitle_placement_to_document(placement_id, text)
                self._refresh_documents()
                self._set_status("修改已应用到字幕文档")
            else:
                self._repository.update_subtitle_placement_text(placement_id, text)
                self._set_status("已保存序列字幕覆盖")
            self._refresh_preview_subtitles()
            self.selectionChanged.emit()
            self._schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def createShortFromHighlight(self, highlight_id: str) -> None:
        try:
            self._require_writable()
            sequence = HighlightService(self._repository).create_short_sequence(highlight_id)
            self._active_sequence_id = sequence.id
            self._editor = TimelineEditor(self._repository, sequence.id)
            self._refresh_all()
            self._set_status("已从高光创建短视频序列")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def previewHighlight(self, highlight_id: str) -> None:
        try:
            candidate = next(
                item for item in self._repository.list_highlights() if item.id == highlight_id
            )
            project = self._repository.get_project()
            if self._active_sequence_id != project.main_sequence_id:
                self._active_sequence_id = project.main_sequence_id
                self._editor = TimelineEditor(self._repository, self._active_sequence_id)
                self._refresh_all()
            self._pending_preview_range = (candidate.start_frame, candidate.end_frame)
            self._schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def addHighlightToMainSequence(self, highlight_id: str) -> None:
        try:
            self._require_writable()
            candidate = next(
                item for item in self._repository.list_highlights() if item.id == highlight_id
            )
            main_sequence_id = self._repository.get_project().main_sequence_id
            editor = TimelineEditor(self._repository, main_sequence_id)
            video_track = next(
                track for track in editor.state.tracks if track.kind == TrackKind.VIDEO
            )
            timeline_start = max((clip.timeline_end for clip in editor.state.clips), default=0)
            clip = editor.add_clip(
                track_id=video_track.id,
                asset_id=candidate.asset_id,
                timeline_start=timeline_start,
                source_in=candidate.start_frame,
                duration=candidate.end_frame - candidate.start_frame,
            )
            if self._active_sequence_id == main_sequence_id:
                self._editor = editor
                self._selected_clip_id = clip.id
                self._refresh_timeline()
                self.selectionChanged.emit()
                self.historyChanged.emit()
            self._set_status("高光区间已添加到主序列")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def createAllHighlightShorts(self) -> None:
        try:
            self._require_writable()
            candidates = self._repository.list_highlights(self._selected_asset_id or None)
            if not candidates:
                raise ValueError("没有可创建的高光候选")
            service = HighlightService(self._repository)
            for candidate in candidates:
                service.create_short_sequence(candidate.id)
            self._refresh_sequences()
            self.projectStateChanged.emit()
            self._set_status(f"已创建 {len(candidates)} 个短视频草稿")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, bool, bool)
    @Slot(str, float, bool, bool, str, str)
    def updateAudioBus(
        self,
        bus_id: str,
        gain_db: float,
        muted: bool,
        solo: bool,
        parent_bus_id: str = "",
        channel_layout: str = "",
    ) -> None:
        try:
            self._require_writable()
            bus = next(
                item
                for item in self._repository.list_audio_buses(self._active_sequence_id)
                if item.id == bus_id
            )
            self._repository.save_audio_bus(
                bus.model_copy(
                    update={
                        "gain_db": max(-60.0, min(12.0, gain_db)),
                        "muted": muted,
                        "solo": solo,
                        "parent_bus_id": (
                            parent_bus_id or None if parent_bus_id or channel_layout else bus.parent_bus_id
                        ),
                        "channel_layout": channel_layout or bus.channel_layout,
                    }
                )
            )
            self._refresh_audio_buses()
            self._schedule_preview_graph()
            self._set_status(f"已更新 {bus.name} 总线")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def addAudioBus(self, name: str) -> None:
        try:
            self._require_writable()
            buses = self._repository.list_audio_buses(self._active_sequence_id)
            master = next((item for item in buses if item.parent_bus_id is None), None)
            if master is None:
                raise RuntimeError("序列缺少主总线")
            label = name.strip() or f"总线 {len(buses)}"
            bus = self._repository.save_audio_bus(
                AudioBus(
                    sequence_id=self._active_sequence_id,
                    name=label,
                    parent_bus_id=master.id,
                    position=len(buses),
                    channel_layout=master.channel_layout,
                )
            )
            self._selected_audio_bus_id = bus.id
            self._refresh_audio_buses()
            self.selectionChanged.emit()
            self._schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def addAudioEffect(self, bus_id: str, kind: str) -> None:
        try:
            self._require_writable()
            effect_kind = AudioEffectKind(kind)
            effects = self._repository.list_audio_effects(bus_id)
            parameters: dict = {}
            if effect_kind == AudioEffectKind.LOUDNESS_NORMALIZE:
                parameters = {
                    "target_lufs": self.settings.audio.loudness_target_lufs,
                    "true_peak_db": self.settings.audio.true_peak_db,
                }
            elif effect_kind == AudioEffectKind.DUCKING:
                parameters = {
                    "driver_bus_id": next(
                        (
                            bus.id
                            for bus in self._repository.list_audio_buses(self._active_sequence_id)
                            if bus.name in {"对白", "Dialogue"}
                        ),
                        "",
                    ),
                }
            effect = self._repository.save_audio_effect(
                AudioEffect(
                    bus_id=bus_id,
                    kind=effect_kind,
                    position=len(effects),
                    parameters=parameters,
                )
            )
            self._selected_audio_bus_id = bus_id
            self._selected_audio_effect_id = effect.id
            self._refresh_audio_effects()
            self._schedule_preview_graph()
            self.selectionChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, bool)
    def setAudioEffectEnabled(self, effect_id: str, enabled: bool) -> None:
        try:
            self._require_writable()
            effects = self._repository.list_audio_effects(self._selected_audio_bus_id)
            effect = next(item for item in effects if item.id == effect_id)
            self._repository.save_audio_effect(effect.model_copy(update={"enabled": enabled}))
            self._refresh_audio_effects()
            self._schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, result="QVariantList")
    def audioEffectPresets(self, kind: str) -> list[dict]:
        try:
            return [
                {"presetId": preset_id}
                for preset_id in audio_effect_preset_ids(AudioEffectKind(kind))
            ]
        except ValueError:
            return []

    @Slot(str, str)
    def applyAudioEffectPreset(self, effect_id: str, preset_id: str) -> None:
        try:
            self._require_writable()
            effects = self._repository.list_audio_effects(self._selected_audio_bus_id)
            effect = next(item for item in effects if item.id == effect_id)
            validated = AudioEffect.model_validate(
                {
                    **effect.model_dump(mode="python"),
                    "parameters": audio_effect_preset(effect.kind, preset_id),
                }
            )
            self._repository.save_audio_effect(validated)
            self._selected_audio_effect_id = effect_id
            self._refresh_audio_effects()
            self._schedule_preview_graph()
            self.selectionChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeAudioEffect(self, effect_id: str) -> None:
        try:
            self._require_writable()
            self._repository.remove_audio_effect(effect_id)
            self._selected_audio_effect_id = ""
            self._refresh_audio_effects()
            self._schedule_preview_graph()
            self.selectionChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int)
    def moveAudioEffect(self, effect_id: str, position: int) -> None:
        try:
            self._require_writable()
            effects = self._repository.list_audio_effects(self._selected_audio_bus_id)
            source_index = next(index for index, effect in enumerate(effects) if effect.id == effect_id)
            destination = max(0, min(len(effects) - 1, position))
            effect = effects.pop(source_index)
            effects.insert(destination, effect)
            effects = [item.model_copy(update={"position": index}) for index, item in enumerate(effects)]
            self._repository.save_audio_effect_chain(self._selected_audio_bus_id, effects)
            self._selected_audio_effect_id = effect_id
            self._refresh_audio_effects()
            self._schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, object)
    def setAudioEffectParameter(self, effect_id: str, key: str, value: object) -> None:
        try:
            self._require_writable()
            effects = self._repository.list_audio_effects(self._selected_audio_bus_id)
            effect = next(item for item in effects if item.id == effect_id)
            parameters = dict(effect.parameters)
            parameters[key] = value
            validated = AudioEffect.model_validate(
                {
                    **effect.model_dump(mode="python"),
                    "parameters": parameters,
                }
            )
            self._repository.save_audio_effect(validated)
            self._selected_audio_effect_id = effect_id
            self._refresh_audio_effects()
            self._schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def analyzeLoudness(self) -> None:
        if not self._active_sequence_id:
            self.errorOccurred.emit("请先打开一个序列")
            return
        self._start_task(
            TaskKind.ANALYZE,
            "测量序列响度",
            {"analysis": "loudness", "sequence_id": self._active_sequence_id},
            sequence_id=self._active_sequence_id,
        )

    @Slot(str)
    def downloadUrl(self, url: str) -> None:
        if not url.strip():
            self.errorOccurred.emit("请输入视频链接")
            return
        try:
            self._require_writable()
            self._start_download_workflow(url.strip(), self.settings.download.resolution, None)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def analyzeDownloadUrl(self, url: str) -> None:
        if not url.strip():
            self.errorOccurred.emit("请输入视频链接")
            return
        self._download_analysis = {}
        self.downloadAnalysisChanged.emit()
        self._start_task(
            TaskKind.ANALYZE,
            "分析下载链接",
            {"analysis": "download_url", "url": url.strip()},
            sequence_id=self._active_sequence_id,
        )

    @Slot(str, str)
    def startAnalyzedDownload(self, resolution: str, playlist_items: str) -> None:
        try:
            self._require_writable()
            url = str(self._download_analysis.get("url") or "")
            if not url:
                raise RuntimeError("下载分析结果已失效，请重新分析链接")
            self._start_download_workflow(
                url,
                resolution or self.settings.download.resolution,
                playlist_items.strip() or None,
            )
            self._download_analysis = {}
            self.downloadAnalysisChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def dismissDownloadAnalysis(self) -> None:
        self._download_analysis = {}
        self.downloadAnalysisChanged.emit()

    @Slot(str)
    def pauseTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.pause(task_id)
            self._set_status("已请求暂停任务")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def resumeTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.resume(task_id)
            self._refresh_tasks()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def cancelTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.cancel(task_id)
            self._set_status("已请求取消任务")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def openArtifact(self, path_value: str) -> None:
        try:
            path = Path(path_value)
            if not path.is_absolute():
                if not self._repository:
                    raise RuntimeError("当前没有打开的项目")
                path = self._repository.project_dir / path
            path = path.resolve(strict=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise RuntimeError(f"无法打开产物：{path}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def exportH264(self, path_url: str) -> None:
        self.exportSequence("h264", path_url)

    @Slot(str, str)
    def exportSequence(self, format_name: str, path_url: str) -> None:
        self.exportSequenceWithOptions(format_name, path_url, {})

    @Slot(str, str, "QVariantMap")
    def exportSequenceWithOptions(
        self,
        format_name: str,
        path_url: str,
        options: dict,
    ) -> None:
        try:
            output = self._local_path(path_url)
            export_format = ExportFormat(format_name)
            state = self._editor.state
            preset = self._default_export_preset(
                export_format,
                state.sequence.profile.color_mode,
                state.sequence.profile.fps,
            )
            updates: dict = {}
            field_map = {
                "container": "container",
                "videoCodec": "video_codec",
                "audioCodec": "audio_codec",
                "pixelFormat": "pixel_format",
                "qualityValue": "quality_value",
                "preset": "preset",
                "gopFrames": "gop_frames",
                "audioBitrate": "audio_bitrate",
                "burnSubtitleTrackId": "burn_subtitle_track_id",
            }
            for source_name, target_name in field_map.items():
                if source_name in options and options[source_name] not in {"", None}:
                    updates[target_name] = options[source_name]
            if isinstance(options.get("advanced"), dict):
                updates["advanced"] = options["advanced"]
            preset = preset.model_copy(update=updates)
            self._repository.save_sequence_export_preset(self._active_sequence_id, preset)
            self.projectStateChanged.emit()
            workflow = self._active_workflow_run()
            workflow_parameters = {}
            if workflow and workflow.stage == WorkflowStage.EXPORT:
                workflow_parameters = {
                    "workflow_run_id": workflow.id,
                    "workflow_stage": workflow.stage.value,
                }
            task = self._start_task(
                TaskKind.EXPORT,
                f"导出 {export_format.value.upper()}",
                {
                    "output_path": str(output),
                    "sequence_id": self._active_sequence_id,
                    "format": export_format.value,
                    "preset": preset.model_dump(mode="json"),
                    **workflow_parameters,
                },
                sequence_id=self._active_sequence_id,
            )
            if task and workflow_parameters:
                self._workflows.mark_running(workflow.id, task_ids=[task.id])
                self.workflowChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def toggleTaskDrawer(self) -> None:
        self._task_drawer_open = not self._task_drawer_open
        self.taskDrawerChanged.emit()

    @Slot()
    def shutdown(self) -> None:
        self._close_current()

    def _bind(self, repository: ProjectRepository) -> None:
        self._repository = repository
        self._assets = AssetService(repository, MediaProbe(self.paths))
        if not repository.read_only:
            self._assets.refresh_all()
        project = repository.get_project()
        self._active_sequence_id = project.main_sequence_id
        self._editor = TimelineEditor(repository, self._active_sequence_id)
        self._tasks = TaskService(TaskRepository(repository.project_dir))
        self._workflows = WorkflowCoordinator(
            repository,
            global_auto_continue=self.settings.workflow.auto_continue,
        )
        self._register_task_handlers()
        self._tasks.events.subscribe(self._task_bridge.eventReceived.emit)
        self._reconcile_interrupted_workflows()
        project_path = str(repository.project_dir)
        self.settings.ui.recent_project_paths = [
            project_path,
            *(path for path in self.settings.ui.recent_project_paths if Path(path) != repository.project_dir),
        ][:10]
        self.settings_repository.save(self.settings)
        self._refresh_recent_projects()
        self._refresh_all()

    def _refresh_recent_projects(self) -> None:
        items = []
        totals = {
            "runningTaskCount": 0,
            "failedTaskCount": 0,
            "offlineAssetCount": 0,
            "pendingWorkflowCount": 0,
            "recentArtifactCount": 0,
        }
        for path_value in self.settings.ui.recent_project_paths:
            path = Path(path_value)
            item = {
                "name": path.name,
                "path": str(path),
                "available": (path / "project.mfp").is_file(),
                "runningTaskCount": 0,
                "failedTaskCount": 0,
                "offlineAssetCount": 0,
                "pendingWorkflowCount": 0,
                "recentArtifact": "",
            }
            if item["available"]:
                try:
                    with ProjectRepository.open(path, writable=False) as repository:
                        tasks = TaskRepository(path).list()
                        item["runningTaskCount"] = sum(
                            task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED}
                            for task in tasks
                        )
                        item["failedTaskCount"] = sum(
                            task.status == TaskStatus.FAILED for task in tasks
                        )
                        item["offlineAssetCount"] = sum(
                            not repository.resolve_asset_path(asset).is_file()
                            for asset in repository.list_assets()
                        )
                        item["pendingWorkflowCount"] = len(
                            repository.list_workflow_runs(active_only=True)
                        )
                        artifacts = [
                            value
                            for task in reversed(tasks)
                            for value in reversed(task.artifacts)
                            if Path(value).is_file()
                            or (not Path(value).is_absolute() and (path / value).is_file())
                        ]
                        item["recentArtifact"] = artifacts[0] if artifacts else ""
                except (OSError, RuntimeError, sqlite3.Error):
                    item["available"] = False
            items.append(item)
            for key in (
                "runningTaskCount",
                "failedTaskCount",
                "offlineAssetCount",
                "pendingWorkflowCount",
            ):
                totals[key] += int(item[key])
            totals["recentArtifactCount"] += bool(item["recentArtifact"])
        self._home_summary = totals
        self._recent_project_model.set_items(items)

    def _register_task_handlers(self) -> None:
        assert self._tasks and self._repository

        def proxy(context: TaskContext) -> list[str]:
            asset = self._repository.get_asset(context.task.parameters["asset_id"])
            sequence = self._repository.get_sequence(context.task.sequence_id or self._active_sequence_id)
            context.report_progress(10, "proxy_preparing")
            updated = ProxyService(self._repository, self.paths).generate(
                asset,
                sequence.profile,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            context.report_progress(95, "proxy_verifying")
            return [
                path
                for path in (updated.proxy_path, updated.sdr_preview_proxy_path)
                if path
            ]

        def waveform(context: TaskContext) -> list[str]:
            asset = self._repository.get_asset(context.task.parameters["asset_id"])
            context.report_progress(10, "waveform_decoding")
            updated = WaveformService(self._repository, self.paths).generate(
                asset,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            context.report_progress(95, "waveform_verifying")
            return [updated.waveform_path] if updated.waveform_path else []

        def download(context: TaskContext) -> list[str]:
            service = YtDlpDownloadService(self._assets)
            settings = self.settings.download
            assets = service.download(
                context.task.parameters["url"],
                resolution=context.task.parameters.get("resolution", settings.resolution),
                cookie_file=settings.cookie_file,
                browser_cookies=settings.browser_cookies,
                playlist_items=context.task.parameters.get("playlist_items"),
                progress=context.report_progress,
            )
            return [asset.path for asset in assets]

        def export(context: TaskContext) -> list[str]:
            sequence_id = context.task.parameters["sequence_id"]
            state = self._repository.load_timeline(sequence_id)
            export_format = ExportFormat(context.task.parameters.get("format", "h264"))
            preset_data = context.task.parameters.get("preset")
            preset = (
                ExportPreset.model_validate(preset_data)
                if preset_data
                else self._default_export_preset(
                    export_format,
                    state.sequence.profile.color_mode,
                    state.sequence.profile.fps,
                )
            )
            context.report_progress(5, "export_compiling")
            result = MltExportService(TimelineCompiler(self._repository), self.paths).export(
                state,
                preset,
                context.task.parameters["output_path"],
                check_cancelled=context.cancellation.raise_if_requested,
            )
            context.report_progress(98, "export_verifying")
            return [str(result.output_path), *(str(path) for path in result.subtitle_files)]

        def transcribe(context: TaskContext) -> list[str]:
            document = SubtitleService(self._repository).transcribe_asset(
                context.task.parameters["asset_id"],
                FasterWhisperProcessEngine(
                    self.settings.asr,
                    self.paths,
                    check_cancelled=context.cancellation.raise_if_requested,
                ),
                progress=context.report_progress,
            )
            files = list(
                (self._repository.project_dir / "generated" / "subtitles" / document.asset_id).glob(
                    f"*{document.id[:8]}*.srt"
                )
            )
            return [str(path.relative_to(self._repository.project_dir).as_posix()) for path in files]

        def translate(context: TaskContext) -> list[str]:
            provider = self._active_llm_provider()
            document = TranslationService(self._repository).translate_document(
                context.task.parameters["document_id"],
                target_language=context.task.parameters["target_language"],
                provider=provider,
                progress=context.report_progress,
            )
            files = list(
                (self._repository.project_dir / "generated" / "subtitles" / document.asset_id).glob(
                    f"*{document.id[:8]}*.srt"
                )
            )
            return [str(path.relative_to(self._repository.project_dir).as_posix()) for path in files]

        def highlight(context: TaskContext) -> list[str]:
            HighlightService(self._repository).analyze_document(
                context.task.parameters["document_id"],
                provider=self._active_llm_provider(),
                progress=context.report_progress,
            )
            return []

        def analyze(context: TaskContext) -> list[str]:
            analysis_kind = context.task.parameters.get("analysis")
            if analysis_kind == "download_url":
                context.report_progress(10, "download_analyzing")
                settings = self.settings.download
                summary = YtDlpDownloadService(self._assets).analyze(
                    context.task.parameters["url"],
                    cookie_file=settings.cookie_file,
                    browser_cookies=settings.browser_cookies,
                )
                summary["url"] = context.task.parameters["url"]
                destination = (
                    self._repository.project_dir
                    / "cache"
                    / "download-analysis"
                    / f"{context.task.id}.json"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                context.report_progress(100, "download_analysis_ready")
                return [str(destination.relative_to(self._repository.project_dir).as_posix())]
            if analysis_kind != "loudness":
                raise ValueError("Unknown sequence analysis type")
            sequence_id = context.task.parameters["sequence_id"]
            state = self._repository.load_timeline(sequence_id)
            _metrics, result_path = LoudnessAnalysisService(
                TimelineCompiler(self._repository),
                self.paths,
            ).analyze(
                state,
                check_cancelled=context.cancellation.raise_if_requested,
                report_progress=context.report_progress,
            )
            return [str(result_path.relative_to(self._repository.project_dir).as_posix())]

        self._tasks.register(TaskKind.ANALYZE, analyze)
        self._tasks.register(TaskKind.PROXY, proxy)
        self._tasks.register(TaskKind.WAVEFORM, waveform)
        self._tasks.register(TaskKind.DOWNLOAD, download)
        self._tasks.register(TaskKind.EXPORT, export)
        self._tasks.register(TaskKind.TRANSCRIBE, transcribe)
        self._tasks.register(TaskKind.TRANSLATE, translate)
        self._tasks.register(TaskKind.HIGHLIGHT, highlight)

    def _start_task(
        self,
        kind: TaskKind,
        name: str,
        parameters: dict,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task | None:
        try:
            return self._create_task(
                kind,
                name,
                parameters,
                input_asset_ids,
                sequence_id=sequence_id,
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))
            return None

    def _create_task(
        self,
        kind: TaskKind,
        name: str,
        parameters: dict,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task:
        self._require_writable()
        project = self._repository.get_project()
        task = self._tasks.start(
            project_id=project.id,
            sequence_id=sequence_id or self._active_sequence_id,
            kind=kind,
            name=name,
            input_asset_ids=input_asset_ids,
            parameters=parameters,
        )
        self._task_drawer_open = True
        self.taskDrawerChanged.emit()
        self._refresh_tasks()
        return task

    @Slot(object)
    def _on_task_event(self, event: TaskEvent) -> None:
        self._handle_workflow_task_event(event)
        try:
            task = Task.model_validate(event.payload)
            if (
                task.kind == TaskKind.ANALYZE
                and task.parameters.get("analysis") == "download_url"
                and task.status == TaskStatus.COMPLETED
                and task.artifacts
                and self._repository
            ):
                path = Path(task.artifacts[0])
                if not path.is_absolute():
                    path = self._repository.project_dir / path
                self._download_analysis = json.loads(path.read_text(encoding="utf-8"))
                self.downloadAnalysisChanged.emit()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self._refresh_tasks()
        self._refresh_audio_metrics()
        self._refresh_assets()
        self._refresh_documents()
        self._refresh_highlights()
        self._refresh_recent_projects()
        self._schedule_preview_graph()
        self.workflowChanged.emit()

    def _active_workflow_run(self):
        if not self._repository:
            return None
        runs = self._repository.list_workflow_runs(active_only=True)
        return runs[0] if runs else None

    def _continue_workflow(self, run_id: str, *, target_language: str = "") -> None:
        run = self._repository.get_workflow_run(run_id)
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            return
        if run.status == WorkflowStatus.RUNNING:
            self._set_status("当前工作流阶段正在运行")
            return

        if run.stage == WorkflowStage.DOWNLOAD:
            url = str(run.payload.get("url") or "").strip()
            if not url:
                self._workflows.block(run.id, "workflow_download_url_required")
                self.workflowChanged.emit()
                return
            self._run_workflow_tasks(
                run,
                [
                    (
                        TaskKind.DOWNLOAD,
                        "下载视频",
                        {"url": url},
                        [],
                    )
                ],
            )
            return

        if not run.asset_ids and run.stage != WorkflowStage.EXPORT:
            self._workflows.block(run.id, "workflow_assets_required")
            self.workflowChanged.emit()
            return
        offline = [
            asset_id
            for asset_id in run.asset_ids
            if self._repository.get_asset(asset_id).status.value == "offline"
        ]
        if offline:
            self._workflows.block(run.id, "workflow_offline_assets")
            self.workflowChanged.emit()
            return

        if run.stage == WorkflowStage.PREPARE_MEDIA:
            tasks: list[tuple[TaskKind, str, dict, list[str]]] = []
            for asset_id in run.asset_ids:
                asset = self._repository.get_asset(asset_id)
                decision = ProxyService.decision(asset, dropped_frames=0)
                if not asset.proxy_path and decision.required:
                    tasks.append(
                        (
                            TaskKind.PROXY,
                            "生成代理",
                            {"asset_id": asset.id, "reasons": list(decision.reasons)},
                            [asset.id],
                        )
                    )
                if asset.metadata.has_audio and not asset.waveform_path:
                    tasks.append(
                        (
                            TaskKind.WAVEFORM,
                            "生成波形",
                            {"asset_id": asset.id},
                            [asset.id],
                        )
                    )
            self._run_or_advance_workflow(run, tasks)
            return

        if run.stage == WorkflowStage.TRANSCRIBE:
            transcribable = [
                asset_id
                for asset_id in run.asset_ids
                if self._repository.get_asset(asset_id).kind in {AssetKind.VIDEO, AssetKind.AUDIO}
            ]
            if not transcribable:
                self._workflows.block(run.id, "workflow_no_transcribable_assets")
                self.workflowChanged.emit()
                return
            tasks = [
                (
                    TaskKind.TRANSCRIBE,
                    "转录字幕",
                    {"asset_id": asset_id},
                    [asset_id],
                )
                for asset_id in transcribable
            ]
            document_ids_before = [
                document.id
                for asset_id in run.asset_ids
                for document in self._repository.list_subtitle_documents(asset_id)
            ]
            self._run_workflow_tasks(
                run,
                tasks,
                payload={"document_ids_before_transcribe": document_ids_before},
            )
            return

        if run.stage == WorkflowStage.TRANSLATE:
            language = (
                target_language.strip()
                or str(run.payload.get("target_language") or "").strip()
                or self.settings.translation.target_language
            )
            if not language:
                self._workflows.block(run.id, "workflow_translation_language_required")
                self.workflowChanged.emit()
                return
            if not self._has_active_llm_provider():
                self._workflows.block(run.id, "workflow_llm_provider_required")
                self.workflowChanged.emit()
                return
            source_ids = {str(value) for value in run.payload.get("source_document_ids", [])}
            documents = [
                document
                for asset_id in run.asset_ids
                for document in self._repository.list_subtitle_documents(asset_id)
                if document.id in source_ids
            ]
            if not documents:
                self._workflows.block(run.id, "workflow_source_subtitles_required")
                self.workflowChanged.emit()
                return
            document_ids_before = [
                document.id
                for asset_id in run.asset_ids
                for document in self._repository.list_subtitle_documents(asset_id)
            ]
            tasks = [
                (
                    TaskKind.TRANSLATE,
                    "翻译字幕",
                    {"document_id": document.id, "target_language": language},
                    [document.asset_id],
                )
                for document in documents
            ]
            self._run_workflow_tasks(
                run,
                tasks,
                payload={
                    "target_language": language,
                    "document_ids_before_translate": document_ids_before,
                },
            )
            return

        if run.stage == WorkflowStage.HIGHLIGHT:
            if not self._has_active_llm_provider():
                self._workflows.block(run.id, "workflow_llm_provider_required")
                self.workflowChanged.emit()
                return
            translated_ids = {str(value) for value in run.payload.get("translated_document_ids", [])}
            source_ids = {str(value) for value in run.payload.get("source_document_ids", [])}
            selected_ids = translated_ids or source_ids
            documents = [
                document
                for asset_id in run.asset_ids
                for document in self._repository.list_subtitle_documents(asset_id)
                if document.id in selected_ids
            ]
            if not documents:
                self._workflows.block(run.id, "workflow_subtitles_required")
                self.workflowChanged.emit()
                return
            tasks = [
                (
                    TaskKind.HIGHLIGHT,
                    "AI 高光分析",
                    {"document_id": document.id},
                    [document.asset_id],
                )
                for document in documents
            ]
            highlight_ids_before = [
                candidate.id
                for asset_id in run.asset_ids
                for candidate in self._repository.list_highlights(asset_id)
            ]
            self._run_workflow_tasks(
                run,
                tasks,
                payload={"highlight_ids_before": highlight_ids_before},
            )
            return

        if run.stage == WorkflowStage.CREATE_SHORTS:
            candidate_ids = {str(value) for value in run.payload.get("highlight_candidate_ids", [])}
            candidates = [
                candidate
                for asset_id in run.asset_ids
                for candidate in self._repository.list_highlights(asset_id)
                if candidate.id in candidate_ids
            ]
            if not candidates:
                self._workflows.block(run.id, "workflow_highlights_required")
                self.workflowChanged.emit()
                return
            service = HighlightService(self._repository)
            sequence_ids = [service.create_short_sequence(item.id).id for item in candidates]
            advanced = self._workflows.advance(
                run.id,
                payload={"short_sequence_ids": sequence_ids},
            )
            self._refresh_sequences()
            self._continue_if_configured(advanced)
            return

        if run.stage == WorkflowStage.EXPORT:
            self._workflows.block(run.id, "workflow_export_settings_required")
            self.workflowChanged.emit()

    def _run_or_advance_workflow(
        self,
        run,
        tasks: list[tuple[TaskKind, str, dict, list[str]]],
    ) -> None:
        if tasks:
            self._run_workflow_tasks(run, tasks)
            return
        advanced = self._workflows.advance(run.id)
        self._continue_if_configured(advanced)

    def _run_workflow_tasks(
        self,
        run,
        specs: list[tuple[TaskKind, str, dict, list[str]]],
        *,
        payload: dict | None = None,
    ) -> None:
        for task_id in run.payload.get("task_ids", []):
            try:
                existing = self._tasks.repository.get(str(task_id))
                if existing.status in {
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                    TaskStatus.PAUSED,
                }:
                    self._tasks.cancel(existing.id)
            except KeyError:
                continue
        task_ids = []
        for kind, name, parameters, asset_ids in specs:
            task = self._create_task(
                kind,
                name,
                {
                    **parameters,
                    "workflow_run_id": run.id,
                    "workflow_stage": run.stage.value,
                },
                asset_ids,
                sequence_id=run.sequence_id,
            )
            task_ids.append(task.id)
        self._workflows.mark_running(run.id, task_ids=task_ids, payload=payload)
        self.workflowChanged.emit()

    def _handle_workflow_task_event(self, event: TaskEvent) -> None:
        if not self._repository or not self._tasks or not self._workflows:
            return
        try:
            task = Task.model_validate(event.payload)
        except (TypeError, ValueError):
            return
        run_id = str(task.parameters.get("workflow_run_id") or "")
        stage = str(task.parameters.get("workflow_stage") or "")
        if not run_id:
            return
        try:
            run = self._repository.get_workflow_run(run_id)
        except KeyError:
            return
        if run.status != WorkflowStatus.RUNNING or run.stage.value != stage:
            return
        task_ids = [str(value) for value in run.payload.get("task_ids", [])]
        if task.id not in task_ids:
            return
        tasks = [self._tasks.repository.get(task_id) for task_id in task_ids]
        if any(item.status == TaskStatus.FAILED for item in tasks):
            failed = next(item for item in tasks if item.status == TaskStatus.FAILED)
            self._workflows.block(run.id, "workflow_task_failed")
            self._set_status(f"工作流任务失败：{failed.error or failed.name}")
            return
        if any(item.status == TaskStatus.CANCELLED for item in tasks):
            self._workflows.block(run.id, "workflow_task_cancelled")
            return
        if not all(item.status == TaskStatus.COMPLETED for item in tasks):
            return

        payload: dict = {}
        if run.stage == WorkflowStage.DOWNLOAD:
            artifact_paths = set()
            for item in tasks:
                for value in item.artifacts:
                    path = Path(value)
                    if not path.is_absolute():
                        path = self._repository.project_dir / path
                    artifact_paths.add(str(path.resolve()))
            assets = [
                asset
                for asset in self._repository.list_assets()
                if asset.origin == AssetOrigin.DOWNLOAD
                and str(self._repository.resolve_asset_path(asset).resolve()) in artifact_paths
            ]
            if not assets:
                self._workflows.block(run.id, "workflow_download_artifacts_missing")
                return
            self._selected_asset_id = assets[0].id
            self.selectionChanged.emit()
            advanced = self._workflows.advance(run.id, asset_ids=[asset.id for asset in assets])
        else:
            if run.stage == WorkflowStage.TRANSCRIBE:
                before = {str(value) for value in run.payload.get("document_ids_before_transcribe", [])}
                source_ids = [
                    document.id
                    for asset_id in run.asset_ids
                    for document in self._repository.list_subtitle_documents(asset_id)
                    if document.is_source and document.id not in before
                ]
                if not source_ids:
                    self._workflows.block(run.id, "workflow_transcription_artifacts_missing")
                    return
                payload["source_document_ids"] = source_ids
            elif run.stage == WorkflowStage.TRANSLATE:
                before = {str(value) for value in run.payload.get("document_ids_before_translate", [])}
                translated_ids = [
                    document.id
                    for asset_id in run.asset_ids
                    for document in self._repository.list_subtitle_documents(asset_id)
                    if not document.is_source and document.id not in before
                ]
                if not translated_ids:
                    self._workflows.block(run.id, "workflow_translation_artifacts_missing")
                    return
                payload["translated_document_ids"] = translated_ids
            elif run.stage == WorkflowStage.HIGHLIGHT:
                before = {str(value) for value in run.payload.get("highlight_ids_before", [])}
                highlight_ids = [
                    candidate.id
                    for asset_id in run.asset_ids
                    for candidate in self._repository.list_highlights(asset_id)
                    if candidate.id not in before
                ]
                if not highlight_ids:
                    self._workflows.block(run.id, "workflow_highlight_artifacts_missing")
                    return
                payload["highlight_candidate_ids"] = highlight_ids
            advanced = self._workflows.advance(run.id, payload=payload)
        self._continue_if_configured(advanced)

    def _reconcile_interrupted_workflows(self) -> None:
        if not self._repository or not self._tasks or not self._workflows:
            return
        for run in self._repository.list_workflow_runs(active_only=True):
            if run.status != WorkflowStatus.RUNNING:
                continue
            task_ids = [str(value) for value in run.payload.get("task_ids", [])]
            if not task_ids:
                self._workflows.block(run.id, "workflow_interrupted")
                continue
            tasks = []
            for task_id in task_ids:
                try:
                    tasks.append(self._tasks.repository.get(task_id))
                except KeyError:
                    self._workflows.block(run.id, "workflow_interrupted")
                    break
            else:
                if any(task.status in {TaskStatus.PENDING, TaskStatus.PAUSED} for task in tasks):
                    self._workflows.block(run.id, "workflow_interrupted")

    def _continue_if_configured(self, run) -> None:
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            self.workflowChanged.emit()
            return
        if run.auto_continue or not self._stage_requires_confirmation(run.stage):
            self._continue_workflow(run.id)
        else:
            self.workflowChanged.emit()

    def _stage_requires_confirmation(self, stage: WorkflowStage) -> bool:
        settings = self.settings.workflow
        return {
            WorkflowStage.DOWNLOAD: settings.confirm_download,
            WorkflowStage.PREPARE_MEDIA: settings.confirm_proxy,
            WorkflowStage.TRANSCRIBE: settings.confirm_transcribe,
            WorkflowStage.TRANSLATE: settings.confirm_translate,
            WorkflowStage.HIGHLIGHT: settings.confirm_highlight,
            WorkflowStage.CREATE_SHORTS: True,
            WorkflowStage.EXPORT: settings.confirm_export,
            WorkflowStage.COMPLETE: False,
        }[stage]

    def _has_active_llm_provider(self) -> bool:
        return any(provider.enabled for provider in self.settings.llm_providers)

    def _refresh_all(self) -> None:
        self._refresh_assets()
        self._refresh_sequences()
        self._refresh_timeline()
        self._refresh_tasks()
        self._refresh_documents()
        self._refresh_highlights()
        self._refresh_audio_buses()
        self._refresh_audio_metrics()
        self._refresh_preview_subtitles()
        self.projectStateChanged.emit()
        self.historyChanged.emit()
        self.workflowChanged.emit()

    def _refresh_assets(self) -> None:
        if not self._repository:
            self._asset_model.set_items([])
            return
        self._asset_model.set_items(
            [
                {
                    "assetId": asset.id,
                    "name": asset.name,
                    "kind": asset.kind.value,
                    "path": asset.path,
                    "status": asset.status.value,
                    "managed": asset.managed,
                    "durationFrames": asset.metadata.duration_frames,
                    "width": asset.metadata.width or 0,
                    "height": asset.metadata.height or 0,
                    "proxyReady": bool(asset.proxy_path),
                    "waveformReady": bool(asset.waveform_path),
                }
                for asset in self._repository.list_assets()
            ]
        )

    def _refresh_sequences(self) -> None:
        if not self._repository:
            self._sequence_model.set_items([])
            return
        self._sequence_model.set_items(
            [
                {
                    "sequenceId": sequence.id,
                    "name": sequence.name,
                    "kind": sequence.kind.value,
                    "profile": f"{sequence.profile.width}×{sequence.profile.height}",
                    "colorMode": sequence.profile.color_mode.value,
                }
                for sequence in self._repository.list_sequences()
            ]
        )

    def _refresh_timeline(self) -> None:
        if not self._editor or not self._repository:
            self._track_model.set_items([])
            self._clip_model.set_items([])
            self._transition_model.set_items([])
            self._marker_model.set_items([])
            self._range_model.set_items([])
            return
        state = self._editor.state
        assets = {asset.id: asset for asset in self._repository.list_assets()}
        track_positions = {track.id: index for index, track in enumerate(state.tracks)}
        self._track_model.set_items(
            [
                {
                    "trackId": track.id,
                    "name": track.name,
                    "displayName": self._localized_default_name(track.name),
                    "kind": track.kind.value,
                    "position": track.position,
                    "enabled": track.enabled,
                    "locked": track.locked,
                    "muted": track.muted,
                    "solo": track.solo,
                    "audioBusId": track.audio_bus_id or "",
                }
                for track in state.tracks
            ]
        )
        self._clip_model.set_items(
            [
                {
                    "clipId": clip.id,
                    "trackId": clip.track_id,
                    "trackPosition": track_positions[clip.track_id],
                    "assetId": clip.asset_id,
                    "assetName": assets[clip.asset_id].name,
                    "sourceIn": clip.source_in,
                    "startFrame": clip.timeline_start,
                    "durationFrames": clip.duration,
                    "endFrame": clip.timeline_end,
                    "speed": clip.speed_numerator / clip.speed_denominator,
                    "pitchCompensation": clip.pitch_compensation,
                    "kind": assets[clip.asset_id].kind.value,
                    "waveformReady": bool(assets[clip.asset_id].waveform_path),
                    "x": clip.transform.x,
                    "y": clip.transform.y,
                    "scaleX": clip.transform.scale_x,
                    "scaleY": clip.transform.scale_y,
                    "rotation": clip.transform.rotation,
                    "cropLeft": clip.transform.crop_left,
                    "cropTop": clip.transform.crop_top,
                    "cropRight": clip.transform.crop_right,
                    "cropBottom": clip.transform.crop_bottom,
                    "opacity": clip.transform.opacity,
                    "gainDb": clip.audio.gain_db,
                    "pan": clip.audio.pan,
                    "fadeInFrames": clip.audio.fade_in_frames,
                    "fadeOutFrames": clip.audio.fade_out_frames,
                }
                for clip in state.clips
            ]
        )
        clips = {clip.id: clip for clip in state.clips}
        self._transition_model.set_items(
            [
                {
                    "transitionId": item.id,
                    "trackId": item.track_id,
                    "trackPosition": track_positions[item.track_id],
                    "leftClipId": item.left_clip_id,
                    "rightClipId": item.right_clip_id,
                    "kind": item.kind.value,
                    "durationFrames": item.duration,
                    "boundaryFrame": clips[item.left_clip_id].timeline_end,
                }
                for item in state.transitions
            ]
        )
        self._marker_model.set_items(
            [
                {
                    "markerId": item.id,
                    "frame": item.frame,
                    "name": item.name,
                    "markerColor": item.color,
                }
                for item in state.markers
            ]
        )
        self._range_model.set_items(
            [
                {
                    "rangeId": item.id,
                    "startFrame": item.start_frame,
                    "endFrame": item.end_frame,
                    "name": item.name,
                    "rangeColor": item.color,
                }
                for item in state.ranges
            ]
        )
        self._schedule_preview_graph()

    @Slot(str, int, int, float, int, result="QVariantList")
    def waveformPeaks(
        self,
        asset_id: str,
        source_in: int,
        duration_frames: int,
        speed: float,
        pixel_width: int,
    ) -> list[float]:
        if not self._repository or not self._active_sequence_id or pixel_width <= 0:
            return []
        try:
            asset = self._repository.get_asset(asset_id)
            if not asset.waveform_path:
                return []
            path = Path(asset.waveform_path)
            if not path.is_absolute():
                path = self._repository.project_dir / path
            modified = path.stat().st_mtime_ns
            cached = self._waveform_cache.get(asset_id)
            if not cached or cached[0] != modified:
                cached = (modified, json.loads(path.read_text(encoding="utf-8")))
                self._waveform_cache[asset_id] = cached
            payload = cached[1]
            profile = self._repository.get_sequence(self._active_sequence_id).profile
            sample_rate = int(payload["sample_rate"])
            start_sample = round(source_in * sample_rate * profile.fps_denominator / profile.fps_numerator)
            source_frames = max(1, round(duration_frames * abs(speed)))
            end_sample = start_sample + round(
                source_frames * sample_rate * profile.fps_denominator / profile.fps_numerator
            )
            target_blocks = max(1, pixel_width // 2)
            block_sizes = sorted(int(value) for value in payload["levels"])
            required_block = max(1, (end_sample - start_sample) // target_blocks)
            block = min(block_sizes, key=lambda value: abs(value - required_block))
            peaks = payload["levels"][str(block)]
            first = max(0, start_sample // block)
            last = min(len(peaks), (end_sample + block - 1) // block)
            visible = peaks[first:last]
            if not visible:
                return []
            stride = max(1, (len(visible) + target_blocks - 1) // target_blocks)
            flattened: list[float] = []
            for offset in range(0, len(visible), stride):
                group = visible[offset : offset + stride]
                flattened.extend(
                    [
                        min(float(item[0]) for item in group),
                        max(float(item[1]) for item in group),
                    ]
                )
            return flattened
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
            return []

    def _refresh_tasks(self) -> None:
        if not self._tasks:
            self._task_model.set_items([])
            return
        self._task_model.set_items(
            [
                {
                    "taskId": task.id,
                    "name": task.name,
                    "displayName": self._localized_task_name(task.name),
                    "kind": task.kind.value,
                    "status": task.status.value,
                    "statusLabel": self._localized_task_status(task.status.value),
                    "progress": task.progress,
                    "messageCode": task.message_code,
                    "messageLabel": self._localized_task_message(task.message_code),
                    "error": task.error or "",
                    "artifacts": task.artifacts,
                }
                for task in reversed(self._tasks.repository.list())
            ]
        )

    def _refresh_documents(self) -> None:
        if not self._repository:
            self._document_model.set_items([])
            self._segment_model.set_items([])
            return
        documents = self._repository.list_subtitle_documents(self._selected_asset_id or None)
        self._document_model.set_items(
            [
                {
                    "documentId": document.id,
                    "assetId": document.asset_id,
                    "language": document.language,
                    "isSource": document.is_source,
                    "sourceDocumentId": document.source_document_id or "",
                    "segmentCount": len(self._repository.list_subtitle_segments(document.id)),
                }
                for document in documents
            ]
        )
        if self._selected_document_id and all(
            document.id != self._selected_document_id for document in documents
        ):
            self._selected_document_id = ""
        self._refresh_segments()

    def _refresh_segments(self) -> None:
        if not self._repository or not self._selected_document_id:
            self._segment_model.set_items([])
            return
        self._segment_model.set_items(
            [
                {
                    "segmentId": segment.id,
                    "startFrame": segment.start_frame,
                    "endFrame": segment.end_frame,
                    "text": segment.text,
                    "speaker": segment.speaker or "",
                    "confidence": segment.confidence if segment.confidence is not None else -1,
                }
                for segment in self._repository.list_subtitle_segments(self._selected_document_id)
            ]
        )

    def _refresh_highlights(self) -> None:
        if not self._repository:
            self._highlight_model.set_items([])
            return
        candidates = self._repository.list_highlights(self._selected_asset_id or None)
        self._highlight_model.set_items(
            [
                {
                    "highlightId": item.id,
                    "assetId": item.asset_id,
                    "startFrame": item.start_frame,
                    "endFrame": item.end_frame,
                    "title": item.title,
                    "reason": item.reason,
                    "score": item.score,
                }
                for item in candidates
            ]
        )

    def _refresh_audio_buses(self) -> None:
        if not self._repository or not self._active_sequence_id:
            self._audio_bus_model.set_items([])
            return
        self._audio_bus_model.set_items(
            [
                {
                    "busId": bus.id,
                    "name": bus.name,
                    "displayName": self._localized_default_name(bus.name),
                    "parentBusId": bus.parent_bus_id or "",
                    "gainDb": bus.gain_db,
                    "muted": bus.muted,
                    "solo": bus.solo,
                    "channelLayout": bus.channel_layout,
                }
                for bus in self._repository.list_audio_buses(self._active_sequence_id)
            ]
        )
        bus_ids = {bus.id for bus in self._repository.list_audio_buses(self._active_sequence_id)}
        if self._selected_audio_bus_id not in bus_ids:
            self._selected_audio_bus_id = ""
        self._refresh_audio_effects()

    def _refresh_audio_effects(self) -> None:
        if not self._repository or not self._selected_audio_bus_id:
            self._audio_effect_model.set_items([])
            self._selected_audio_effect_id = ""
            self._refresh_audio_effect_parameters()
            return
        effects = self._repository.list_audio_effects(self._selected_audio_bus_id)
        self._audio_effect_model.set_items(
            [
                {
                    "effectId": effect.id,
                    "busId": effect.bus_id,
                    "kind": effect.kind.value,
                    "position": effect.position,
                    "enabled": effect.enabled,
                    "parameters": effect.parameters,
                }
                for effect in effects
            ]
        )
        if self._selected_audio_effect_id not in {effect.id for effect in effects}:
            self._selected_audio_effect_id = ""
        self._refresh_audio_effect_parameters()

    def _refresh_audio_effect_parameters(self) -> None:
        if not self._repository or not self._selected_audio_bus_id or not self._selected_audio_effect_id:
            self._audio_effect_parameter_model.set_items([])
            return
        try:
            effect = next(
                effect
                for effect in self._repository.list_audio_effects(self._selected_audio_bus_id)
                if effect.id == self._selected_audio_effect_id
            )
        except StopIteration:
            self._audio_effect_parameter_model.set_items([])
            return
        parameter_schema = audio_effect_parameter_schema(effect.kind)
        self._audio_effect_parameter_model.set_items(
            [
                {
                    **spec,
                    "value": effect.parameters[spec["key"]],
                    "valueType": spec.get("valueType", "number"),
                    "minimum": float(parameter_schema[spec["key"]].get("minimum", 0.0)),
                    "maximum": float(parameter_schema[spec["key"]].get("maximum", 0.0)),
                }
                for spec in _AUDIO_PARAMETER_SPECS[effect.kind]
            ]
        )

    def _refresh_audio_metrics(self) -> None:
        metrics: dict = {}
        if self._repository and self._active_sequence_id:
            path = (
                self._repository.project_dir
                / "generated"
                / "audio"
                / f"{self._active_sequence_id}-loudness.json"
            )
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    metrics = {
                        "samplePeakDbfs": float(payload["sample_peak_dbfs"]),
                        "truePeakDbtp": float(payload["true_peak_dbtp"]),
                        "shortTermLufs": float(payload["short_term_lufs"]),
                        "integratedLufs": float(payload["integrated_lufs"]),
                    }
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    metrics = {}
        if self._audio_metrics != metrics:
            self._audio_metrics = metrics
        self.audioMetricsChanged.emit()

    def _schedule_preview_graph(self) -> None:
        if not self._repository or not self._editor or not self._editor.state.clips:
            if self._preview_graph_path:
                self._preview_graph_path = ""
                self.previewGraphChanged.emit()
            return
        self._preview_timer.start()

    @Slot()
    def _compile_preview_graph(self) -> None:
        if not self._repository or not self._editor:
            return
        try:
            state = self._editor.state
            if not state.clips:
                return
            destination = self._repository.project_dir / "cache" / "mlt" / f"{state.sequence.id}-preview.mlt"
            TimelineCompiler(self._repository).write(
                state,
                destination,
                use_proxies=self.settings.preview.preview_quality != "source",
                native_preview=True,
                prefer_sdr_preview_proxy=(
                    state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
                    and not self._hdr_preview_active
                ),
            )
            self._preview_graph_path = str(destination)
            self.previewGraphChanged.emit()
            if self._pending_preview_range is not None:
                start_frame, end_frame = self._pending_preview_range
                self._pending_preview_range = None
                self.previewRangeRequested.emit(start_frame, end_frame)
        except Exception as error:
            self.errorOccurred.emit(f"预览图编译失败：{error}")

    def _refresh_preview_subtitles(self) -> None:
        self._preview_subtitles = []
        self._subtitle_placement_model.set_items([])
        self._audio_metrics = {}
        self._waveform_cache.clear()
        if not self._repository or not self._active_sequence_id:
            return
        tracks = [
            track
            for track in self._repository.load_timeline(self._active_sequence_id).tracks
            if track.kind == TrackKind.SUBTITLE and track.enabled
        ]
        if not tracks:
            return
        segments = {
            segment.id: segment
            for document in self._repository.list_subtitle_documents()
            for segment in self._repository.list_subtitle_segments(document.id)
        }
        placement_rows = []
        for track in tracks:
            for placement in self._repository.list_subtitle_placements(track.id):
                segment = segments.get(placement.segment_id)
                if segment:
                    text = placement.text_override or segment.text
                    placement_rows.append(
                        {
                            "placementId": placement.id,
                            "trackId": placement.track_id,
                            "segmentId": placement.segment_id,
                            "startFrame": placement.start_frame,
                            "endFrame": placement.end_frame,
                            "text": text,
                            "sourceText": segment.text,
                            "hasOverride": placement.text_override is not None,
                        }
                    )
                    if track.id == tracks[0].id:
                        self._preview_subtitles.append(
                            (placement.start_frame, placement.end_frame, text)
                        )
        self._subtitle_placement_model.set_items(placement_rows)
        placement_ids = {item["placementId"] for item in placement_rows}
        if self._selected_subtitle_placement_id not in placement_ids:
            self._selected_subtitle_placement_id = ""

    @Slot(int, result=str)
    def subtitleTextAtFrame(self, frame: int) -> str:
        for start, end, text in self._preview_subtitles:
            if start <= frame < end:
                return text
            if start > frame:
                break
        return ""

    def _close_current(self) -> None:
        if self._tasks:
            self._tasks.shutdown(wait=True)
        if self._repository:
            self._repository.close()
        self._tasks = None
        self._workflows = None
        self._repository = None
        self._assets = None
        self._editor = None
        self._active_sequence_id = ""
        self._selected_asset_id = ""
        self._selected_clip_id = ""
        self._selected_document_id = ""
        self._selected_subtitle_placement_id = ""
        self._selected_highlight_id = ""
        self._selected_audio_bus_id = ""
        self._selected_audio_effect_id = ""
        self._selected_transition_id = ""
        self._selected_marker_id = ""
        self._selected_range_id = ""
        self._range_in_frame = None
        self._download_analysis = {}
        self.downloadAnalysisChanged.emit()
        self._preview_timer.stop()
        self._preview_graph_path = ""
        self._hdr_preview_active = False
        self._preview_subtitles = []
        if self._pending_profile_asset_id:
            self._pending_profile_asset_id = ""
            self._pending_profile_label = ""
            self.profileConfirmationChanged.emit()
        if self._pending_relink_asset_id:
            self._pending_relink_asset_id = ""
            self._pending_relink_path = ""
            self.relinkConfirmationChanged.emit()
        self._asset_model.set_items([])
        self._sequence_model.set_items([])
        self._track_model.set_items([])
        self._clip_model.set_items([])
        self._transition_model.set_items([])
        self._marker_model.set_items([])
        self._range_model.set_items([])
        self._task_model.set_items([])
        self._document_model.set_items([])
        self._segment_model.set_items([])
        self._subtitle_placement_model.set_items([])
        self._highlight_model.set_items([])
        self._audio_bus_model.set_items([])
        self._audio_effect_model.set_items([])
        self._audio_effect_parameter_model.set_items([])
        self.audioMetricsChanged.emit()
        self.workflowChanged.emit()
        self.previewGraphChanged.emit()

    def _require_writable(self) -> None:
        if not self._repository or not self._editor or not self._tasks:
            raise RuntimeError("请先打开一个项目")
        if self._repository.read_only:
            raise PermissionError("项目以只读方式打开")

    def _set_status(self, message: str) -> None:
        self._status_message = message
        self.statusChanged.emit()

    def _localized_default_name(self, name: str) -> str:
        language = self.settings.ui.language
        if language == "zh_CN":
            return name
        exact = {
            "en": {
                "主总线": "Master",
                "对白": "Dialogue",
                "音乐": "Music",
                "效果": "Effects",
            },
            "ja": {
                "主总线": "マスター",
                "对白": "台詞",
                "音乐": "音楽",
                "效果": "効果",
            },
        }[language]
        if name in exact:
            return exact[name]
        prefixes = {
            "en": {"视频 ": "Video ", "音频 ": "Audio ", "字幕 ": "Subtitles "},
            "ja": {"视频 ": "ビデオ ", "音频 ": "オーディオ ", "字幕 ": "字幕 "},
        }[language]
        for source, translated in prefixes.items():
            if name.startswith(source) and name[len(source) :].isdigit():
                return translated + name[len(source) :]
        return name

    def _localized_encoder_label(self, label_key: str) -> str:
        labels = {
            "h264_software": ("H.264 软件", "H.264 Software", "H.264 ソフトウェア"),
            "h264_nvidia": ("H.264 NVIDIA", "H.264 NVIDIA", "H.264 NVIDIA"),
            "h264_intel_qsv": ("H.264 Intel QSV", "H.264 Intel QSV", "H.264 Intel QSV"),
            "h264_amd_amf": ("H.264 AMD AMF", "H.264 AMD AMF", "H.264 AMD AMF"),
            "hevc_software": ("HEVC 软件", "HEVC Software", "HEVC ソフトウェア"),
            "hevc_nvidia": ("HEVC NVIDIA", "HEVC NVIDIA", "HEVC NVIDIA"),
            "hevc_intel_qsv": ("HEVC Intel QSV", "HEVC Intel QSV", "HEVC Intel QSV"),
            "hevc_amd_amf": ("HEVC AMD AMF", "HEVC AMD AMF", "HEVC AMD AMF"),
            "av1_svt_software": ("AV1 SVT 软件", "AV1 SVT Software", "AV1 SVT ソフトウェア"),
            "av1_nvidia": ("AV1 NVIDIA", "AV1 NVIDIA", "AV1 NVIDIA"),
            "av1_intel_qsv": ("AV1 Intel QSV", "AV1 Intel QSV", "AV1 Intel QSV"),
            "av1_amd_amf": ("AV1 AMD AMF", "AV1 AMD AMF", "AV1 AMD AMF"),
            "prores_software": ("ProRes 软件", "ProRes Software", "ProRes ソフトウェア"),
        }
        language_index = {"zh_CN": 0, "en": 1, "ja": 2}[self.settings.ui.language]
        return labels[label_key][language_index]

    def _localized_task_name(self, name: str) -> str:
        language = self.settings.ui.language
        if language == "zh_CN":
            return name
        names = {
            "en": {
                "生成代理": "Generate Proxy",
                "生成波形": "Generate Waveform",
                "转录字幕": "Transcribe",
                "翻译字幕": "Translate",
                "AI 高光分析": "AI Highlight Analysis",
                "分析下载链接": "Analyze Download Link",
                "下载视频": "Download Video",
                "测量序列响度": "Measure Sequence Loudness",
            },
            "ja": {
                "生成代理": "プロキシを生成",
                "生成波形": "波形を生成",
                "转录字幕": "文字起こし",
                "翻译字幕": "翻訳",
                "AI 高光分析": "AI ハイライト分析",
                "分析下载链接": "ダウンロードリンクを解析",
                "下载视频": "動画をダウンロード",
                "测量序列响度": "シーケンスラウドネスを測定",
            },
        }[language]
        if name in names:
            return names[name]
        if name.startswith("导出 "):
            return ("Export " if language == "en" else "書き出し ") + name[3:]
        return name

    def _localized_task_status(self, status: str) -> str:
        labels = {
            "zh_CN": {
                "pending": "等待中",
                "running": "运行中",
                "paused": "已暂停",
                "completed": "已完成",
                "failed": "失败",
                "cancelled": "已取消",
            },
            "en": {
                "pending": "Pending",
                "running": "Running",
                "paused": "Paused",
                "completed": "Completed",
                "failed": "Failed",
                "cancelled": "Cancelled",
            },
            "ja": {
                "pending": "待機中",
                "running": "実行中",
                "paused": "一時停止",
                "completed": "完了",
                "failed": "失敗",
                "cancelled": "キャンセル済み",
            },
        }
        return labels[self.settings.ui.language].get(status, status)

    def _localized_task_message(self, code: str) -> str:
        messages = {
            "queued": ("已排队", "Queued", "キューに追加"),
            "running": ("正在运行", "Running", "実行中"),
            "completed": ("已完成", "Completed", "完了"),
            "failed": ("任务失败", "Task failed", "タスク失敗"),
            "cancelled": ("已取消", "Cancelled", "キャンセル済み"),
            "interrupted_by_restart": ("因应用重启而暂停", "Paused after restart", "再起動により一時停止"),
            "downloading": ("正在下载", "Downloading", "ダウンロード中"),
            "postprocessing": ("正在整理下载文件", "Post-processing", "後処理中"),
            "loading_asr_model": (
                "正在加载转录模型",
                "Loading transcription model",
                "文字起こしモデルを読込中",
            ),
            "transcribing": ("正在转录", "Transcribing", "文字起こし中"),
            "transcription_completed": ("转录完成", "Transcription complete", "文字起こし完了"),
            "translating": ("正在翻译", "Translating", "翻訳中"),
            "translation_completed": ("翻译完成", "Translation complete", "翻訳完了"),
            "highlight_analyzing": ("正在分析高光", "Analyzing highlights", "ハイライトを分析中"),
            "highlight_completed": ("高光分析完成", "Highlight analysis complete", "ハイライト分析完了"),
            "proxy_preparing": ("正在准备代理", "Preparing proxy", "プロキシを準備中"),
            "proxy_verifying": ("正在验证代理", "Verifying proxy", "プロキシを検証中"),
            "waveform_decoding": ("正在生成波形", "Generating waveform", "波形を生成中"),
            "waveform_verifying": ("正在验证波形", "Verifying waveform", "波形を検証中"),
            "export_compiling": ("正在编译时间线", "Compiling timeline", "タイムラインをコンパイル中"),
            "export_verifying": ("正在验证导出文件", "Verifying export", "書き出しを検証中"),
            "download_analyzing": (
                "正在分析下载链接",
                "Analyzing download link",
                "ダウンロードリンクを解析中",
            ),
            "download_analysis_ready": ("下载分析完成", "Download analysis ready", "ダウンロード解析完了"),
            "audio_analysis_compiling": (
                "正在编译音频图",
                "Compiling audio graph",
                "音声グラフをコンパイル中",
            ),
            "audio_analysis_measuring_loudness": ("正在测量响度", "Measuring loudness", "ラウドネスを測定中"),
            "audio_analysis_measuring_peak": ("正在测量峰值", "Measuring peaks", "ピークを測定中"),
            "audio_analysis_complete": ("响度测量完成", "Loudness analysis complete", "ラウドネス測定完了"),
            "workflow_cancelled": ("工作流已取消", "Workflow cancelled", "ワークフローをキャンセル"),
            "workflow_complete": ("工作流已完成", "Workflow complete", "ワークフロー完了"),
        }
        language_index = {"zh_CN": 0, "en": 1, "ja": 2}[self.settings.ui.language]
        if code in messages:
            return messages[code][language_index]
        if code.startswith("workflow_") and code.endswith("_ready"):
            return ("上一阶段已完成", "Ready for confirmation", "確認待ち")[language_index]
        return code.replace("_", " ")

    def _timeline_snap_targets(self, clip_id: str, playhead_frame: int) -> list[int]:
        targets = [0, max(0, playhead_frame)]
        if not self._editor:
            return targets
        state = self._editor.state
        for clip in state.clips:
            if clip.id != clip_id:
                targets.extend([clip.timeline_start, clip.timeline_end])
        targets.extend(marker.frame for marker in state.markers)
        for item in state.ranges:
            targets.extend([item.start_frame, item.end_frame])
        return targets

    def _start_download_workflow(
        self,
        url: str,
        resolution: str,
        playlist_items: str | None,
    ) -> None:
        run = self._workflows.begin(
            sequence_id=self._active_sequence_id,
            stage=WorkflowStage.DOWNLOAD,
            payload={
                "url": url,
                "resolution": resolution,
                "playlist_items": playlist_items,
            },
            running=True,
        )
        task = self._create_task(
            TaskKind.DOWNLOAD,
            "下载视频",
            {
                "url": url,
                "resolution": resolution,
                "playlist_items": playlist_items,
                "workflow_run_id": run.id,
                "workflow_stage": run.stage.value,
            },
            sequence_id=run.sequence_id,
        )
        self._workflows.mark_running(run.id, task_ids=[task.id])
        self.workflowChanged.emit()

    @staticmethod
    def _snap_tolerance_frames(pixels_per_frame: float) -> int:
        return max(1, round(8.0 / max(0.01, pixels_per_frame)))

    def _active_llm_provider(self):
        try:
            return next(provider for provider in self.settings.llm_providers if provider.enabled)
        except StopIteration as error:
            raise RuntimeError("请先在设置中配置并启用一个 LLM 提供商") from error

    @staticmethod
    def _default_export_preset(
        export_format: ExportFormat,
        color_mode: ColorMode,
        fps: float,
    ) -> ExportPreset:
        if color_mode == ColorMode.HDR10_BT2020_PQ and export_format == ExportFormat.H264:
            raise ValueError("HDR10 序列不能导出 H.264，请选择 HEVC、AV1 或 ProRes")
        values = {
            ExportFormat.H264: ("mp4", "libx264", "yuv420p", 18.0, {}),
            ExportFormat.HEVC: (
                "mp4",
                "libx265",
                "yuv420p10le" if color_mode == ColorMode.HDR10_BT2020_PQ else "yuv420p",
                20.0,
                {},
            ),
            ExportFormat.AV1: (
                "mkv",
                "libsvtav1",
                "yuv420p10le" if color_mode == ColorMode.HDR10_BT2020_PQ else "yuv420p",
                24.0,
                {},
            ),
            ExportFormat.PRORES: ("mov", "prores_ks", "yuv422p10le", 0.0, {"profile": 3}),
            ExportFormat.AUDIO: ("flac", None, None, 0.0, {}),
        }
        container, video_codec, pixel_format, quality, advanced = values[export_format]
        return ExportPreset(
            name=f"{export_format.value.upper()} 高质量",
            format=export_format,
            container=container,
            video_codec=video_codec,
            audio_codec="flac" if export_format == ExportFormat.AUDIO else "aac",
            pixel_format=pixel_format,
            quality_value=quality,
            preset="8" if export_format == ExportFormat.AV1 else "medium",
            gop_frames=max(1, round(fps * 2)),
            advanced=advanced,
        )

    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        path = url.toLocalFile() if url.isLocalFile() else value
        return Path(path).expanduser().resolve()

    @staticmethod
    def _safe_project_name(name: str) -> str:
        invalid = '<>:"/\\|?*'
        sanitized = "".join("_" if character in invalid else character for character in name).strip(" .")
        if not sanitized:
            raise ValueError("项目名称无效")
        return sanitized
