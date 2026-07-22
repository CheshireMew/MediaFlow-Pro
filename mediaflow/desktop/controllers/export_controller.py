from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.application.export_catalog import default_export_preset
from mediaflow.desktop.presentation_catalogs import (
    encoder_label,
    export_format_options,
    no_subtitle_burn_label,
    system_name,
)
from mediaflow.domain.enums import (
    ColorMode,
    ExportFormat,
    TrackKind,
    WorkflowStage,
)
from mediaflow.domain.exports import SubtitleStyle, WatermarkOverlay
from mediaflow.domain.task_commands import (
    ExportSequenceCommand,
    WorkflowTaskLink,
)

from .controller_facet import ControllerFacet


class ExportController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    waveformDataChanged = Signal(str)
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()

    @Property("QVariantList", constant=True)
    def subtitleFontOptions(self) -> list[dict]:
        return self._api.subtitle_font_options()

    @Property("QVariantList", notify=settingsChanged)
    def videoEncoderOptions(self) -> list[dict]:
        return [{**item, "label": encoder_label(item["labelKey"])} for item in self._video_encoder_options]

    @Property("QVariantList", notify=projectStateChanged)
    def exportFormatOptions(self) -> list[dict]:
        color_mode = self._editor.state.sequence.profile.color_mode if self._editor else ColorMode.SDR_BT709
        return export_format_options(color_mode)

    @Property("QVariantList", notify=projectStateChanged)
    def subtitleTrackOptions(self) -> list[dict]:
        values = [{"label": no_subtitle_burn_label(), "value": ""}]
        if not self._editor:
            return values
        values.extend(
            {"label": system_name(track.name), "value": track.id}
            for track in self._editor.state.tracks
            if track.kind == TrackKind.SUBTITLE and track.enabled
        )
        return values

    @Property("QVariantMap", notify=projectStateChanged)
    def exportPresetData(self) -> dict:
        if not self._documents or not self._active_sequence_id:
            return {}
        preset = self._documents.get_sequence(self._active_sequence_id).export_preset
        return preset.model_dump(mode="json") if preset else {}

    @Property(str, notify=projectStateChanged)
    def defaultExportDirectory(self) -> str:
        return str(self._documents.project_dir / "exports") if self._documents else ""

    @Slot(str)
    def exportH264(self, path_url: str) -> None:
        self.exportSequence("h264", path_url)

    @Slot(str, str)
    def exportSequence(self, format_name: str, path_url: str) -> None:
        self.exportSequenceWithOptions(format_name, path_url, {})

    @Slot(str, str, "QVariantMap")
    def exportSequenceToDefaultLocation(
        self,
        format_name: str,
        suffix: str,
        options: dict,
    ) -> None:
        try:
            self._require_writable()
            sequence = self._documents.get_sequence(self._active_sequence_id)
            extension = "".join(character for character in suffix if character.isalnum()).lower()
            if not extension:
                raise ValueError("导出格式缺少有效的文件扩展名")
            directory = Path(self.defaultExportDirectory)
            base_name = self._safe_project_name(sequence.name)
            reserved_outputs = {
                Path(task.command.output_path).resolve()
                for task in self._task_view.values()
                if isinstance(task.command, ExportSequenceCommand) and not task.status.is_terminal
            }
            sequence_number = 1
            while True:
                numbered_suffix = "" if sequence_number == 1 else f" ({sequence_number})"
                output = directory / f"{base_name}{numbered_suffix}.{extension}"
                if not output.exists() and output.resolve() not in reserved_outputs:
                    break
                sequence_number += 1
            self.exportSequenceWithOptions(format_name, str(output), options)
        except Exception as error:
            self.errorOccurred.emit(str(error))

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
            preset = default_export_preset(
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
            if isinstance(options.get("subtitleStyle"), dict):
                updates["subtitle_style"] = SubtitleStyle.model_validate(options["subtitleStyle"])
            if isinstance(options.get("watermark"), dict):
                updates["watermark"] = WatermarkOverlay.model_validate(options["watermark"])
            preset = type(preset).model_validate(
                {**preset.model_dump(mode="python"), **updates}
            )
            self._documents.save_sequence_export_preset(self._active_sequence_id, preset)
            self.projectStateChanged.emit()
            workflow = self._active_workflow_run()
            workflow_link = (
                WorkflowTaskLink(run_id=workflow.id, stage=workflow.stage)
                if workflow and workflow.stage == WorkflowStage.EXPORT
                else None
            )
            task = self._start_task(
                ExportSequenceCommand(
                    output_path=str(output),
                    sequence_id=self._active_sequence_id,
                    format=export_format,
                    preset=preset,
                    workflow=workflow_link,
                ),
                sequence_id=self._active_sequence_id,
            )
            if task and workflow_link:
                self._workflows.attach_export_task(workflow.id, task.id)
                self.workflowChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))
