from __future__ import annotations

from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.application.web_field_validation import WebFieldValidator
from mediaflow.domain.web_manifest import EditableMediaManifest, WebLayerManifest
from mediaflow.domain.web_manifest_primitives import (
    WebEditableField,
    WebParameter,
    WebScene,
)
from mediaflow.domain.web_media_sources import WebMediaSourcesManifest
from mediaflow.domain.web_state import (
    WebClipState,
    WebLayerOverride,
    WebRebindConflict,
    WebSceneState,
)

ConflictKind = Literal[
    "removed-layer",
    "removed-field",
    "removed-scene",
    "removed-parameter",
    "incompatible-value",
    "out-of-range-keyframe",
    "removed-data-field",
    "removed-theme-variable",
    "removed-variant",
    "removed-media-source",
]
ConflictResolution = Literal["drop", "default"]


class _ConflictCollector:
    def __init__(self) -> None:
        self._conflicts: dict[str, WebRebindConflict] = {}

    def add(
        self,
        path: str,
        kind: ConflictKind,
        message: str,
        value: JsonValue,
        resolution: ConflictResolution,
    ) -> None:
        self._conflicts.setdefault(
            path,
            WebRebindConflict(
                path=path,
                kind=kind,
                message=message,
                current_value=value,
                allowed_resolutions=(resolution,),
            ),
        )

    def sorted(self) -> list[WebRebindConflict]:
        return [self._conflicts[path] for path in sorted(self._conflicts)]


class WebRebindConflictDetector:
    """Compares persisted clip state with one replacement package contract."""

    def __init__(
        self,
        old_manifest: EditableMediaManifest,
        new_manifest: EditableMediaManifest,
        new_media_sources: WebMediaSourcesManifest,
    ) -> None:
        self.old_layers = {item.id: item for item in old_manifest.layers}
        self.new_layers = {item.id: item for item in new_manifest.layers}
        self.new_scenes = {item.id: item for item in new_manifest.scenes}
        self.new_variants = {item.id for item in new_manifest.variants}
        self.new_themes = {item.id: item for item in new_manifest.theme_variables}
        self.old_parameters = {
            item.descriptor.id: item for item in old_manifest.parameters
        }
        self.new_parameters = {
            item.descriptor.id: item for item in new_manifest.parameters
        }
        self.new_data = {item.id: item for item in new_manifest.data_fields}
        self.new_media_ids = {item.id for item in new_media_sources.sources}

    def detect(
        self,
        affected: list[tuple[str, WebClipState]],
    ) -> list[WebRebindConflict]:
        conflicts = _ConflictCollector()
        for _sequence_id, state in affected:
            clip_root = f"clips.{state.clip_id}"
            self._selection_conflicts(conflicts, clip_root, state)
            self._theme_conflicts(conflicts, clip_root, state)
            self._global_parameter_conflicts(conflicts, clip_root, state)
            for scene_id, scene_state in state.scenes.items():
                self._scene_conflicts(
                    conflicts,
                    clip_root,
                    scene_id,
                    scene_state,
                )
        return conflicts.sorted()

    def _selection_conflicts(
        self,
        conflicts: _ConflictCollector,
        clip_root: str,
        state: WebClipState,
    ) -> None:
        if state.scene_id and state.scene_id not in self.new_scenes:
            conflicts.add(
                f"{clip_root}.scene_id",
                "removed-scene",
                f"Selected scene {state.scene_id} was removed",
                state.scene_id,
                "default",
            )
        if state.variant and state.variant.id not in self.new_variants:
            conflicts.add(
                f"{clip_root}.variant",
                "removed-variant",
                f"Selected variant {state.variant.id} was removed",
                state.variant.id,
                "default",
            )

    def _theme_conflicts(
        self,
        conflicts: _ConflictCollector,
        clip_root: str,
        state: WebClipState,
    ) -> None:
        for theme_id, value in state.theme.items():
            path = f"{clip_root}.theme.{theme_id}"
            definition = self.new_themes.get(theme_id)
            if definition is None:
                conflicts.add(
                    path,
                    "removed-theme-variable",
                    f"Theme variable {theme_id} was removed",
                    cast(JsonValue, value),
                    "drop",
                )
                continue
            try:
                if definition.kind == "number":
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        raise ValueError("numeric value required")
                elif not isinstance(value, str):
                    raise ValueError("text value required")
                WebFieldValidator.constraint(
                    theme_id,
                    "theme",
                    value,
                    definition.constraints,
                )
            except ValueError as error:
                conflicts.add(
                    path,
                    "incompatible-value",
                    str(error),
                    cast(JsonValue, value),
                    "default",
                )

    def _global_parameter_conflicts(
        self,
        conflicts: _ConflictCollector,
        clip_root: str,
        state: WebClipState,
    ) -> None:
        for parameter_id in set(state.parameters) | set(state.parameter_locks):
            path = f"{clip_root}.parameters.{parameter_id}"
            definition = self.new_parameters.get(parameter_id)
            if definition is None or definition.binding.scope != "global":
                conflicts.add(
                    path,
                    "removed-parameter",
                    f"Global parameter {parameter_id} was removed or changed scope",
                    cast(JsonValue, state.parameters.get(parameter_id)),
                    "drop",
                )
                continue
            if parameter_id not in state.parameters:
                continue
            value = cast(JsonValue, state.parameters[parameter_id])
            try:
                definition.descriptor.validate_value(value)
            except ValueError as error:
                conflicts.add(
                    path,
                    "incompatible-value",
                    str(error),
                    value,
                    "default",
                )

    def _scene_conflicts(
        self,
        conflicts: _ConflictCollector,
        clip_root: str,
        scene_id: str,
        state: WebSceneState,
    ) -> None:
        scene_root = f"{clip_root}.scenes.{scene_id}"
        definition = self.new_scenes.get(scene_id)
        if definition is None:
            conflicts.add(
                scene_root,
                "removed-scene",
                f"Scene {scene_id} was removed",
                cast(JsonValue, state.model_dump(mode="json")),
                "drop",
            )
            return
        self._layer_conflicts(conflicts, scene_root, definition, state)
        self._scene_parameter_conflicts(conflicts, scene_root, definition, state)
        self._data_conflicts(conflicts, scene_root, state)

    def _layer_conflicts(
        self,
        conflicts: _ConflictCollector,
        scene_root: str,
        scene: WebScene,
        state: WebSceneState,
    ) -> None:
        layer_ids = set(state.layers) | set(state.animations) | set(state.locks)
        for layer_id in layer_ids:
            layer_root = f"{scene_root}.layers.{layer_id}"
            layer = self.new_layers.get(layer_id)
            if layer is None:
                self._removed_layer(conflicts, layer_root, layer_id, state)
                continue
            old_layer = self.old_layers.get(layer_id)
            if old_layer is not None and old_layer.kind != layer.kind:
                conflicts.add(
                    layer_root,
                    "incompatible-value",
                    f"Layer {layer_id} changed kind",
                    old_layer.kind,
                    "default",
                )
                continue
            self._removed_layer_fields(conflicts, layer_root, layer, state)
            self._layer_override_conflicts(conflicts, layer_root, layer, state)
            self._layer_animation_conflicts(
                conflicts,
                layer_root,
                layer,
                scene,
                state,
            )

    @staticmethod
    def _removed_layer(
        conflicts: _ConflictCollector,
        layer_root: str,
        layer_id: str,
        state: WebSceneState,
    ) -> None:
        conflicts.add(
            layer_root,
            "removed-layer",
            f"Layer {layer_id} was removed",
            cast(
                JsonValue,
                {
                    "override": (
                        state.layers[layer_id].model_dump(mode="json", exclude_none=True)
                        if layer_id in state.layers
                        else None
                    ),
                    "animations": {
                        key: value.model_dump(mode="json")
                        for key, value in state.animations.get(layer_id, {}).items()
                    },
                    "locks": list(state.locks.get(layer_id, ())),
                },
            ),
            "drop",
        )

    @staticmethod
    def _removed_layer_fields(
        conflicts: _ConflictCollector,
        layer_root: str,
        layer: WebLayerManifest,
        state: WebSceneState,
    ) -> None:
        layer_id = layer.id
        used_fields = (
            state.layers.get(layer_id, WebLayerOverride()).changed_fields()
            | set(state.animations.get(layer_id, {}))
            | set(state.locks.get(layer_id, ()))
        )
        for field in sorted(used_fields - set(layer.editable)):
            conflicts.add(
                f"{layer_root}.{field}",
                "removed-field",
                f"Layer field {layer_id}.{field} is no longer editable",
                None,
                "drop",
            )

    def _layer_override_conflicts(
        self,
        conflicts: _ConflictCollector,
        layer_root: str,
        layer: WebLayerManifest,
        state: WebSceneState,
    ) -> None:
        override = state.layers.get(layer.id)
        if override is None:
            return
        for field, value in override.model_dump(exclude_none=True).items():
            if field not in layer.editable:
                continue
            path = f"{layer_root}.{field}"
            try:
                WebFieldValidator.layer_value(
                    layer.id,
                    field,
                    value,
                    layer.constraints.get(cast(WebEditableField, field)),
                )
                if field == "image" and value not in self.new_media_ids:
                    conflicts.add(
                        path,
                        "removed-media-source",
                        f"Image source {value} was removed",
                        cast(JsonValue, value),
                        "default",
                    )
            except ValueError as error:
                conflicts.add(
                    path,
                    "incompatible-value",
                    str(error),
                    cast(JsonValue, value),
                    "default",
                )

    @staticmethod
    def _layer_animation_conflicts(
        conflicts: _ConflictCollector,
        layer_root: str,
        layer: WebLayerManifest,
        scene: WebScene,
        state: WebSceneState,
    ) -> None:
        for field, track in state.animations.get(layer.id, {}).items():
            if field not in layer.editable:
                continue
            path = f"{layer_root}.{field}.animation"
            try:
                if track.keyframes[-1].time_ms >= scene.duration_ms:
                    raise OverflowError
                for keyframe in track.keyframes:
                    WebFieldValidator.layer_value(
                        layer.id,
                        field,
                        keyframe.value,
                        layer.constraints.get(field),
                    )
            except OverflowError:
                conflicts.add(
                    path,
                    "out-of-range-keyframe",
                    f"Animation {layer.id}.{field} exceeds scene duration",
                    cast(JsonValue, track.model_dump(mode="json")),
                    "default",
                )
            except ValueError as error:
                conflicts.add(
                    path,
                    "incompatible-value",
                    str(error),
                    cast(JsonValue, track.model_dump(mode="json")),
                    "default",
                )

    def _scene_parameter_conflicts(
        self,
        conflicts: _ConflictCollector,
        scene_root: str,
        scene: WebScene,
        state: WebSceneState,
    ) -> None:
        parameter_ids = (
            set(state.parameters)
            | set(state.parameter_animations)
            | set(state.parameter_locks)
        )
        for parameter_id in parameter_ids:
            path = f"{scene_root}.parameters.{parameter_id}"
            definition = self.new_parameters.get(parameter_id)
            if definition is None:
                conflicts.add(
                    path,
                    "removed-parameter",
                    f"Parameter {parameter_id} was removed",
                    cast(JsonValue, state.parameters.get(parameter_id)),
                    "drop",
                )
                continue
            old_definition = self.old_parameters.get(parameter_id)
            if self._parameter_scope_changed(old_definition, definition):
                conflicts.add(
                    path,
                    "removed-parameter",
                    f"Parameter {parameter_id} changed scope",
                    cast(JsonValue, state.parameters.get(parameter_id)),
                    "drop",
                )
                continue
            self._parameter_value_conflict(conflicts, path, definition, state)
            self._parameter_animation_conflict(
                conflicts,
                path,
                parameter_id,
                definition,
                scene,
                state,
            )

    @staticmethod
    def _parameter_scope_changed(
        old_definition: WebParameter | None,
        new_definition: WebParameter,
    ) -> bool:
        return (
            old_definition is not None
            and old_definition.binding.scope != new_definition.binding.scope
        )

    @staticmethod
    def _parameter_value_conflict(
        conflicts: _ConflictCollector,
        path: str,
        definition: WebParameter,
        state: WebSceneState,
    ) -> None:
        parameter_id = definition.descriptor.id
        if parameter_id not in state.parameters:
            return
        value = cast(JsonValue, state.parameters[parameter_id])
        try:
            definition.descriptor.validate_value(value)
        except ValueError as error:
            conflicts.add(path, "incompatible-value", str(error), value, "default")

    @staticmethod
    def _parameter_animation_conflict(
        conflicts: _ConflictCollector,
        parameter_root: str,
        parameter_id: str,
        definition: WebParameter,
        scene: WebScene,
        state: WebSceneState,
    ) -> None:
        track = state.parameter_animations.get(parameter_id)
        if track is None:
            return
        path = f"{parameter_root}.animation"
        try:
            if (
                definition.descriptor.timeline != "keyframe"
                or track.keyframes[-1].time_ms >= scene.duration_ms
            ):
                raise OverflowError
            for keyframe in track.keyframes:
                definition.descriptor.validate_value(keyframe.value)
        except OverflowError:
            conflicts.add(
                path,
                "out-of-range-keyframe",
                f"Parameter animation {parameter_id} is no longer valid",
                cast(JsonValue, track.model_dump(mode="json")),
                "default",
            )
        except ValueError as error:
            conflicts.add(
                path,
                "incompatible-value",
                str(error),
                cast(JsonValue, track.model_dump(mode="json")),
                "default",
            )

    def _data_conflicts(
        self,
        conflicts: _ConflictCollector,
        scene_root: str,
        state: WebSceneState,
    ) -> None:
        for field_id, value in state.data_snapshot.values.items():
            path = f"{scene_root}.data.{field_id}"
            definition = self.new_data.get(field_id)
            if definition is None:
                conflicts.add(
                    path,
                    "removed-data-field",
                    f"Data field {field_id} was removed",
                    value,
                    "drop",
                )
                continue
            try:
                WebFieldValidator.data_value(definition, value)
                if definition.kind == "media-source" and value not in self.new_media_ids:
                    conflicts.add(
                        path,
                        "removed-media-source",
                        f"Media source {value} was removed",
                        value,
                        "default",
                    )
            except ValueError as error:
                conflicts.add(
                    path,
                    "incompatible-value",
                    str(error),
                    value,
                    "default",
                )
