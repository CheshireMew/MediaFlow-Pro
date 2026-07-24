from __future__ import annotations

import json
from typing import cast

from PySide6.QtCore import Property, QObject, QUrl, QUrlQuery, Signal, Slot

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.task_commands import ExportWebClipCommand
from mediaflow.domain.web_media import WebExportFormat

from .controller_facet import ControllerFacet


class WebController(ControllerFacet):
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
    webStateChanged = Signal()

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
        self._active_layout_id = ""
        self._selected_web_layer_id = ""
        self._browser_values: dict[str, dict] = {}
        self._rebind_report: dict = {}
        self._pending_rebind_source = ""
        self.selectionChanged.connect(self._refresh)
        self.projectStateChanged.connect(self._refresh)
        self.historyChanged.connect(self._refresh)

    @Property(QObject, constant=True)
    def layersModel(self) -> QObject:
        return self._web_layer_model

    @Property(bool, notify=webStateChanged)
    def isWebClip(self) -> bool:
        return bool(self._web_clip_id)

    @Property(bool, notify=webStateChanged)
    def editMode(self) -> bool:
        return self._web_edit_mode

    @Property(str, notify=webStateChanged)
    def entryUrl(self) -> str:
        return self._web_entry_url

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
        return dict(self._runtime_web_state.get("layout") or self._web_manifest.get("canvas") or {})

    @Property(str, notify=webStateChanged)
    def activeLayoutId(self) -> str:
        return self._active_layout_id

    @Property("QVariantList", notify=webStateChanged)
    def layoutOptions(self) -> list[dict]:
        selected = self._web_state.get("layout_id")
        values = [
            {
                "id": "",
                "name": "自动匹配序列比例",
                "selected": selected is None,
            }
        ]
        values.extend(
            {
                "id": item["id"],
                "name": f'{item["name"]} · {item["canvas"]["width"]}×{item["canvas"]["height"]}',
                "selected": selected == item["id"],
            }
            for item in self._web_manifest.get("layouts", [])
        )
        return values

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
        values = (self._web_state.get("data_snapshot") or {}).get("values", {})
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "valueText": json.dumps(
                    values.get(item["id"], item.get("default")),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
            for item in self._web_manifest.get("data_fields", [])
        ]

    @Property("QVariantList", notify=webStateChanged)
    def keyframesData(self) -> list[dict]:
        clip = self._selected_clip()
        if clip is None or self._editor is None:
            return []
        profile = self._editor.state.sequence.profile
        fps = profile.fps
        values: list[dict] = []
        for layer_id, tracks in self._web_state.get("animations", {}).items():
            for field, track in tracks.items():
                for keyframe in track.get("keyframes", []):
                    source_frame = round(int(keyframe["time_ms"]) * fps / 1000)
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

    @Property(str, notify=webStateChanged)
    def capabilitiesJson(self) -> str:
        values = {
            layer["id"]: list(layer.get("editable") or [])
            for layer in self._web_manifest.get("layers", [])
        }
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    @Property(str, notify=webStateChanged)
    def selectedLayerId(self) -> str:
        return self._selected_web_layer_id

    @Property("QVariantMap", notify=webStateChanged)
    def selectedLayerData(self) -> dict:
        row = self._web_layer_model.findRow("layerId", self._selected_web_layer_id)
        return self._web_layer_model.get(row)

    @Property(str, notify=webStateChanged)
    def browserSnapshotScript(self) -> str:
        selectors = {
            layer["id"]: layer["selector"] for layer in self._web_manifest.get("layers", [])
        }
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
            return JSON.stringify(values);
        }})()"""

    @Slot(bool)
    def setEditMode(self, enabled: bool) -> None:
        enabled = bool(enabled) and bool(self._web_clip_id)
        if enabled == self._web_edit_mode:
            return
        self._web_edit_mode = enabled
        self.webStateChanged.emit()

    @Slot(str)
    def selectLayer(self, layer_id: str) -> None:
        known = {layer["id"] for layer in self._web_manifest.get("layers", [])}
        value = layer_id if layer_id in known else ""
        if value == self._selected_web_layer_id:
            return
        self._selected_web_layer_id = value
        self.webStateChanged.emit()

    @Slot(str)
    def selectBrowserLayer(self, layer_id: str) -> None:
        self.selectLayer(layer_id)

    @Slot(str)
    def applyBrowserSnapshot(self, payload: str) -> None:
        try:
            values = json.loads(payload or "{}")
            if not isinstance(values, dict):
                raise ValueError("Browser layer snapshot must be an object")
            self._browser_values = {
                str(layer_id): dict(value)
                for layer_id, value in values.items()
                if isinstance(value, dict)
            }
            self._refresh_layers()
            self.webStateChanged.emit()
        except (TypeError, ValueError) as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, "QVariantMap")
    def updateLayer(self, layer_id: str, changes: dict) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            current_revision = int(self._web_state.get("revision", 0))
            updated = self._project.web.update_clip(
                self._active_sequence_id,
                clip_id=self._web_clip_id,
                updates={layer_id: dict(changes)},
                expected_revision=current_revision,
                actor="human",
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def commitBrowserState(self, payload: str) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            browser_state = json.loads(payload)
            before = self._runtime_web_state.get("layers", {})
            after = browser_state.get("layers", {})
            updates: dict[str, dict[str, object]] = {}
            editable = {
                layer["id"]: set(layer.get("editable") or [])
                for layer in self._web_manifest.get("layers", [])
            }
            for layer_id in set(before) | set(after):
                old = dict(before.get(layer_id) or {})
                new = dict(after.get(layer_id) or {})
                patch = {
                    field: new.get(field)
                    for field in set(old) | set(new)
                    if field in editable.get(layer_id, set()) and old.get(field) != new.get(field)
                }
                if patch:
                    updates[layer_id] = patch
            if not updates:
                return
            updated = self._project.web.update_clip(
                self._active_sequence_id,
                self._web_clip_id,
                updates,
                expected_revision=int(browser_state.get("revision", 0)),
                actor="human",
                layout_id=self._active_layout_id or None,
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def selectLayout(self, layout_id: str) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            updated = self._project.web.select_layout(
                self._active_sequence_id,
                self._web_clip_id,
                layout_id or None,
                expected_revision=int(self._web_state.get("revision", 0)),
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def updateThemeValue(self, variable_id: str, value: str) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            item = next(
                entry
                for entry in self._web_manifest.get("theme_variables", [])
                if entry["id"] == variable_id
            )
            typed_value: str | float = float(value) if item["kind"] == "number" else value
            updated = self._project.web.update_theme(
                self._active_sequence_id,
                self._web_clip_id,
                {variable_id: typed_value},
                expected_revision=int(self._web_state.get("revision", 0)),
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def updateDataValue(self, field_id: str, value_json: str) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            value = json.loads(value_json)
            updated = self._project.web.update_data(
                self._active_sequence_id,
                self._web_clip_id,
                {field_id: value},
                expected_revision=int(self._web_state.get("revision", 0)),
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(QUrl, str)
    def importDataSnapshot(self, source_url: QUrl, field_id: str) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            updated = self._project.web.update_data_from_file(
                self._active_sequence_id,
                self._web_clip_id,
                source_url.toLocalFile(),
                field_id=field_id or None,
                expected_revision=int(self._web_state.get("revision", 0)),
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

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
    def setKeyframeAtFrame(self, field: str, value, easing: str, frame: int) -> None:
        if not self._project or not self._web_clip_id or not self._selected_web_layer_id:
            return
        try:
            time_ms = self._time_ms_for_frame(frame)
            updated = self._project.web.set_keyframe(
                self._active_sequence_id,
                self._web_clip_id,
                self._selected_web_layer_id,
                field,
                time_ms,
                self._coerce_value(field, value),
                easing={"kind": easing or "linear"},
                expected_revision=int(self._web_state.get("revision", 0)),
                actor="human",
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int)
    def removeKeyframeAtFrame(self, field: str, frame: int) -> None:
        if not self._project or not self._web_clip_id or not self._selected_web_layer_id:
            return
        try:
            updated = self._project.web.remove_keyframe(
                self._active_sequence_id,
                self._web_clip_id,
                self._selected_web_layer_id,
                field,
                self._time_ms_for_frame(frame),
                expected_revision=int(self._web_state.get("revision", 0)),
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, result=int)
    def timeMsForFrame(self, frame: int) -> int:
        try:
            return self._time_ms_for_frame(frame)
        except RuntimeError:
            return 0

    @Slot(str, str, str)
    def createBatchVariants(
        self,
        records_json: str,
        bindings_json: str,
        name_template: str,
    ) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            records = json.loads(records_json)
            bindings = json.loads(bindings_json)
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                raise ValueError("Batch records must be a JSON array of objects")
            if not isinstance(bindings, dict):
                raise ValueError("Batch bindings must be a JSON object")
            self._project.web.create_variants(
                self._active_sequence_id,
                self._web_clip_id,
                records,
                {str(key): str(value) for key, value in bindings.items()},
                name_template=name_template or "版本 {index}",
                actor="human",
            )
            self.projectStateChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(QUrl, str, str)
    def createBatchVariantsFromFile(
        self,
        source_url: QUrl,
        bindings_json: str,
        name_template: str,
    ) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            records = self._project.web.read_variant_records(source_url.toLocalFile())
            bindings = json.loads(bindings_json)
            if not isinstance(bindings, dict):
                raise ValueError("Batch bindings must be a JSON object")
            self._project.web.create_variants(
                self._active_sequence_id,
                self._web_clip_id,
                records,
                {str(key): str(value) for key, value in bindings.items()},
                name_template=name_template or "版本 {index}",
                actor="human",
            )
            self.projectStateChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(QUrl)
    def inspectRebind(self, source_url: QUrl) -> None:
        if not self._project or not self._web_asset_id:
            return
        try:
            self._pending_rebind_source = source_url.toLocalFile()
            report = self._project.web.rebind_asset(
                self._web_asset_id,
                self._pending_rebind_source,
                dry_run=True,
            )
            self._rebind_report = report.model_dump(mode="json")
            self.webStateChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(bool)
    def commitRebind(self, allow_conflicts: bool) -> None:
        if not self._project or not self._web_asset_id or not self._pending_rebind_source:
            return
        try:
            report = self._project.web.rebind_asset(
                self._web_asset_id,
                self._pending_rebind_source,
                dry_run=False,
                allow_conflicts=allow_conflicts,
            )
            self._rebind_report = report.model_dump(mode="json")
            self.projectStateChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(QUrl, str, int, str, bool)
    def exportSelected(
        self,
        output_url: QUrl,
        format_name: str,
        time_ms: int,
        background: str,
        overwrite: bool,
    ) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            command = ExportWebClipCommand(
                sequence_id=self._active_sequence_id,
                clip_id=self._web_clip_id,
                output_path=output_url.toLocalFile(),
                format=cast(WebExportFormat, format_name),
                time_ms=max(0, int(time_ms)),
                background=background or "#000000",
                overwrite=overwrite,
            )
            self._start_task(command, sequence_id=self._active_sequence_id)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def renderSelected(self) -> None:
        if self._web_clip_id:
            self._projector.schedule_preview_graph()

    def _set_locks(self, layer_id: str, fields: list[str], locked: bool) -> None:
        if not self._project or not self._web_clip_id:
            return
        try:
            updated = self._project.web.set_field_locks(
                self._active_sequence_id,
                self._web_clip_id,
                layer_id,
                fields,
                locked,
                expected_revision=int(self._web_state.get("revision", 0)),
            )
            self._accept_state(updated)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    def _accept_state(self, state) -> None:
        self._web_state = state.model_dump(mode="json")
        self._refresh_runtime_state()
        self._refresh_layers()
        self.webStateChanged.emit()
        self.historyChanged.emit()
        self._projector.schedule_preview_graph()

    def _refresh_runtime_state(self) -> None:
        if self._project and self._web_clip_id:
            self._runtime_web_state = self._project.web.runtime_state(
                self._active_sequence_id,
                self._web_clip_id,
            )
            self._active_layout_id = str(
                (self._runtime_web_state.get("layout") or {}).get("id") or ""
            )
        else:
            self._runtime_web_state = {}
            self._active_layout_id = ""

    def _selected_clip(self):
        if self._editor is None or not self._web_clip_id:
            return None
        return next(
            (item for item in self._editor.state.clips if item.id == self._web_clip_id),
            None,
        )

    def _time_ms_for_frame(self, frame: int) -> int:
        clip = self._selected_clip()
        if clip is None or self._editor is None:
            raise RuntimeError("No editable web clip is selected")
        profile = self._editor.state.sequence.profile
        local_frame = max(0, min(clip.duration - 1, int(frame) - clip.timeline_start))
        consumed = round(local_frame * abs(clip.speed_numerator) / clip.speed_denominator)
        source_frame = clip.source_in + consumed if clip.speed_numerator > 0 else clip.source_in - consumed
        return max(0, round(source_frame * 1000 / profile.fps))

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
    def refresh(self) -> None:
        self._refresh()

    @Slot()
    def _refresh(self) -> None:
        previous_clip_id = self._web_clip_id
        self._web_clip_id = ""
        self._web_asset_id = ""
        self._web_entry_url = ""
        self._web_manifest = {}
        self._web_state = {}
        self._runtime_web_state = {}
        self._active_layout_id = ""
        if self._project and self._editor and self._selected_clip_ids:
            clip_id = self._selected_clip_ids[-1]
            clip = next((item for item in self._editor.state.clips if item.id == clip_id), None)
            if clip is not None:
                asset = self._documents.get_asset(clip.asset_id)
                if asset.kind == AssetKind.WEB:
                    spec = self._documents.get_web_asset_spec(asset.id)
                    state = self._editor.state.web_states[clip.id]
                    url = QUrl.fromLocalFile(str(self._documents.resolve_asset_path(asset)))
                    url.setQuery(QUrlQuery("capture=1"))
                    self._web_clip_id = clip.id
                    self._web_asset_id = asset.id
                    self._web_entry_url = url.toString()
                    self._web_manifest = spec.manifest.model_dump(mode="json")
                    self._web_state = state.model_dump(mode="json")
                    self._refresh_runtime_state()
        if self._web_clip_id != previous_clip_id:
            self._browser_values = {}
        known = [layer["id"] for layer in self._web_manifest.get("layers", [])]
        if self._selected_web_layer_id not in known:
            self._selected_web_layer_id = known[0] if known else ""
        if not self._web_clip_id:
            self._web_edit_mode = False
            self._selected_web_layer_id = ""
        self._refresh_layers()
        self.webStateChanged.emit()

    def _refresh_layers(self) -> None:
        overrides = self._runtime_web_state.get("layers", {})
        locks = self._web_state.get("locks", {})
        animations = self._web_state.get("animations", {})
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
                    and set(layer.get("editable") or []).issubset(
                        set(locks.get(layer["id"]) or [])
                    ),
                    "keyframeCount": sum(
                        len(track.get("keyframes") or [])
                        for track in animations.get(layer["id"], {}).values()
                    ),
                    "enterMs": value("enter_ms", 0),
                    "exitMs": value("exit_ms", self._web_manifest.get("timeline", {}).get("duration_ms", 0)),
                    "delayMs": value("delay_ms", 0),
                    "durationMs": value("duration_ms", 0),
                }
            )
        self._web_layer_model.set_items(rows)
