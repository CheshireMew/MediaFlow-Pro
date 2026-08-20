from __future__ import annotations

from datetime import datetime
from typing import cast

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtGui import QGuiApplication

from mediaflow.automation.request_factory import AutomationRequestFactory
from mediaflow.desktop.editor_planning import (
    current_transcription_plan,
    export_preset_for_options,
    next_default_export_output,
    web_scene_time_for_frame,
)
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.task_commands import DiagnosticsBundleCommand

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import AutomationControllerScope
from .web_editor_context import coerce_web_descriptor_value, find_web_descriptor


class AutomationController(ControllerFacet[AutomationControllerScope]):
    requestPreviewChanged = Signal()
    requestPrepared = Signal()
    projectStateChanged = Signal()

    def __init__(
        self,
        session: AutomationControllerScope,
        *,
        web,
    ):
        super().__init__(session)
        self.setObjectName("automationController")
        self._factory = AutomationRequestFactory()
        self._web = web
        self._request_preview = ""
        self._request_title = ""

    @Property(str, notify=requestPreviewChanged)
    def requestPreviewJson(self) -> str:
        return self._request_preview

    @Property(str, notify=requestPreviewChanged)
    def requestTitle(self) -> str:
        return self._request_title

    @Property(str, constant=True)
    def executionCommand(self) -> str:
        return "mediaflow-cli execute --request request.json"

    @Property(str, notify=projectStateChanged)
    def diagnosticsDefaultPath(self) -> str:
        current = self._session.state.binding.current
        if current is None:
            return ""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return str(current.project_dir / "exports" / f"diagnostics-{stamp}.zip")

    def _copy(self, title: str, operation: str, arguments: dict) -> None:
        current = self._session.state.binding.current
        if current is None:
            raise RuntimeError("请先打开一个项目")
        request = self._factory.create(
            operation,
            arguments,
            project_path=current.project_dir,
            content_revision=current.known_content_revision,
            actor=current.actor_identity,
            client_id=f"mediaflow-desktop:{current.actor_id}",
        )
        rendered = self._factory.canonical_json(request)
        application = cast(QGuiApplication | None, QGuiApplication.instance())
        if application is None:
            raise RuntimeError("桌面剪贴板尚未初始化")
        application.clipboard().setText(rendered)
        self._request_title = title
        self._request_preview = rendered
        self.requestPreviewChanged.emit()
        self.requestPrepared.emit()

    @Slot(str, str, "QVariantMap")
    @report_ui_errors
    def copyCurrentExportRequest(
        self,
        format_name: str,
        suffix: str,
        options: dict,
    ) -> None:
        self._session._require_exportable_sequence()
        output = next_default_export_output(self._session, suffix)
        preset = export_preset_for_options(self._session, format_name, options)
        self._copy(
            "导出当前序列",
            "export.sequence",
            {
                "sequence_id": self._session.state.binding.active_sequence_id,
                "output_path": str(output),
                "format": preset.format.value,
                "preset": preset.model_dump(mode="json"),
                "overwrite": False,
            },
        )

    @Slot(str, str, str, int)
    @report_ui_errors
    def copyCurrentTranscriptionRequest(
        self,
        model: str,
        device: str,
        language: str,
        parallel_chunks: int,
    ) -> None:
        self._session._require_writable()
        selected_asr = AsrSettings.model_validate(
            {
                **self._session.state.service_settings.asr.model_dump(mode="python"),
                "model": model.strip(),
                "device": device,
                "language": language.strip() or "auto",
                "parallel_chunks": parallel_chunks,
            }
        )
        plan = current_transcription_plan(self._session, selected_asr)
        self._copy(
            "转录当前时间轴",
            "transcript.sequence.transcribe",
            {
                "sequence_id": plan.sequence_id,
                "asr": selected_asr.model_dump(mode="json"),
                "start_frame": plan.timeline_start_frame,
                "end_frame": plan.timeline_end_frame,
            },
        )

    @Slot(str, str, "QVariant")
    @report_ui_errors
    def copyWebFieldUpdateRequest(self, target: str, source_id: str, value) -> None:
        context = self._web.context_snapshot()
        descriptor = find_web_descriptor(context.edit_document, target, source_id)
        typed = coerce_web_descriptor_value(descriptor, value)
        base = {
            "sequence_id": self._session.state.binding.active_sequence_id,
            "clip_id": context.clip_id,
            "expected_revision": int(context.persistent_state.get("revision", 0)),
        }
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            operation = "web.clip.update"
            arguments = {
                **base,
                "scene_id": context.active_scene_id,
                "updates": {layer_id: {field: typed}},
                "actor": "human",
            }
        elif target == "parameter":
            operation = "web.clip.parameter.update"
            arguments = {
                **base,
                "scene_id": context.active_scene_id,
                "parameter_id": source_id,
                "value": typed,
                "actor": "human",
            }
        elif target == "theme":
            operation = "web.clip.theme.update"
            arguments = {**base, "changes": {source_id: typed}}
        elif target == "data":
            operation = "web.clip.data.update"
            arguments = {
                **base,
                "scene_id": context.active_scene_id,
                "values": {source_id: typed},
            }
        else:
            raise ValueError(f"未知的网页编辑目标：{target}")
        self._copy("修改网页字段", operation, arguments)

    @Slot(str, str, "QVariant", str, int)
    @report_ui_errors
    def copyWebKeyframeSetRequest(
        self,
        target: str,
        source_id: str,
        value,
        easing: str,
        frame: int,
    ) -> None:
        context = self._web.context_snapshot()
        descriptor = find_web_descriptor(context.edit_document, target, source_id)
        typed = coerce_web_descriptor_value(descriptor, value)
        scene_id, time_ms = web_scene_time_for_frame(self._session, context, frame)
        base = {
            "sequence_id": self._session.state.binding.active_sequence_id,
            "clip_id": context.clip_id,
            "scene_id": scene_id,
            "time_ms": time_ms,
            "value": typed,
            "easing": {"kind": easing or "linear"},
            "expected_revision": int(context.persistent_state.get("revision", 0)),
            "actor": "human",
        }
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            operation = "web.clip.keyframe.set"
            arguments = {**base, "layer_id": layer_id, "field": field}
        elif target == "parameter":
            operation = "web.clip.parameter.keyframe.set"
            arguments = {**base, "parameter_id": source_id}
        else:
            raise ValueError("当前字段不能创建关键帧")
        self._copy("设置网页关键帧", operation, arguments)

    @Slot(str, str, int)
    @report_ui_errors
    def copyWebKeyframeRemoveRequest(
        self,
        target: str,
        source_id: str,
        frame: int,
    ) -> None:
        context = self._web.context_snapshot()
        scene_id, time_ms = web_scene_time_for_frame(self._session, context, frame)
        base = {
            "sequence_id": self._session.state.binding.active_sequence_id,
            "clip_id": context.clip_id,
            "scene_id": scene_id,
            "time_ms": time_ms,
            "expected_revision": int(context.persistent_state.get("revision", 0)),
        }
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            operation = "web.clip.keyframe.remove"
            arguments = {**base, "layer_id": layer_id, "field": field}
        elif target == "parameter":
            operation = "web.clip.parameter.keyframe.remove"
            arguments = {**base, "parameter_id": source_id}
        else:
            raise ValueError("当前字段没有关键帧")
        self._copy("移除网页关键帧", operation, arguments)

    @Slot()
    @report_ui_errors
    def copyWebRebindPlanRequest(self) -> None:
        context = self._web.context_snapshot()
        source = self._session.state.web_delivery.pending_rebind_source
        if not context.asset_id or not source:
            raise ValueError("请先检查新版网页包")
        self._copy(
            "检查网页换版计划",
            "web.asset.rebind.plan",
            {"asset_id": context.asset_id, "source": source},
        )

    @Slot()
    @report_ui_errors
    def copyWebRebindCommitRequest(self) -> None:
        context = self._web.context_snapshot()
        delivery = self._session.state.web_delivery
        if not context.asset_id or not delivery.pending_rebind_source:
            raise ValueError("请先检查新版网页包")
        self._copy(
            "提交网页换版计划",
            "web.asset.rebind.commit",
            {
                "asset_id": context.asset_id,
                "source": delivery.pending_rebind_source,
                "plan_digest": str(delivery.rebind_plan.get("plan_digest") or ""),
                "resolutions": dict(delivery.rebind_resolutions),
            },
        )

    @Slot()
    @report_ui_errors
    def copyProjectHandoffRequest(self) -> None:
        self._copy(
            "检查项目交接状态",
            "project.handoff.inspect",
            {"sequence_id": self._session.state.binding.active_sequence_id},
        )

    @Slot(str, "QVariantList", bool)
    @report_ui_errors
    def copyDiagnosticsBundleRequest(
        self,
        output_path: str,
        task_ids: list,
        overwrite: bool,
    ) -> None:
        output = self._session._local_path(output_path)
        self._copy(
            "生成诊断包",
            "diagnostics.bundle.create",
            {
                "output_path": str(output),
                "task_ids": [str(value) for value in task_ids],
                "overwrite": overwrite,
            },
        )

    @Slot(str, bool)
    @report_ui_errors
    def createDiagnosticsBundle(self, output_path: str, overwrite: bool) -> None:
        self._session._require_writable()
        output = self._session._local_path(output_path)
        self._session.tasks.start(
            DiagnosticsBundleCommand(
                output_path=str(output),
                overwrite=overwrite,
            ),
            sequence_id=self._session.state.binding.active_sequence_id,
        )
        self._session._set_status("诊断包任务已加入任务中心")
