from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.ports import WebApplicationDocuments, WebPackageValidatorPort
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_clip_editing_service import WebClipEditingService
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import Asset
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebAnimationTrack,
    WebAssetSpec,
    WebClipState,
    WebDataSnapshot,
    WebEditableField,
    WebLayerOverride,
    WebMediaSourcesManifest,
    WebRebindCommitReport,
    WebRebindConflict,
    WebRebindPlan,
    WebRuntimeVariant,
    WebSceneState,
    web_media_sources_have_audio,
)


class WebRebindService:
    """Plans and commits whole-project editable-media package replacement."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        timeline: Callable[[str], TimelineEditor],
        runtime_validator: WebPackageValidatorPort,
        packages: WebPackageService,
        clips: WebClipEditingService,
    ) -> None:
        self.repository = repository
        self._timeline = timeline
        self._runtime_validator = runtime_validator
        self._packages = packages
        self._clips = clips

    def plan_rebind_asset(
        self,
        asset_id: str,
        source: str | Path,
    ) -> WebRebindPlan:
        (
            asset,
            old_spec,
            _package_tree,
            new_manifest,
            new_media_sources,
        ) = self._rebind_inputs(asset_id, source)
        old_layers = {item.id: item for item in old_spec.manifest.layers}
        new_layers = {item.id: item for item in new_manifest.layers}
        affected = self._affected_web_states(asset_id)
        conflicts = self._rebind_conflicts(
            old_spec.manifest,
            new_manifest,
            new_media_sources,
            affected,
        )
        retained_layers = sorted(set(old_layers) & set(new_layers))
        added_layers = sorted(set(new_layers) - set(old_layers))
        removed_layers = sorted(set(old_layers) - set(new_layers))
        payload = {
            "asset_id": asset_id,
            "old_source_hash": old_spec.source_hash,
            "new_source_hash": _package_tree.source_hash,
            "retained_layers": retained_layers,
            "added_layers": added_layers,
            "removed_layers": removed_layers,
            "affected": [
                {
                    "sequence_id": sequence_id,
                    "clip_id": state.clip_id,
                    "revision": state.revision,
                }
                for sequence_id, state in affected
            ],
            "conflicts": [item.model_dump(mode="json") for item in conflicts],
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return WebRebindPlan(
            asset_id=asset_id,
            old_source_hash=old_spec.source_hash,
            new_source_hash=_package_tree.source_hash,
            plan_digest=digest,
            retained_layers=retained_layers,
            added_layers=added_layers,
            removed_layers=removed_layers,
            affected_clips=[state.clip_id for _sequence_id, state in affected],
            conflicts=conflicts,
        )

    def commit_rebind_asset(
        self,
        asset_id: str,
        source: str | Path,
        plan_digest: str,
        resolutions: Mapping[str, str],
    ) -> WebRebindCommitReport:
        plan = self.plan_rebind_asset(asset_id, source)
        if plan.plan_digest != plan_digest:
            raise RuntimeError("Editable media rebind plan changed; inspect the package again")
        conflicts = {item.path: item for item in plan.conflicts}
        if set(resolutions) != set(conflicts):
            missing = sorted(set(conflicts) - set(resolutions))
            unknown = sorted(set(resolutions) - set(conflicts))
            raise ValueError(
                "Editable media rebind needs one decision for every conflict; "
                f"missing={missing}, unknown={unknown}"
            )
        normalized_resolutions: dict[str, Literal["drop", "default"]] = {}
        for path, raw_resolution in resolutions.items():
            conflict = conflicts[path]
            if raw_resolution not in conflict.allowed_resolutions:
                raise ValueError(
                    f"Resolution {raw_resolution} is not allowed for {path}; "
                    f"choose one of {list(conflict.allowed_resolutions)}"
                )
            normalized_resolutions[path] = cast(
                Literal["drop", "default"],
                raw_resolution,
            )

        (
            asset,
            old_spec,
            package_tree,
            new_manifest,
            new_media_sources,
        ) = self._rebind_inputs(asset_id, source)
        if old_spec.source_hash != plan.old_source_hash or package_tree.source_hash != plan.new_source_hash:
            raise RuntimeError("Editable media rebind inputs changed after the reviewed plan")
        old_package_root = web_files.web_package_root(
            self.repository.catalog.resolve_asset_path(asset),
            old_spec.manifest,
        )
        if package_tree.source_hash == old_spec.source_hash:
            return WebRebindCommitReport(
                asset_id=asset_id,
                old_source_hash=old_spec.source_hash,
                new_source_hash=package_tree.source_hash,
                plan_digest=plan.plan_digest,
                migrated_clips=[],
                resolved_paths=normalized_resolutions,
                archive_path=str(old_package_root),
            )
        affected = self._affected_web_states(asset_id)
        migrated = [
            self._migrate_rebind_state(
                state,
                new_manifest,
                package_tree.source_hash,
                set(normalized_resolutions),
            )
            for _sequence_id, state in affected
        ]
        for state in migrated:
            web_contract.validate_media_bindings(
                new_manifest,
                new_media_sources,
                state,
            )

        new_has_audio = web_media_sources_have_audio(new_media_sources)
        main_profile = self.repository.catalog.get_sequence(
            self.repository.catalog.get_project().main_sequence_id
        ).profile
        duration_frames = max(
            1,
            round(new_manifest.duration_ms * main_profile.fps / 1000),
        )
        publication = self._packages.stage_package(
            package_tree,
            new_manifest,
            new_media_sources,
            asset_id=asset_id,
        )

        def commit_rebind() -> None:
            default_variant = publication.manifest.default_variant
            self.repository.catalog.update_asset(
                asset.model_copy(
                    update={
                        "path": str(publication.entry),
                        "metadata": asset.metadata.model_copy(
                            update={
                                "duration_frames": duration_frames,
                                "width": default_variant.canvas.width,
                                "height": default_variant.canvas.height,
                                "has_audio": new_has_audio,
                            }
                        ),
                    }
                )
            )
            self.repository.web.save_web_asset_spec(
                WebAssetSpec(
                    asset_id=asset_id,
                    manifest=publication.manifest,
                    source_hash=publication.source_hash,
                )
            )
            self.repository.web.save_web_clip_states(migrated)

        self._packages.commit_publication(publication, commit_rebind)
        for sequence_id in dict.fromkeys(sequence_id for sequence_id, _state in affected):
            self._timeline(sequence_id).reload()
        return WebRebindCommitReport(
            asset_id=asset_id,
            old_source_hash=old_spec.source_hash,
            new_source_hash=package_tree.source_hash,
            plan_digest=plan.plan_digest,
            migrated_clips=[state.clip_id for state in migrated],
            resolved_paths=normalized_resolutions,
            archive_path=str(old_package_root),
        )

    def _rebind_inputs(
        self,
        asset_id: str,
        source: str | Path,
    ) -> tuple[
        Asset,
        WebAssetSpec,
        web_files.WebPackageTree,
        EditableMediaManifest,
        WebMediaSourcesManifest,
    ]:
        asset = self.repository.catalog.get_asset(asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Asset is not editable web media")
        old_spec = self.repository.web.get_web_asset_spec(asset_id)
        package_tree, new_manifest, new_media_sources = self._packages.read_package_tree(source)
        new_has_audio = web_media_sources_have_audio(new_media_sources)
        if new_has_audio != asset.metadata.has_audio:
            raise ValueError(
                "Rebinding editable media cannot change whether the asset has "
                "native audio; import it as a new asset instead"
            )
        self._packages.preflight_package_tree(package_tree)
        self._runtime_validator.validate(package_tree.root, new_manifest)
        return (
            asset,
            old_spec,
            package_tree,
            new_manifest,
            new_media_sources,
        )

    def _affected_web_states(
        self,
        asset_id: str,
    ) -> list[tuple[str, WebClipState]]:
        affected: list[tuple[str, WebClipState]] = []
        for sequence in self.repository.catalog.list_sequences(include_archived=True):
            timeline = self.repository.timeline.load_timeline(sequence.id)
            for clip in timeline.clips:
                if clip.asset_id == asset_id and clip.id in timeline.web_states:
                    affected.append((sequence.id, timeline.web_states[clip.id]))
        return affected

    def _rebind_conflicts(
        self,
        old_manifest: EditableMediaManifest,
        new_manifest: EditableMediaManifest,
        new_media_sources: WebMediaSourcesManifest,
        affected: list[tuple[str, WebClipState]],
    ) -> list[WebRebindConflict]:
        old_layers = {item.id: item for item in old_manifest.layers}
        new_layers = {item.id: item for item in new_manifest.layers}
        new_scenes = {item.id: item for item in new_manifest.scenes}
        new_variants = {item.id for item in new_manifest.variants}
        new_themes = {item.id: item for item in new_manifest.theme_variables}
        old_parameters = {item.descriptor.id: item for item in old_manifest.parameters}
        new_parameters = {item.descriptor.id: item for item in new_manifest.parameters}
        new_data = {item.id: item for item in new_manifest.data_fields}
        new_media_ids = {item.id for item in new_media_sources.sources}
        conflicts: dict[str, WebRebindConflict] = {}

        def add(
            path: str,
            kind: str,
            message: str,
            value: JsonValue,
            resolution: Literal["drop", "default"],
        ) -> None:
            conflicts.setdefault(
                path,
                WebRebindConflict(
                    path=path,
                    kind=cast(
                        Literal[
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
                        ],
                        kind,
                    ),
                    message=message,
                    current_value=value,
                    allowed_resolutions=(resolution,),
                ),
            )

        for _sequence_id, state in affected:
            clip_root = f"clips.{state.clip_id}"
            if state.scene_id and state.scene_id not in new_scenes:
                add(
                    f"{clip_root}.scene_id",
                    "removed-scene",
                    f"Selected scene {state.scene_id} was removed",
                    state.scene_id,
                    "default",
                )
            if state.variant and state.variant.id not in new_variants:
                add(
                    f"{clip_root}.variant",
                    "removed-variant",
                    f"Selected variant {state.variant.id} was removed",
                    state.variant.id,
                    "default",
                )
            for theme_id, theme_value in state.theme.items():
                path = f"{clip_root}.theme.{theme_id}"
                theme_definition = new_themes.get(theme_id)
                if theme_definition is None:
                    add(
                        path,
                        "removed-theme-variable",
                        f"Theme variable {theme_id} was removed",
                        cast(JsonValue, theme_value),
                        "drop",
                    )
                    continue
                try:
                    if theme_definition.kind == "number":
                        if not isinstance(theme_value, (int, float)) or isinstance(theme_value, bool):
                            raise ValueError("numeric value required")
                    elif not isinstance(theme_value, str):
                        raise ValueError("text value required")
                    self._clips.validate_constraint(
                        theme_id,
                        "theme",
                        theme_value,
                        theme_definition.constraints,
                    )
                except ValueError as error:
                    add(
                        path,
                        "incompatible-value",
                        str(error),
                        cast(JsonValue, theme_value),
                        "default",
                    )
            for parameter_id in set(state.parameters) | set(state.parameter_locks):
                path = f"{clip_root}.parameters.{parameter_id}"
                parameter_definition = new_parameters.get(parameter_id)
                if (
                    parameter_definition is None
                    or parameter_definition.binding.scope != "global"
                ):
                    add(
                        path,
                        "removed-parameter",
                        f"Global parameter {parameter_id} was removed or changed scope",
                        cast(JsonValue, state.parameters.get(parameter_id)),
                        "drop",
                    )
                    continue
                if parameter_id in state.parameters:
                    try:
                        parameter_definition.descriptor.validate_value(
                            cast(JsonValue, state.parameters[parameter_id])
                        )
                    except ValueError as error:
                        add(
                            path,
                            "incompatible-value",
                            str(error),
                            cast(JsonValue, state.parameters[parameter_id]),
                            "default",
                        )
            for scene_id, scene_state in state.scenes.items():
                scene_root = f"{clip_root}.scenes.{scene_id}"
                scene_definition = new_scenes.get(scene_id)
                if scene_definition is None:
                    add(
                        scene_root,
                        "removed-scene",
                        f"Scene {scene_id} was removed",
                        cast(JsonValue, scene_state.model_dump(mode="json")),
                        "drop",
                    )
                    continue
                for layer_id in (
                    set(scene_state.layers) | set(scene_state.animations) | set(scene_state.locks)
                ):
                    layer_root = f"{scene_root}.layers.{layer_id}"
                    layer = new_layers.get(layer_id)
                    if layer is None:
                        add(
                            layer_root,
                            "removed-layer",
                            f"Layer {layer_id} was removed",
                            cast(
                                JsonValue,
                                {
                                    "override": (
                                        scene_state.layers[layer_id].model_dump(
                                            mode="json",
                                            exclude_none=True,
                                        )
                                        if layer_id in scene_state.layers
                                        else None
                                    ),
                                    "animations": {
                                        key: value.model_dump(mode="json")
                                        for key, value in scene_state.animations.get(layer_id, {}).items()
                                    },
                                    "locks": list(scene_state.locks.get(layer_id, ())),
                                },
                            ),
                            "drop",
                        )
                        continue
                    if old_layers.get(layer_id) is not None and old_layers[layer_id].kind != layer.kind:
                        add(
                            layer_root,
                            "incompatible-value",
                            f"Layer {layer_id} changed kind",
                            old_layers[layer_id].kind,
                            "default",
                        )
                        continue
                    used_fields = (
                        scene_state.layers.get(
                            layer_id,
                            WebLayerOverride(),
                        ).changed_fields()
                        | set(scene_state.animations.get(layer_id, {}))
                        | set(scene_state.locks.get(layer_id, ()))
                    )
                    for field in sorted(used_fields - set(layer.editable)):
                        add(
                            f"{layer_root}.{field}",
                            "removed-field",
                            f"Layer field {layer_id}.{field} is no longer editable",
                            None,
                            "drop",
                        )
                    override = scene_state.layers.get(layer_id)
                    if override is not None:
                        for field, value in override.model_dump(exclude_none=True).items():
                            path = f"{layer_root}.{field}"
                            if field not in layer.editable:
                                continue
                            try:
                                self._clips.validated_field_value(
                                    layer_id,
                                    field,
                                    value,
                                    layer.constraints.get(cast(WebEditableField, field)),
                                )
                                if field == "image" and value not in new_media_ids:
                                    add(
                                        path,
                                        "removed-media-source",
                                        f"Image source {value} was removed",
                                        cast(JsonValue, value),
                                        "default",
                                    )
                            except ValueError as error:
                                add(
                                    path,
                                    "incompatible-value",
                                    str(error),
                                    cast(JsonValue, value),
                                    "default",
                                )
                    for field, track in scene_state.animations.get(layer_id, {}).items():
                        path = f"{layer_root}.{field}.animation"
                        if field not in layer.editable:
                            continue
                        try:
                            if track.keyframes[-1].time_ms >= scene_definition.duration_ms:
                                raise OverflowError
                            for keyframe in track.keyframes:
                                self._clips.validated_field_value(
                                    layer_id,
                                    field,
                                    keyframe.value,
                                    layer.constraints.get(field),
                                )
                        except OverflowError:
                            add(
                                path,
                                "out-of-range-keyframe",
                                f"Animation {layer_id}.{field} exceeds scene duration",
                                cast(JsonValue, track.model_dump(mode="json")),
                                "default",
                            )
                        except ValueError as error:
                            add(
                                path,
                                "incompatible-value",
                                str(error),
                                cast(JsonValue, track.model_dump(mode="json")),
                                "default",
                            )
                parameter_ids = (
                    set(scene_state.parameters)
                    | set(scene_state.parameter_animations)
                    | set(scene_state.parameter_locks)
                )
                for parameter_id in parameter_ids:
                    parameter_root = f"{scene_root}.parameters.{parameter_id}"
                    parameter_definition = new_parameters.get(parameter_id)
                    if parameter_definition is None:
                        add(
                            parameter_root,
                            "removed-parameter",
                            f"Parameter {parameter_id} was removed",
                            cast(
                                JsonValue,
                                scene_state.parameters.get(parameter_id),
                            ),
                            "drop",
                        )
                        continue
                    old_definition = old_parameters.get(parameter_id)
                    if (
                        old_definition is not None
                        and old_definition.binding.scope
                        != parameter_definition.binding.scope
                    ):
                        add(
                            parameter_root,
                            "removed-parameter",
                            f"Parameter {parameter_id} changed scope",
                            cast(
                                JsonValue,
                                scene_state.parameters.get(parameter_id),
                            ),
                            "drop",
                        )
                        continue
                    if parameter_id in scene_state.parameters:
                        try:
                            parameter_definition.descriptor.validate_value(
                                cast(
                                    JsonValue,
                                    scene_state.parameters[parameter_id],
                                )
                            )
                        except ValueError as error:
                            add(
                                parameter_root,
                                "incompatible-value",
                                str(error),
                                cast(
                                    JsonValue,
                                    scene_state.parameters[parameter_id],
                                ),
                                "default",
                            )
                    parameter_track = scene_state.parameter_animations.get(parameter_id)
                    if parameter_track is not None:
                        path = f"{parameter_root}.animation"
                        try:
                            if (
                                parameter_definition.descriptor.timeline != "keyframe"
                                or parameter_track.keyframes[-1].time_ms >= scene_definition.duration_ms
                            ):
                                raise OverflowError
                            for keyframe in parameter_track.keyframes:
                                parameter_definition.descriptor.validate_value(keyframe.value)
                        except OverflowError:
                            add(
                                path,
                                "out-of-range-keyframe",
                                f"Parameter animation {parameter_id} is no longer valid",
                                cast(
                                    JsonValue,
                                    parameter_track.model_dump(mode="json"),
                                ),
                                "default",
                            )
                        except ValueError as error:
                            add(
                                path,
                                "incompatible-value",
                                str(error),
                                cast(
                                    JsonValue,
                                    parameter_track.model_dump(mode="json"),
                                ),
                                "default",
                            )
                for field_id, data_value in scene_state.data_snapshot.values.items():
                    path = f"{scene_root}.data.{field_id}"
                    data_definition = new_data.get(field_id)
                    if data_definition is None:
                        add(
                            path,
                            "removed-data-field",
                            f"Data field {field_id} was removed",
                            data_value,
                            "drop",
                        )
                        continue
                    try:
                        self._clips.validate_data_value(
                            data_definition,
                            data_value,
                        )
                        if data_definition.kind == "media-source" and data_value not in new_media_ids:
                            add(
                                path,
                                "removed-media-source",
                                f"Media source {data_value} was removed",
                                data_value,
                                "default",
                            )
                    except ValueError as error:
                        add(
                            path,
                            "incompatible-value",
                            str(error),
                            data_value,
                            "default",
                        )
        return [conflicts[path] for path in sorted(conflicts)]

    def _migrate_rebind_state(
        self,
        state: WebClipState,
        manifest: EditableMediaManifest,
        source_hash: str,
        resolved_paths: set[str],
    ) -> WebClipState:
        clip_root = f"clips.{state.clip_id}"
        new_layers = {item.id: item for item in manifest.layers}
        new_scenes = {item.id: item for item in manifest.scenes}
        new_data_ids = {item.id for item in manifest.data_fields}
        new_parameters = {item.descriptor.id: item for item in manifest.parameters}
        migrated_scenes: dict[str, WebSceneState] = {}
        for scene_id, scene_state in state.scenes.items():
            scene_root = f"{clip_root}.scenes.{scene_id}"
            if scene_id not in new_scenes or scene_root in resolved_paths:
                continue
            layers: dict[str, WebLayerOverride] = {}
            animations: dict[str, dict[WebEditableField, WebAnimationTrack]] = {}
            locks: dict[str, tuple[WebEditableField, ...]] = {}
            for layer_id, layer in new_layers.items():
                layer_root = f"{scene_root}.layers.{layer_id}"
                if layer_root in resolved_paths:
                    continue
                override = scene_state.layers.get(layer_id)
                if override is not None:
                    values = {
                        field: value
                        for field, value in override.model_dump(exclude_none=True).items()
                        if field in layer.editable and f"{layer_root}.{field}" not in resolved_paths
                    }
                    if values:
                        layers[layer_id] = WebLayerOverride.model_validate(values)
                tracks = {
                    field: track
                    for field, track in scene_state.animations.get(layer_id, {}).items()
                    if field in layer.editable
                    and f"{layer_root}.{field}" not in resolved_paths
                    and f"{layer_root}.{field}.animation" not in resolved_paths
                }
                if tracks:
                    animations[layer_id] = tracks
                fields = tuple(
                    field
                    for field in scene_state.locks.get(layer_id, ())
                    if field in layer.editable and f"{layer_root}.{field}" not in resolved_paths
                )
                if fields:
                    locks[layer_id] = fields
            parameters = {
                parameter_id: value
                for parameter_id, value in scene_state.parameters.items()
                if parameter_id in new_parameters
                and new_parameters[parameter_id].binding.scope == "scene"
                and f"{scene_root}.parameters.{parameter_id}" not in resolved_paths
            }
            parameter_animations = {
                parameter_id: track
                for parameter_id, track in scene_state.parameter_animations.items()
                if parameter_id in new_parameters
                and new_parameters[parameter_id].descriptor.timeline == "keyframe"
                and f"{scene_root}.parameters.{parameter_id}" not in resolved_paths
                and f"{scene_root}.parameters.{parameter_id}.animation" not in resolved_paths
            }
            parameter_locks = tuple(
                parameter_id
                for parameter_id in scene_state.parameter_locks
                if parameter_id in new_parameters
                and new_parameters[parameter_id].binding.scope == "scene"
                and f"{scene_root}.parameters.{parameter_id}" not in resolved_paths
            )
            snapshot = WebDataSnapshot(
                source_kind=scene_state.data_snapshot.source_kind,
                source_label=scene_state.data_snapshot.source_label,
                captured_at=scene_state.data_snapshot.captured_at,
                values={
                    field_id: value
                    for field_id, value in scene_state.data_snapshot.values.items()
                    if field_id in new_data_ids and f"{scene_root}.data.{field_id}" not in resolved_paths
                },
            )
            migrated_scenes[scene_id] = WebSceneState(
                layers=layers,
                animations=animations,
                parameters=parameters,
                parameter_animations=parameter_animations,
                parameter_locks=parameter_locks,
                data_snapshot=snapshot,
                locks=locks,
            )
        selected_variant = manifest.variant_for(
            state.variant.id
            if state.variant is not None
            and f"{clip_root}.variant" not in resolved_paths
            and state.variant.id in {item.id for item in manifest.variants}
            else None
        )
        scene_id = (
            state.scene_id
            if state.scene_id in new_scenes and f"{clip_root}.scene_id" not in resolved_paths
            else manifest.scenes[0].id
        )
        return state.model_copy(
            update={
                "scenes": migrated_scenes,
                "theme": {
                    key: value
                    for key, value in state.theme.items()
                    if key in {item.id for item in manifest.theme_variables}
                    and f"{clip_root}.theme.{key}" not in resolved_paths
                },
                "parameters": {
                    key: value
                    for key, value in state.parameters.items()
                    if key in new_parameters
                    and new_parameters[key].binding.scope == "global"
                    and f"{clip_root}.parameters.{key}" not in resolved_paths
                },
                "parameter_locks": tuple(
                    key
                    for key in state.parameter_locks
                    if key in new_parameters
                    and new_parameters[key].binding.scope == "global"
                    and f"{clip_root}.parameters.{key}" not in resolved_paths
                ),
                "variant": WebRuntimeVariant(
                    id=selected_variant.id,
                    width=selected_variant.canvas.width,
                    height=selected_variant.canvas.height,
                ),
                "scene_id": scene_id,
                "source_hash": source_hash,
                "revision": state.revision + 1,
            }
        )
