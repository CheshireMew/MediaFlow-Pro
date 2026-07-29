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
from mediaflow.domain.storage_names import (
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    export_quality_directory,
    safe_child_path,
)
from mediaflow.domain.task_commands import (
    ExportSequenceCommand,
    WorkflowTaskLink,
)

from .controller_facet import ControllerFacet, report_ui_errors


class ExportController(ControllerFacet):
    projectStateChanged = Signal()
    tasksChanged = Signal()
    settingsChanged = Signal()
    workflowChanged = Signal()
    exportCapabilityChanged = Signal()
    errorOccurred = Signal(str)

    @Property("QVariantList", constant=True)
    def subtitleFontOptions(self) -> list[dict]:
        return self._session._api.subtitle_font_options()

    @Property("QVariantList", notify=settingsChanged)
    def videoEncoderOptions(self) -> list[dict]:
        return [
            {**item, "label": encoder_label(item["labelKey"])}
            for item in self._session.presentation.video_encoder_options
        ]

    @Property("QVariantList", notify=projectStateChanged)
    def exportFormatOptions(self) -> list[dict]:
        color_mode = (
            self._session.binding.timeline.state.sequence.profile.color_mode
            if self._session.binding.timeline
            else ColorMode.SDR_BT709
        )
        return export_format_options(color_mode)

    @Property("QVariantList", notify=projectStateChanged)
    def subtitleTrackOptions(self) -> list[dict]:
        values = [{"label": no_subtitle_burn_label(), "value": ""}]
        if not self._session.binding.timeline:
            return values
        values.extend(
            {"label": system_name(track.name), "value": track.id}
            for track in self._session.binding.timeline.state.tracks
            if track.kind == TrackKind.SUBTITLE and track.enabled
        )
        return values

    @Property("QVariantMap", notify=projectStateChanged)
    def exportPresetData(self) -> dict:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return {}
        preset = self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).export_preset
        return preset.model_dump(mode="json") if preset else {}

    @Property(str, notify=projectStateChanged)
    def defaultExportDirectory(self) -> str:
        return (
            str(self._session.binding.current.project_dir / "exports")
            if self._session.binding.current
            else ""
        )

    @Property(bool, notify=exportCapabilityChanged)
    def canExportSequence(self) -> bool:
        return self._session._active_sequence_has_renderable_content()

    @Property("QVariantList", notify=tasksChanged)
    def exportHistory(self) -> list[dict]:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return []
        values: list[dict] = []
        for record in self._session.binding.current.list_export_history(
            self._session.binding.active_sequence_id
        ):
            warnings = sum(check.status == "warning" for check in record.quality.checks)
            failures = sum(check.status == "failed" for check in record.quality.checks)
            encoder_recovery = next(
                (check for check in record.quality.checks if check.key == "encoder_recovery"),
                None,
            )
            recovery_details = encoder_recovery.details if encoder_recovery else {}
            values.append(
                {
                    "recordId": record.id,
                    "outputPath": record.output_path,
                    "outputName": Path(record.output_path).name,
                    "format": record.format.value.upper(),
                    "qualityPassed": record.quality.passed,
                    "warningCount": warnings,
                    "failureCount": failures,
                    "encoderFallbackUsed": encoder_recovery is not None,
                    "requestedVideoCodec": recovery_details.get(
                        "requested_video_codec", ""
                    ),
                    "actualVideoCodec": recovery_details.get("actual_video_codec", ""),
                    "checks": [check.model_dump(mode="json") for check in record.quality.checks],
                    "proofFrames": list(record.quality.proof_frames),
                    "reportPath": str(
                        export_quality_directory(
                            self._session.binding.current.project_dir,
                            record.id,
                        )
                        / "report.json"
                    ),
                    "sha256": record.quality.sha256,
                    "contentRevision": record.content_revision,
                    "createdAt": record.created_at,
                }
            )
        return values

    @Slot(str)
    @report_ui_errors
    def exportFcpxml(self, path_url: str) -> None:
        self._session._require_exportable_sequence()
        output = self._session.binding.current.export_fcpxml(
            self._session.binding.active_sequence_id,
            self._session._local_path(path_url),
            overwrite=True,
        )
        self._session._set_status(f"已导出 FCPXML：{output.name}")

    @Slot(str, str, "QVariantMap")
    @report_ui_errors
    def exportSequenceToDefaultLocation(
        self,
        format_name: str,
        suffix: str,
        options: dict,
    ) -> None:
        self._session._require_exportable_sequence()
        sequence = self._session.binding.current.get_sequence(self._session.binding.active_sequence_id)
        extension = "".join(character for character in suffix if character.isalnum()).lower()
        if not extension:
            raise ValueError("导出格式缺少有效的文件扩展名")
        directory = Path(self.defaultExportDirectory)
        reserved_outputs = {
            Path(task.command.output_path).resolve()
            for task in self._session.task_state.items.values()
            if isinstance(task.command, ExportSequenceCommand) and not task.status.is_terminal
        }
        sequence_number = 1
        while True:
            numbered_suffix = "" if sequence_number == 1 else f" ({sequence_number})"
            output = safe_child_path(
                directory,
                sequence.name,
                suffix=f"{numbered_suffix}.{extension}",
                required_sibling_component_utf16_units=(
                    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS
                ),
            )
            if not output.exists() and output.resolve() not in reserved_outputs:
                break
            sequence_number += 1
        self._export_sequence_with_options(
            format_name,
            str(output),
            options,
            overwrite=False,
        )

    @Slot(str, str, "QVariantMap")
    @report_ui_errors
    def exportSequenceWithOptions(
        self,
        format_name: str,
        path_url: str,
        options: dict,
    ) -> None:
        self._session._require_exportable_sequence()
        self._export_sequence_with_options(
            format_name,
            path_url,
            options,
            overwrite=True,
        )

    def _export_sequence_with_options(
        self,
        format_name: str,
        path_url: str,
        options: dict,
        *,
        overwrite: bool,
    ) -> None:
        output = self._session._local_path(path_url)
        export_format = ExportFormat(format_name)
        state = self._session.binding.timeline.state
        preset = default_export_preset(
            export_format,
            state.sequence.profile.color_mode,
            state.sequence.profile.fps,
        )
        updates: dict = {}
        allowed_video_codecs = {
            str(option["value"])
            for option in self._session.presentation.video_encoder_options
            if export_format.value in option["formats"]
        }
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
            value = options.get(source_name)
            if value in {"", None}:
                continue
            if source_name == "videoCodec" and str(value) not in allowed_video_codecs:
                continue
            updates[target_name] = value
        if isinstance(options.get("advanced"), dict):
            updates["advanced"] = options["advanced"]
        if isinstance(options.get("subtitleStyle"), dict):
            updates["subtitle_style"] = SubtitleStyle.model_validate(options["subtitleStyle"])
        if isinstance(options.get("watermark"), dict):
            updates["watermark"] = WatermarkOverlay.model_validate(options["watermark"])
        preset = type(preset).model_validate({**preset.model_dump(mode="python"), **updates})
        self._session.binding.current.save_sequence_export_preset(
            self._session.binding.active_sequence_id, preset
        )
        self._session.events.projectStateChanged.emit()
        workflow = self._session.tasks.active_workflow()
        workflow_link = (
            WorkflowTaskLink(run_id=workflow.id, stage=workflow.stage)
            if workflow and workflow.stage == WorkflowStage.EXPORT
            else None
        )
        task = self._session.tasks.start(
            ExportSequenceCommand(
                output_path=str(output),
                sequence_id=self._session.binding.active_sequence_id,
                format=export_format,
                preset=preset,
                overwrite=overwrite,
                workflow=workflow_link,
            ),
            sequence_id=self._session.binding.active_sequence_id,
        )
        if task and workflow_link:
            self._session.binding.current.attach_export_task(workflow.id, task.id)
            self._session.events.workflowChanged.emit()
