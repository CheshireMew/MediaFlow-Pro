from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QCoreApplication, QObject, QUrl, Signal, Slot

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.task_commands import ExportWebClipCommand
from mediaflow.domain.web_media import (
    WEB_EXPORT_FORMATS,
    default_web_export_suffix,
    require_web_export_destination,
    web_export_suffixes,
)

from .controller_facet import ControllerFacet, report_ui_errors

if TYPE_CHECKING:
    from mediaflow.composition import EditorProject


class WebController(ControllerFacet):
    projectStateChanged = Signal()
    historyChanged = Signal()
    errorOccurred = Signal(str)
    webStateChanged = Signal()
    webSelectionChanged = Signal()
    browserSelectionRequested = Signal(str)
    entryUrlChanged = Signal()

    def __init__(self, session):
        super().__init__(session)
        self.setObjectName("webController")
        self._web_edit_mode = False
        self._web_clip_id = ""
        self._web_asset_id = ""
        self._web_entry_url = ""
        self._web_manifest: dict = {}
        self._web_state: dict = {}
        self._runtime_web_state: dict = {}
        self._active_variant_id = ""
        self._active_scene_id = ""
        self._selected_web_layer_id = ""
        self._browser_values: dict[str, dict] = {}
        self._browser_revision = 0
        self._browser_edit_mode = False
        self._browser_selected_layer_id = ""
        self._browser_ready = False
        self._rebind_report: dict = {}
        self._pending_rebind_source = ""
        session.events.selectionChanged.connect(self._refresh)
        session.events.projectStateChanged.connect(self._refresh)
        session.events.historyChanged.connect(self._refresh)

    @Property(QObject, constant=True)
    def layersModel(self) -> QObject:
        return self._session.models.web_layers

    @Property(bool, notify=webStateChanged)
    def isWebClip(self) -> bool:
        return bool(self._web_clip_id)

    @Property(bool, notify=webStateChanged)
    def editMode(self) -> bool:
        return self._web_edit_mode

    @Property(str, notify=entryUrlChanged)
    def entryUrl(self) -> str:
        return self._web_entry_url

    @Property(bool, notify=webStateChanged)
    def browserReady(self) -> bool:
        return self._browser_ready

    @Property("QVariantMap", notify=webSelectionChanged)
    def browserLayerSnapshot(self) -> dict:
        return dict(self._browser_values.get(self._selected_web_layer_id) or {})

    @Property(int, notify=webStateChanged)
    def browserRevision(self) -> int:
        return self._browser_revision

    @Property(bool, notify=webStateChanged)
    def browserEditMode(self) -> bool:
        return self._browser_edit_mode

    @Property(str, notify=webStateChanged)
    def browserSelectedLayerId(self) -> str:
        return self._browser_selected_layer_id

    @Property("QVariantMap", notify=webStateChanged)
    def manifestData(self) -> dict:
        return dict(self._web_manifest)

    @Property(str, notify=webStateChanged)
    def stateJson(self) -> str:
        return json.dumps(self._runtime_web_state, ensure_ascii=False, separators=(",", ":"))

    @Property(str, notify=webStateChanged)
    def persistentStateJson(self) -> str:
        return json.dumps(self._web_state, ensure_ascii=False, separators=(",", ":"))

    @Property("QVariantMap", notify=webStateChanged)
    def activeCanvasData(self) -> dict:
        variant = next(
            (
                item
                for item in self._web_manifest.get("variants", [])
                if item.get("id") == self._active_variant_id
            ),
            None,
        )
        return dict((variant or {}).get("canvas") or {})

    @Property(str, notify=webStateChanged)
    def activeVariantId(self) -> str:
        return self._active_variant_id

    @Property("QVariantList", notify=webStateChanged)
    def variantOptions(self) -> list[dict]:
        selected = ((self._web_state.get("variant") or {}).get("id")
                    or self._web_manifest.get("default_variant_id"))
        return [
            {
                "id": item["id"],
                "name": f"{item['name']} · {item['canvas']['width']}×{item['canvas']['height']}",
                "selected": selected == item["id"],
            }
            for item in self._web_manifest.get("variants", [])
        ]

    @Property(str, notify=webStateChanged)
    def activeSceneId(self) -> str:
        return self._active_scene_id

    @Property("QVariantList", notify=webStateChanged)
    def themeOptions(self) -> list[dict]:
        overrides = self._web_state.get("theme", {})
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "value": overrides.get(item["id"], item.get("default")),
            }
            for item in self._web_manifest.get("theme_variables", [])
        ]

    @Property("QVariantList", notify=webStateChanged)
    def dataOptions(self) -> list[dict]:
        scene = (self._web_state.get("scenes") or {}).get(self._active_scene_id, {})
        values = (scene.get("data_snapshot") or {}).get("values", {})
        definition: dict = next(
            (
                item
                for item in self._web_manifest.get("scenes", [])
                if item.get("id") == self._active_scene_id
            ),
            {},
        )
        scene_values = definition.get("data") or {}
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "valueText": json.dumps(
                    values.get(item["id"], scene_values.get(item["id"], item.get("default"))),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
            for item in self._web_manifest.get("data_fields", [])
        ]

    @Property("QVariantList", notify=webStateChanged)
    def keyframesData(self) -> list[dict]:
        clip = self._selected_clip()
        if clip is None or self._session.binding.timeline is None:
            return []
        profile = self._session.binding.timeline.state.sequence.profile
        fps = profile.fps
        scene_start_ms = self._scene_start_ms(self._active_scene_id)
        values: list[dict] = []
        scene = (self._web_state.get("scenes") or {}).get(self._active_scene_id, {})
        for layer_id, tracks in scene.get("animations", {}).items():
            for field, track in tracks.items():
                for keyframe in track.get("keyframes", []):
                    source_frame = round(
                        (scene_start_ms + int(keyframe["time_ms"])) * fps / 1000
                    )
                    speed = abs(clip.speed_numerator) / clip.speed_denominator
                    if clip.speed_numerator > 0:
                        local_frame = round((source_frame - clip.source_in) / speed)
                    else:
                        local_frame = round((clip.source_in - source_frame) / speed)
                    frame = clip.timeline_start + local_frame
                    if clip.timeline_start <= frame < clip.timeline_end:
                        values.append(
                            {
                                "layerId": layer_id,
                                "field": field,
                                "timeMs": int(keyframe["time_ms"]),
                                "frame": frame,
                                "valueText": json.dumps(keyframe.get("value"), ensure_ascii=False),
                                "easing": (keyframe.get("easing") or {}).get("kind", "linear"),
                            }
                        )
        return sorted(values, key=lambda item: (item["frame"], item["layerId"], item["field"]))

    @Property("QVariantMap", notify=webStateChanged)
    def componentData(self) -> dict:
        return dict(self._web_manifest.get("component") or {})

    @Property("QVariantMap", notify=webStateChanged)
    def rebindReport(self) -> dict:
        return dict(self._rebind_report)

    @Property("QVariantList", notify=webStateChanged)
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
                        overlay_suffix=(
                            overlay_suffix if format_name == "overlay" else None
                        ),
                    ).removeprefix("."),
                    "filter": f"{label} ({patterns})",
                }
            )
        return options

    @Property(str, notify=webStateChanged)
    def capabilitiesJson(self) -> str:
        if (
            not self._session.binding.current
            or self._session.binding.current.read_only
        ):
            return "{}"
        values = {
            layer["id"]: list(layer.get("editable") or []) for layer in self._web_manifest.get("layers", [])
        }
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    @Property(str, notify=webSelectionChanged)
    def selectedLayerId(self) -> str:
        return self._selected_web_layer_id

    @Property("QVariantMap", notify=webSelectionChanged)
    def selectedLayerData(self) -> dict:
        row = self._session.models.web_layers.findRow("layerId", self._selected_web_layer_id)
        return self._session.models.web_layers.get(row)

    @Property(str, notify=webStateChanged)
    def browserSnapshotScript(self) -> str:
        selectors = {layer["id"]: layer["selector"] for layer in self._web_manifest.get("layers", [])}
        encoded = json.dumps(selectors, ensure_ascii=False)
        return f"""(() => {{
            const selectors = {encoded};
            const values = {{}};
            for (const [id, selector] of Object.entries(selectors)) {{
                const node = document.querySelector(selector);
                if (!node) continue;
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                values[id] = {{
                    content: node.textContent,
                    color: style.color,
                    font_family: style.fontFamily,
                    font_size: parseFloat(style.fontSize),
                    image: node instanceof HTMLImageElement ? node.getAttribute("src") : "",
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    opacity: parseFloat(style.opacity),
                    z_index: Number.isFinite(parseInt(style.zIndex)) ? parseInt(style.zIndex) : 0
                }};
            }}
            return JSON.stringify({{
                revision: Number(window.editableMedia.getState().revision || 0),
                edit_mode: document.documentElement.hasAttribute("data-editable-mode"),
                selected_layer_id:
                    document.querySelector("[data-editable-selected]")
                        ?.dataset.editableId || "",
                layers: values
            }});
        }})()"""

    @Slot(bool)
    @report_ui_errors
    def setEditMode(self, enabled: bool) -> None:
        enabled = bool(enabled) and bool(self._web_clip_id)
        if enabled:
            self._require_mutable_web_clip()
        if enabled == self._web_edit_mode:
            return
        self._web_edit_mode = enabled
        self.webStateChanged.emit()

    @Slot(str)
    def selectLayer(self, layer_id: str) -> None:
        if self._set_selected_layer(layer_id):
            self.browserSelectionRequested.emit(
                self._selected_web_layer_id
            )

    @Slot(str)
    def selectBrowserLayer(self, layer_id: str) -> None:
        self._set_selected_layer(layer_id)

    @Slot()
    def browserBridgeReady(self) -> None:
        if self._browser_ready:
            return
        self._browser_ready = True
        self.webStateChanged.emit()

    @Slot(str)
    def applyBrowserSnapshot(self, payload: str) -> None:
        try:
            snapshot = json.loads(payload or "{}")
            if not isinstance(snapshot, dict):
                raise ValueError("Browser snapshot must be an object")
            revision = snapshot.get("revision")
            edit_mode = snapshot.get("edit_mode")
            selected_layer_id = snapshot.get("selected_layer_id")
            values = snapshot.get("layers")
            if (
                not isinstance(revision, int)
                or not isinstance(edit_mode, bool)
                or not isinstance(selected_layer_id, str)
                or not isinstance(values, dict)
            ):
                raise ValueError(
                    "Browser snapshot must contain revision, edit mode, "
                    "selection, and layers"
                )
            normalized = {
                str(layer_id): dict(value)
                for layer_id, value in values.items()
                if isinstance(value, dict)
            }
            if (
                revision == self._browser_revision
                and edit_mode == self._browser_edit_mode
                and selected_layer_id == self._browser_selected_layer_id
                and normalized == self._browser_values
            ):
                return
            self._browser_revision = revision
            self._browser_edit_mode = edit_mode
            self._browser_selected_layer_id = selected_layer_id
            self._browser_values = normalized
            self._refresh_layers()
            self.webSelectionChanged.emit()
            self.webStateChanged.emit()
        except (TypeError, ValueError) as error:
            self._session.events.errorOccurred.emit(str(error))

    @Slot(str, "QVariantMap")
    @report_ui_errors
    def updateLayer(self, layer_id: str, changes: dict) -> None:
        current = self._require_mutable_web_clip()
        current_revision = int(self._web_state.get("revision", 0))
        updated = current.update_web_clip(
            self._session.binding.active_sequence_id,
            clip_id=self._web_clip_id,
            updates={layer_id: dict(changes)},
            scene_id=self._active_scene_id,
            expected_revision=current_revision,
            actor="human",
        )
        self._accept_state(updated)

    @Slot(str)
    @report_ui_errors
    def commitBrowserState(self, payload: str) -> None:
        current = self._require_mutable_web_clip()
        browser_state = json.loads(payload)
        if browser_state == self._runtime_web_state:
            return
        updated = current.commit_web_runtime_state(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            browser_state,
            expected_revision=int(self._web_state.get("revision", 0)),
        )
        self._accept_state(updated)

    @Slot(str)
    @report_ui_errors
    def selectVariant(self, variant_id: str) -> None:
        current = self._require_mutable_web_clip()
        updated = current.select_web_variant(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            variant_id,
            expected_revision=int(self._web_state.get("revision", 0)),
        )
        self._accept_state(updated)

    @Slot(str, str)
    @report_ui_errors
    def updateThemeValue(self, variable_id: str, value: str) -> None:
        current = self._require_mutable_web_clip()
        item = next(
            entry for entry in self._web_manifest.get("theme_variables", []) if entry["id"] == variable_id
        )
        typed_value: str | float = float(value) if item["kind"] == "number" else value
        updated = current.update_web_theme(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            {variable_id: typed_value},
            expected_revision=int(self._web_state.get("revision", 0)),
        )
        self._accept_state(updated)

    @Slot(str, str)
    @report_ui_errors
    def updateDataValue(self, field_id: str, value_json: str) -> None:
        current = self._require_mutable_web_clip()
        value = json.loads(value_json)
        updated = current.update_web_data(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            {field_id: value},
            scene_id=self._active_scene_id,
            expected_revision=int(self._web_state.get("revision", 0)),
        )
        self._accept_state(updated)

    @Slot(QUrl, str)
    @report_ui_errors
    def importDataSnapshot(self, source_url: QUrl, field_id: str) -> None:
        current = self._require_mutable_web_clip()
        updated = current.update_web_data_from_file(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            source_url.toLocalFile(),
            scene_id=self._active_scene_id,
            field_id=field_id or None,
            expected_revision=int(self._web_state.get("revision", 0)),
        )
        self._accept_state(updated)

    @Slot(str, str, bool)
    def setFieldLocked(self, layer_id: str, field: str, locked: bool) -> None:
        self._set_locks(layer_id, [field], locked)

    @Slot(str, bool)
    def setLayerLocked(self, layer_id: str, locked: bool) -> None:
        layer = next(
            (item for item in self._web_manifest.get("layers", []) if item["id"] == layer_id),
            None,
        )
        if layer is not None:
            self._set_locks(layer_id, list(layer.get("editable") or []), locked)

    @Slot(str, "QVariant", str, int)
    @report_ui_errors
    def setKeyframeAtFrame(self, field: str, value, easing: str, frame: int) -> None:
        current = self._require_mutable_web_clip()
        if not self._selected_web_layer_id:
            raise ValueError("请先选择网页图层")
        scene_id, time_ms = self._scene_time_for_frame(frame)
        updated = current.set_web_keyframe(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            self._selected_web_layer_id,
            field,
            time_ms,
            self._coerce_value(field, value),
            scene_id=scene_id,
            easing={"kind": easing or "linear"},
            expected_revision=int(self._web_state.get("revision", 0)),
            actor="human",
        )
        self._accept_state(updated)

    @Slot(str, int)
    @report_ui_errors
    def removeKeyframeAtFrame(self, field: str, frame: int) -> None:
        current = self._require_mutable_web_clip()
        if not self._selected_web_layer_id:
            raise ValueError("请先选择网页图层")
        scene_id, time_ms = self._scene_time_for_frame(frame)
        updated = current.remove_web_keyframe(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            self._selected_web_layer_id,
            field,
            time_ms,
            scene_id=scene_id,
            expected_revision=int(self._web_state.get("revision", 0)),
        )
        self._accept_state(updated)

    @Slot(int, result=int)
    def timeMsForFrame(self, frame: int) -> int:
        try:
            return self._time_ms_for_frame(frame)
        except RuntimeError:
            return 0

    @Slot(int)
    def setActiveFrame(self, frame: int) -> None:
        try:
            scene_id, _local_time_ms = self._scene_time_for_frame(frame)
        except RuntimeError:
            return
        if scene_id == self._active_scene_id:
            return
        self._active_scene_id = scene_id
        self._refresh_layers()
        self.webStateChanged.emit()

    @Slot(str, str, str)
    @report_ui_errors
    def createBatchVariants(
        self,
        records_json: str,
        bindings_json: str,
        name_template: str,
    ) -> None:
        current = self._require_mutable_web_clip()
        records = json.loads(records_json)
        bindings = json.loads(bindings_json)
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise ValueError("Batch records must be a JSON array of objects")
        if not isinstance(bindings, dict):
            raise ValueError("Batch bindings must be a JSON object")
        current.create_web_variants(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            records,
            {str(key): str(value) for key, value in bindings.items()},
            name_template=name_template or "版本 {index}",
            actor="human",
        )
        self._session.events.projectStateChanged.emit()

    @Slot(QUrl, str, str)
    @report_ui_errors
    def createBatchVariantsFromFile(
        self,
        source_url: QUrl,
        bindings_json: str,
        name_template: str,
    ) -> None:
        current = self._require_mutable_web_clip()
        records = current.read_web_variant_records(source_url.toLocalFile())
        bindings = json.loads(bindings_json)
        if not isinstance(bindings, dict):
            raise ValueError("Batch bindings must be a JSON object")
        current.create_web_variants(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            records,
            {str(key): str(value) for key, value in bindings.items()},
            name_template=name_template or "版本 {index}",
            actor="human",
        )
        self._session.events.projectStateChanged.emit()

    @Slot(QUrl)
    @report_ui_errors
    def inspectRebind(self, source_url: QUrl) -> None:
        current = self._require_mutable_web_clip()
        if not self._web_asset_id:
            raise ValueError("请先选择网页素材")
        self._pending_rebind_source = source_url.toLocalFile()
        report = current.rebind_web_asset(
            self._web_asset_id,
            self._pending_rebind_source,
            dry_run=True,
        )
        self._rebind_report = report.model_dump(mode="json")
        self.webStateChanged.emit()

    @Slot(bool)
    @report_ui_errors
    def commitRebind(self, allow_conflicts: bool) -> None:
        current = self._require_mutable_web_clip()
        if not self._web_asset_id or not self._pending_rebind_source:
            raise ValueError("请先检查新版网页包")
        report = current.rebind_web_asset(
            self._web_asset_id,
            self._pending_rebind_source,
            dry_run=False,
            allow_conflicts=allow_conflicts,
        )
        self._rebind_report = report.model_dump(mode="json")
        self._session.events.projectStateChanged.emit()

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
        self._require_mutable_web_clip()
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
            overlay_suffix=(
                overlay_suffix if export_format == "overlay" else None
            ),
        )
        command = ExportWebClipCommand(
            sequence_id=self._session.binding.active_sequence_id,
            clip_id=self._web_clip_id,
            output_path=str(destination),
            format=export_format,
            time_ms=max(0, int(time_ms)),
            background=background or "#000000",
            overwrite=overwrite,
        )
        self._session.tasks.start(command, sequence_id=self._session.binding.active_sequence_id)

    @report_ui_errors
    def _set_locks(self, layer_id: str, fields: list[str], locked: bool) -> None:
        current = self._require_mutable_web_clip()
        updated = current.set_web_field_locks(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            layer_id,
            fields,
            locked,
            scene_id=self._active_scene_id,
            expected_revision=int(self._web_state.get("revision", 0)),
        )
        self._accept_state(updated)

    def _accept_state(self, state) -> None:
        self._web_state = state.model_dump(mode="json")
        self._refresh_runtime_state()
        self._refresh_layers()
        self.webSelectionChanged.emit()
        self.webStateChanged.emit()
        self._session.events.historyChanged.emit()
        self._session.projectors.timeline.schedule_preview_graph()

    def _refresh_runtime_state(self) -> None:
        if self._session.binding.current and self._web_clip_id:
            self._runtime_web_state = self._session.binding.current.web_runtime_state(
                self._session.binding.active_sequence_id,
                self._web_clip_id,
            )
            self._active_variant_id = str(
                (self._runtime_web_state.get("variant") or {}).get("id") or ""
            )
            known_scenes = {
                item.get("id") for item in self._web_manifest.get("scenes", [])
            }
            if self._active_scene_id not in known_scenes:
                self._active_scene_id = str(
                    self._runtime_web_state.get("scene_id") or ""
                )
        else:
            self._runtime_web_state = {}
            self._active_variant_id = ""
            self._active_scene_id = ""

    def _require_mutable_web_clip(self) -> EditorProject:
        self._session._require_writable()
        if not self._web_clip_id:
            raise ValueError("请先选择网页片段")
        current = self._session.binding.current
        if current is None:
            raise RuntimeError("请先打开一个项目")
        return current

    def _set_selected_layer(self, layer_id: str) -> bool:
        known = {
            layer["id"]
            for layer in self._web_manifest.get("layers", [])
        }
        value = layer_id if layer_id in known else ""
        if value == self._selected_web_layer_id:
            return False
        self._selected_web_layer_id = value
        self.webSelectionChanged.emit()
        return True

    def _overlay_export_suffix(self) -> str:
        animated = int(self._web_manifest.get("duration_ms") or 0) > 0 or any(
            bool(scene.get("animations"))
            for scene in (self._web_state.get("scenes") or {}).values()
            if isinstance(scene, dict)
        )
        return ".mkv" if animated else ".png"

    def _selected_clip(self):
        if self._session.binding.timeline is None or not self._web_clip_id:
            return None
        return next(
            (item for item in self._session.binding.timeline.state.clips if item.id == self._web_clip_id),
            None,
        )

    def _time_ms_for_frame(self, frame: int) -> int:
        clip = self._selected_clip()
        if clip is None or self._session.binding.timeline is None:
            raise RuntimeError("No editable web clip is selected")
        profile = self._session.binding.timeline.state.sequence.profile
        local_frame = max(0, min(clip.duration - 1, int(frame) - clip.timeline_start))
        consumed = round(local_frame * abs(clip.speed_numerator) / clip.speed_denominator)
        source_frame = clip.source_in + consumed if clip.speed_numerator > 0 else clip.source_in - consumed
        return max(0, round(source_frame * 1000 / profile.fps))

    def _scene_time_for_frame(self, frame: int) -> tuple[str, int]:
        global_time_ms = self._time_ms_for_frame(frame)
        elapsed = 0
        scenes = self._web_manifest.get("scenes", [])
        for scene in scenes:
            duration = int(scene.get("duration_ms") or 0)
            if global_time_ms < elapsed + duration:
                return str(scene["id"]), max(0, global_time_ms - elapsed)
            elapsed += duration
        if not scenes:
            raise RuntimeError("Editable media manifest has no scenes")
        last = scenes[-1]
        return str(last["id"]), max(0, int(last.get("duration_ms") or 1) - 1)

    def _scene_start_ms(self, scene_id: str) -> int:
        elapsed = 0
        for scene in self._web_manifest.get("scenes", []):
            if scene.get("id") == scene_id:
                return elapsed
            elapsed += int(scene.get("duration_ms") or 0)
        return 0

    @staticmethod
    def _coerce_value(field: str, value):
        if field in {
            "font_size",
            "x",
            "y",
            "width",
            "height",
            "rotation",
            "opacity",
        }:
            return float(value)
        if field in {"z_index", "enter_ms", "exit_ms", "delay_ms", "duration_ms"}:
            return int(float(value))
        if field == "visible":
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        return value

    @Slot()
    def _refresh(self) -> None:
        previous_clip_id = self._web_clip_id
        previous_entry_url = self._web_entry_url
        self._web_clip_id = ""
        self._web_asset_id = ""
        self._web_entry_url = ""
        self._web_manifest = {}
        self._web_state = {}
        self._runtime_web_state = {}
        self._active_variant_id = ""
        self._active_scene_id = ""
        if (
            self._session.binding.current
            and self._session.binding.timeline
            and self._session.selection.clip_ids
        ):
            clip_id = self._session.selection.clip_ids[-1]
            clip = next(
                (item for item in self._session.binding.timeline.state.clips if item.id == clip_id), None
            )
            if clip is not None:
                asset = self._session.binding.current.get_asset(clip.asset_id)
                if asset.kind == AssetKind.WEB:
                    spec = self._session.binding.current.get_web_asset_spec(asset.id)
                    state = self._session.binding.timeline.state.web_states[clip.id]
                    self._web_clip_id = clip.id
                    self._web_asset_id = asset.id
                    self._web_entry_url = (
                        self._session.binding.current.web_editor_entry_url(asset.id)
                    )
                    self._web_manifest = spec.manifest.model_dump(mode="json")
                    self._web_state = state.model_dump(mode="json")
                    self._refresh_runtime_state()
        if self._web_clip_id != previous_clip_id:
            self._browser_values = {}
            self._browser_revision = 0
            self._browser_edit_mode = False
            self._browser_selected_layer_id = ""
            self._browser_ready = False
        known = [layer["id"] for layer in self._web_manifest.get("layers", [])]
        if self._selected_web_layer_id not in known:
            self._selected_web_layer_id = known[0] if known else ""
        if not self._web_clip_id:
            if self._session.binding.current:
                self._session.binding.current.close_web_preview()
            self._web_edit_mode = False
            self._browser_revision = 0
            self._browser_edit_mode = False
            self._browser_selected_layer_id = ""
            self._browser_ready = False
            self._selected_web_layer_id = ""
        elif self._session.binding.current.read_only:
            self._web_edit_mode = False
        self._refresh_layers()
        self.webSelectionChanged.emit()
        if self._web_entry_url != previous_entry_url:
            self.entryUrlChanged.emit()
        self.webStateChanged.emit()

    def _refresh_layers(self) -> None:
        runtime_scene = (
            (self._runtime_web_state.get("scenes") or {}).get(
                self._active_scene_id, {}
            )
            or {}
        )
        persistent_scene = (
            (self._web_state.get("scenes") or {}).get(self._active_scene_id, {})
            or {}
        )
        overrides = runtime_scene.get("layers", {})
        locks = persistent_scene.get("locks", {})
        animations = persistent_scene.get("animations", {})
        scene_definition: dict = next(
            (
                item
                for item in self._web_manifest.get("scenes", [])
                if item.get("id") == self._active_scene_id
            ),
            {},
        )
        rows = []
        for layer in self._web_manifest.get("layers", []):
            override = dict(overrides.get(layer["id"]) or {})
            browser = self._browser_values.get(layer["id"], {})
            bounds = layer["default_bounds"]

            def value(field: str, fallback, *, _override=override, _browser=browser):
                if field in _override and _override[field] is not None:
                    return _override[field]
                return _browser.get(field, fallback)

            rows.append(
                {
                    "layerId": layer["id"],
                    "name": layer["name"],
                    "kind": layer["kind"],
                    "parentId": layer.get("parent_id") or "",
                    "editable": list(layer.get("editable") or []),
                    "content": value("content", ""),
                    "color": value("color", ""),
                    "fontFamily": value("font_family", ""),
                    "fontSize": value("font_size", 16),
                    "image": value("image", ""),
                    "x": value("x", bounds["x"]),
                    "y": value("y", bounds["y"]),
                    "width": value("width", bounds["width"]),
                    "height": value("height", bounds["height"]),
                    "rotation": value("rotation", bounds.get("rotation", 0)),
                    "opacity": value("opacity", 1.0),
                    "zIndex": value("z_index", 0),
                    "layerVisible": value("visible", True),
                    "lockedFields": list(locks.get(layer["id"]) or []),
                    "allFieldsLocked": bool(layer.get("editable"))
                    and set(layer.get("editable") or []).issubset(set(locks.get(layer["id"]) or [])),
                    "keyframeCount": sum(
                        len(track.get("keyframes") or [])
                        for track in animations.get(layer["id"], {}).values()
                    ),
                    "enterMs": value("enter_ms", 0),
                    "exitMs": value("exit_ms", scene_definition.get("duration_ms", 0)),
                    "delayMs": value("delay_ms", 0),
                    "durationMs": value("duration_ms", 0),
                }
            )
        self._session.models.web_layers.set_items(rows)
