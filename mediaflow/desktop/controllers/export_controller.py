from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.desktop.editor_planning import (
    export_preset_for_options,
    next_default_export_output,
)
from mediaflow.desktop.presentation_export import (
    encoder_label,
    export_format_options,
    no_subtitle_burn_label,
)
from mediaflow.desktop.presentation_messages import system_name
from mediaflow.domain.enums import (
    ColorMode,
    TrackKind,
    WorkflowStage,
)
from mediaflow.domain.storage_names import export_quality_directory
from mediaflow.domain.task_commands import (
    ExportSequenceCommand,
    WorkflowTaskLink,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import ExportControllerScope


class ExportController(ControllerFacet[ExportControllerScope]):
    projectStateChanged = Signal()
    tasksChanged = Signal()
    settingsChanged = Signal()
    workflowChanged = Signal()
    exportCapabilityChanged = Signal()
    errorOccurred = Signal(str)

    @Property(list, constant=True)
    def subtitleFontOptions(self) -> list[dict]:
        return self._session._api.subtitle_font_options()

    @Property(list, notify=settingsChanged)
    def encoderPolicyOptions(self) -> list[dict]:
        return [
            {
                **item,
                "label": encoder_label(item["labelKey"]),
                "available": True,
            }
            for item in self._session.state.presentation.encoder_policy_options
        ]

    @Property(list, notify=projectStateChanged)
    def exportFormatOptions(self) -> list[dict]:
        color_mode = (
            self._session.state.binding.require_timeline().state.sequence.profile.color_mode
            if self._session.state.binding.timeline
            else ColorMode.SDR_BT709
        )
        return export_format_options(color_mode)

    @Property(list, notify=projectStateChanged)
    def subtitleTrackOptions(self) -> list[dict]:
        values = [{"label": no_subtitle_burn_label(), "value": ""}]
        if not self._session.state.binding.timeline:
            return values
        values.extend(
            {"label": system_name(track.name), "value": track.id}
            for track in self._session.state.binding.require_timeline().state.tracks
            if track.kind == TrackKind.SUBTITLE and track.enabled
        )
        return values

    @Property(dict, notify=projectStateChanged)
    def exportPresetData(self) -> dict:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return {}
        preset = (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .export_preset
        )
        return preset.model_dump(mode="json") if preset else {}

    @Property(str, notify=projectStateChanged)
    def defaultExportDirectory(self) -> str:
        return (
            str(self._session.state.binding.require_current().project_dir / "exports")
            if self._session.state.binding.current
            else ""
        )

    @Property(bool, notify=exportCapabilityChanged)
    def canExportSequence(self) -> bool:
        return self._session._active_sequence_has_renderable_content()

    @Property(list, notify=tasksChanged)
    def exportHistory(self) -> list[dict]:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return []
        values: list[dict] = []
        for record in self._session.state.binding.require_current().list_export_history(
            self._session.state.binding.active_sequence_id
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
                    "requestedVideoCodec": recovery_details.get("requested_video_codec", ""),
                    "actualVideoCodec": recovery_details.get("actual_video_codec", ""),
                    "checks": [check.model_dump(mode="json") for check in record.quality.checks],
                    "proofFrames": list(record.quality.proof_frames),
                    "reportPath": str(
                        export_quality_directory(
                            self._session.state.binding.require_current().project_dir,
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
        output = self._session.state.binding.require_current().export_fcpxml(
            self._session.state.binding.active_sequence_id,
            self._session._local_path(path_url),
            overwrite=True,
        )
        self._session._set_status("已导出 FCPXML：%1", output.name)

    @Slot(str, str, "QVariantMap")
    @report_ui_errors
    def exportSequenceToDefaultLocation(
        self,
        format_name: str,
        suffix: str,
        options: dict,
    ) -> None:
        self._session._require_exportable_sequence()
        output = next_default_export_output(self._session, suffix)
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
        preset = export_preset_for_options(self._session, format_name, options)
        if preset.burn_subtitle_track_id and preset.subtitle_style is not None:
            self._session.state.binding.require_timeline().set_subtitle_track_style(
                preset.burn_subtitle_track_id,
                preset.subtitle_style,
            )
        self._session.state.binding.require_current().save_sequence_export_preset(
            self._session.state.binding.active_sequence_id, preset
        )
        self._session.updates.commit(project=True)
        workflow = self._session.tasks.active_workflow()
        workflow_link = (
            WorkflowTaskLink(run_id=workflow.id, stage=workflow.stage)
            if workflow and workflow.stage == WorkflowStage.EXPORT
            else None
        )
        task = self._session.tasks.start(
            ExportSequenceCommand(
                output_path=str(output),
                sequence_id=self._session.state.binding.active_sequence_id,
                format=preset.format,
                preset=preset,
                overwrite=overwrite,
                workflow=workflow_link,
            ),
            sequence_id=self._session.state.binding.active_sequence_id,
        )
        if task and workflow_link:
            self._session.state.binding.require_current().attach_export_task(workflow.id, task.id)
            self._session.updates.commit(workflow=True)
