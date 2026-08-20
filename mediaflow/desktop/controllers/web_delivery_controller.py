from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QCoreApplication, QUrl, Signal, Slot

from mediaflow.domain.task_commands import ExportWebClipCommand
from mediaflow.domain.web_exports import (
    WEB_EXPORT_FORMATS,
    default_web_export_suffix,
    require_web_export_destination,
    web_export_suffixes,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import WebControllerScope
from .web_editor_context import WebEditorContext, require_mutable_web_clip

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.web_controller import WebController


class WebDeliveryController(ControllerFacet[WebControllerScope]):
    deliveryStateChanged = Signal()

    def __init__(self, session: WebControllerScope, editor: WebController):
        super().__init__(session)
        self.setObjectName("webDeliveryController")
        self._editor = editor
        editor.webStateChanged.connect(self._refresh)

    @property
    def _context(self) -> WebEditorContext:
        return self._editor.context_snapshot()

    @Property(dict, notify=deliveryStateChanged)
    def rebindPlan(self) -> dict:
        delivery = self._session.state.web_delivery
        return {
            **dict(delivery.rebind_plan),
            "resolutions": dict(delivery.rebind_resolutions),
        }

    @Property(list, notify=deliveryStateChanged)
    def exportFormatOptions(self) -> list[dict]:
        overlay_suffix = self._overlay_export_suffix()
        labels = {
            "png": QCoreApplication.translate("WebExportCatalog", "PNG 单帧"),
            "gif": QCoreApplication.translate("WebExportCatalog", "GIF 动图"),
            "alpha_video": QCoreApplication.translate(
                "WebExportCatalog",
                "透明视频",
            ),
            "video": QCoreApplication.translate("WebExportCatalog", "普通视频"),
            "overlay": QCoreApplication.translate(
                "WebExportCatalog",
                "原样叠加层",
            ),
        }
        options: list[dict] = []
        for format_name in WEB_EXPORT_FORMATS:
            suffixes = web_export_suffixes(
                format_name,
                overlay_suffix=overlay_suffix if format_name == "overlay" else None,
            )
            label = labels[format_name]
            patterns = " ".join(f"*{suffix}" for suffix in suffixes)
            options.append(
                {
                    "label": label,
                    "value": format_name,
                    "suffix": default_web_export_suffix(
                        format_name,
                        overlay_suffix=(overlay_suffix if format_name == "overlay" else None),
                    ).removeprefix("."),
                    "filter": f"{label} ({patterns})",
                }
            )
        return options

    @Property(str, notify=deliveryStateChanged)
    @Slot(str, str, str)
    @report_ui_errors
    def createBatchVariants(
        self,
        records_json: str,
        bindings_json: str,
        name_template: str,
    ) -> None:
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        records = json.loads(records_json)
        bindings = json.loads(bindings_json)
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise ValueError("Batch records must be a JSON array of objects")
        if not isinstance(bindings, dict):
            raise ValueError("Batch bindings must be a JSON object")
        current.create_web_variants(
            self._session.state.binding.active_sequence_id,
            self._context.clip_id,
            records,
            {str(key): str(value) for key, value in bindings.items()},
            name_template=name_template or "版本 {index}",
            actor="human",
        )
        self._session.updates.commit(project=True)

    @Slot(QUrl, str, str)
    @report_ui_errors
    def createBatchVariantsFromFile(
        self,
        source_url: QUrl,
        bindings_json: str,
        name_template: str,
    ) -> None:
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        records = current.read_web_variant_records(source_url.toLocalFile())
        bindings = json.loads(bindings_json)
        if not isinstance(bindings, dict):
            raise ValueError("Batch bindings must be a JSON object")
        current.create_web_variants(
            self._session.state.binding.active_sequence_id,
            self._context.clip_id,
            records,
            {str(key): str(value) for key, value in bindings.items()},
            name_template=name_template or "版本 {index}",
            actor="human",
        )
        self._session.updates.commit(project=True)

    @Slot(QUrl)
    @report_ui_errors
    def inspectRebind(self, source_url: QUrl) -> None:
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        if not self._context.asset_id:
            raise ValueError("请先选择网页素材")
        delivery = self._session.state.web_delivery
        delivery.pending_rebind_source = source_url.toLocalFile()
        plan = current.plan_web_asset_rebind(
            self._context.asset_id,
            delivery.pending_rebind_source,
        )
        delivery.rebind_plan = plan.model_dump(mode="json")
        delivery.rebind_resolutions = {}
        self.deliveryStateChanged.emit()

    @Slot(str, str)
    def setRebindResolution(self, path: str, resolution: str) -> None:
        delivery = self._session.state.web_delivery
        conflicts = {str(item.get("path")): item for item in delivery.rebind_plan.get("conflicts", [])}
        conflict = conflicts.get(path)
        if conflict is None:
            return
        if resolution not in conflict.get("allowed_resolutions", []):
            return
        delivery.rebind_resolutions[path] = resolution
        self.deliveryStateChanged.emit()

    @Slot()
    @report_ui_errors
    def commitRebind(self) -> None:
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        delivery = self._session.state.web_delivery
        if not self._context.asset_id or not delivery.pending_rebind_source:
            raise ValueError("请先检查新版网页包")
        report = current.commit_web_asset_rebind(
            self._context.asset_id,
            delivery.pending_rebind_source,
            str(delivery.rebind_plan.get("plan_digest") or ""),
            delivery.rebind_resolutions,
        )
        delivery.rebind_plan = {
            **delivery.rebind_plan,
            "commit": report.model_dump(mode="json"),
        }
        self._session.updates.commit(project=True)

    @Slot(QUrl, str, int, str, bool)
    @report_ui_errors
    def exportSelected(
        self,
        output_url: QUrl,
        format_name: str,
        time_ms: int,
        background: str,
        overwrite: bool,
    ) -> None:
        require_mutable_web_clip(self._session, self._context.clip_id)
        export_format = next(
            (value for value in WEB_EXPORT_FORMATS if value == format_name),
            None,
        )
        if export_format is None:
            raise ValueError(f"未知的网页导出格式：{format_name}")
        overlay_suffix = self._overlay_export_suffix()
        destination = require_web_export_destination(
            output_url.toLocalFile(),
            export_format,
            overlay_suffix=(overlay_suffix if export_format == "overlay" else None),
        )
        command = ExportWebClipCommand(
            sequence_id=self._session.state.binding.active_sequence_id,
            clip_id=self._context.clip_id,
            output_path=str(destination),
            format=export_format,
            time_ms=max(0, int(time_ms)),
            background=background or "#000000",
            overwrite=overwrite,
        )
        self._session.tasks.start(command, sequence_id=self._session.state.binding.active_sequence_id)

    def _overlay_export_suffix(self) -> str:
        animated = int(self._context.manifest.get("duration_ms") or 0) > 0 or any(
            bool(scene.get("animations"))
            for scene in (self._context.persistent_state.get("scenes") or {}).values()
            if isinstance(scene, dict)
        )
        return ".mkv" if animated else ".png"

    @Slot()
    def _refresh(self) -> None:
        clip_id = self._context.clip_id
        delivery = self._session.state.web_delivery
        if clip_id != delivery.selected_clip_id:
            delivery.selected_clip_id = clip_id
            delivery.rebind_plan = {}
            delivery.rebind_resolutions = {}
            delivery.pending_rebind_source = ""
        self.deliveryStateChanged.emit()
