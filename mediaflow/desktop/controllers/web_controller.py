from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from mediaflow.domain.enums import AssetKind

from .controller_facet import ControllerFacet, report_ui_errors
from .web_editor_context import (
    WebEditorContext,
    coerce_web_descriptor_value,
    find_web_descriptor,
)

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
        self._edit_document: dict = {}
        self._active_variant_id = ""
        self._active_scene_id = ""
        self._selected_web_layer_id = ""
        self._browser_values: dict[str, dict] = {}
        self._browser_revision = 0
        self._browser_edit_mode = False
        self._browser_selected_layer_id = ""
        self._browser_ready = False
        session.events.selectionChanged.connect(self._refresh)
        session.events.projectStateChanged.connect(self._refresh)
        session.events.historyChanged.connect(self._refresh)

    def context_snapshot(self) -> WebEditorContext:
        return WebEditorContext(
            clip_id=self._web_clip_id,
            asset_id=self._web_asset_id,
            manifest=self._web_manifest,
            persistent_state=self._web_state,
            runtime_state=self._runtime_web_state,
            edit_document=self._edit_document,
            active_scene_id=self._active_scene_id,
            selected_layer_id=self._selected_web_layer_id,
        )

    def activate_scene(self, scene_id: str) -> None:
        known = {str(item.get("id")) for item in self._web_manifest.get("scenes", [])}
        if scene_id == self._active_scene_id or scene_id not in known:
            return
        self._active_scene_id = scene_id
        self._refresh_edit_document()
        self._refresh_layers()
        self.webStateChanged.emit()

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
        selected = (self._web_state.get("variant") or {}).get("id") or self._web_manifest.get(
            "default_variant_id"
        )
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
    def editDescriptors(self) -> list[dict]:
        return [dict(item) for item in self._edit_document.get("descriptors", [])]

    @Property("QVariantList", notify=webSelectionChanged)
    def selectedLayerDescriptors(self) -> list[dict]:
        return self._selected_layer_descriptor_rows()

    def _selected_layer_descriptor_rows(self) -> list[dict]:
        prefix = f"{self._selected_web_layer_id}."
        return [
            dict(item)
            for item in self._edit_document.get("descriptors", [])
            if item.get("target") == "layer" and str(item.get("source_id", "")).startswith(prefix)
        ]

    @Property("QVariantList", notify=webStateChanged)
    def parameterDescriptors(self) -> list[dict]:
        return [
            dict(item)
            for item in self._edit_document.get("descriptors", [])
            if item.get("target") == "parameter"
        ]

    @Property("QVariantList", notify=webStateChanged)
    def themeDescriptors(self) -> list[dict]:
        return [
            dict(item) for item in self._edit_document.get("descriptors", []) if item.get("target") == "theme"
        ]

    @Property("QVariantList", notify=webStateChanged)
    def dataDescriptors(self) -> list[dict]:
        return [
            {
                **dict(item),
                "valueText": json.dumps(
                    item.get("value"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
            for item in self._edit_document.get("descriptors", [])
            if item.get("target") == "data"
        ]

    @Property("QVariantMap", notify=webStateChanged)
    def componentData(self) -> dict:
        return dict(self._web_manifest.get("component") or {})

    @Property(str, notify=webStateChanged)
    def capabilitiesJson(self) -> str:
        if not self._session.binding.current or self._session.binding.current.read_only:
            return "{}"
        values = {
            layer["id"]: list(layer.get("editable") or []) for layer in self._web_manifest.get("layers", [])
        }
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    @Property(str, notify=webSelectionChanged)
    def selectedLayerId(self) -> str:
        return self._selected_web_layer_id

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
            self.browserSelectionRequested.emit(self._selected_web_layer_id)

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
                raise ValueError("Browser snapshot must contain revision, edit mode, selection, and layers")
            normalized = {
                str(layer_id): dict(value) for layer_id, value in values.items() if isinstance(value, dict)
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

    @Slot(str, str, "QVariant")
    @report_ui_errors
    def updateDescriptorValue(
        self,
        target: str,
        source_id: str,
        value,
    ) -> None:
        current = self._require_mutable_web_clip()
        descriptor = find_web_descriptor(
            self._edit_document,
            target,
            source_id,
        )
        typed_value = coerce_web_descriptor_value(descriptor, value)
        revision = int(self._web_state.get("revision", 0))
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            updated = current.update_web_clip(
                self._session.binding.active_sequence_id,
                self._web_clip_id,
                {layer_id: {field: typed_value}},
                scene_id=self._active_scene_id,
                expected_revision=revision,
                actor="human",
            )
        elif target == "parameter":
            updated = current.update_web_parameter(
                self._session.binding.active_sequence_id,
                self._web_clip_id,
                source_id,
                typed_value,
                scene_id=self._active_scene_id,
                expected_revision=revision,
                actor="human",
            )
        elif target == "theme":
            updated = current.update_web_theme(
                self._session.binding.active_sequence_id,
                self._web_clip_id,
                {source_id: typed_value},
                expected_revision=revision,
            )
        elif target == "data":
            updated = current.update_web_data(
                self._session.binding.active_sequence_id,
                self._web_clip_id,
                {source_id: typed_value},
                scene_id=self._active_scene_id,
                expected_revision=revision,
            )
        else:
            raise ValueError(f"未知的网页编辑目标：{target}")
        self._accept_state(updated)

    @Slot(str, str, bool)
    @report_ui_errors
    def setDescriptorLocked(
        self,
        target: str,
        source_id: str,
        locked: bool,
    ) -> None:
        current = self._require_mutable_web_clip()
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            updated = current.set_web_field_locks(
                self._session.binding.active_sequence_id,
                self._web_clip_id,
                layer_id,
                [field],
                locked,
                scene_id=self._active_scene_id,
                expected_revision=int(self._web_state.get("revision", 0)),
            )
        elif target == "parameter":
            updated = current.set_web_parameter_lock(
                self._session.binding.active_sequence_id,
                self._web_clip_id,
                source_id,
                locked,
                scene_id=self._active_scene_id,
                expected_revision=int(self._web_state.get("revision", 0)),
            )
        else:
            raise ValueError("只有图层字段和自定义参数支持锁定")
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

    @Slot(str, bool)
    def setLayerLocked(self, layer_id: str, locked: bool) -> None:
        layer = next(
            (item for item in self._web_manifest.get("layers", []) if item["id"] == layer_id),
            None,
        )
        if layer is not None:
            self._set_locks(layer_id, list(layer.get("editable") or []), locked)

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
        self._refresh_edit_document()
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
            self._active_variant_id = str((self._runtime_web_state.get("variant") or {}).get("id") or "")
            known_scenes = {item.get("id") for item in self._web_manifest.get("scenes", [])}
            if self._active_scene_id not in known_scenes:
                self._active_scene_id = str(self._runtime_web_state.get("scene_id") or "")
        else:
            self._runtime_web_state = {}
            self._active_variant_id = ""
            self._active_scene_id = ""

    def _refresh_edit_document(self) -> None:
        if not self._session.binding.current or not self._web_clip_id:
            self._edit_document = {}
            return
        document = self._session.binding.current.describe_web_clip_editing(
            self._session.binding.active_sequence_id,
            self._web_clip_id,
            scene_id=self._active_scene_id or None,
        )
        self._edit_document = document.model_dump(mode="json")

    def _require_mutable_web_clip(self) -> EditorProject:
        self._session._require_writable()
        if not self._web_clip_id:
            raise ValueError("请先选择网页片段")
        current = self._session.binding.current
        if current is None:
            raise RuntimeError("请先打开一个项目")
        return current

    def _set_selected_layer(self, layer_id: str) -> bool:
        known = {layer["id"] for layer in self._web_manifest.get("layers", [])}
        value = layer_id if layer_id in known else ""
        if value == self._selected_web_layer_id:
            return False
        self._selected_web_layer_id = value
        self.webSelectionChanged.emit()
        return True

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
        self._edit_document = {}
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
                    self._web_entry_url = self._session.binding.current.web_editor_entry_url(asset.id)
                    self._web_manifest = spec.manifest.model_dump(mode="json")
                    self._web_state = state.model_dump(mode="json")
                    self._refresh_runtime_state()
                    self._refresh_edit_document()
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
        elif self._session.binding.current is not None and self._session.binding.current.read_only:
            self._web_edit_mode = False
        self._refresh_layers()
        self.webSelectionChanged.emit()
        if self._web_entry_url != previous_entry_url:
            self.entryUrlChanged.emit()
        self.webStateChanged.emit()

    def _refresh_layers(self) -> None:
        runtime_scene = (self._runtime_web_state.get("scenes") or {}).get(self._active_scene_id, {}) or {}
        persistent_scene = (self._web_state.get("scenes") or {}).get(self._active_scene_id, {}) or {}
        overrides = runtime_scene.get("layers", {})
        locks = persistent_scene.get("locks", {})
        animations = persistent_scene.get("animations", {})
        rows = []
        for layer in self._web_manifest.get("layers", []):
            override = dict(overrides.get(layer["id"]) or {})
            rows.append(
                {
                    "layerId": layer["id"],
                    "name": layer["name"],
                    "kind": layer["kind"],
                    "parentId": layer.get("parent_id") or "",
                    "layerVisible": bool(override.get("visible", True)),
                    "allFieldsLocked": bool(layer.get("editable"))
                    and set(layer.get("editable") or []).issubset(set(locks.get(layer["id"]) or [])),
                    "keyframeCount": sum(
                        len(track.get("keyframes") or [])
                        for track in animations.get(layer["id"], {}).values()
                    ),
                }
            )
        self._session.models.web_layers.set_items(rows)
