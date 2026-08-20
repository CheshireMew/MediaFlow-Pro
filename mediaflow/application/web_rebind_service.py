from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.ports import WebApplicationDocuments, WebPackageValidatorPort
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_clip_editing_service import WebClipEditingService
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.application.web_rebind_conflicts import WebRebindConflictDetector
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import Asset
from mediaflow.domain.web_manifest import (
    EditableMediaManifest,
    WebAssetSpec,
)
from mediaflow.domain.web_manifest_primitives import WebEditableField
from mediaflow.domain.web_media_sources import (
    WebMediaSourcesManifest,
    web_media_sources_have_audio,
)
from mediaflow.domain.web_state import (
    WebAnimationTrack,
    WebClipState,
    WebDataSnapshot,
    WebLayerOverride,
    WebRebindCommitReport,
    WebRebindConflict,
    WebRebindPlan,
    WebRuntimeVariant,
    WebSceneState,
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
            self.repository.assets.resolve_asset_path(asset),
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
        main_profile = self.repository.sequences.get_sequence(
            self.repository.projects.get_project().main_sequence_id
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
            self.repository.assets.update_asset(
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
        asset = self.repository.assets.get_asset(asset_id)
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
        for sequence in self.repository.sequences.list_sequences(include_archived=True):
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
        return WebRebindConflictDetector(
            old_manifest,
            new_manifest,
            new_media_sources,
        ).detect(affected)

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
