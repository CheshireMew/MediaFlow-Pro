from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, TypeVar, cast

from pydantic import JsonValue

from mediaflow.application.ports import WebMediaServiceDocuments, WebPackageValidatorPort
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import AssetKind, AssetOrigin
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import Asset, MediaMetadata
from mediaflow.domain.storage_names import require_windows_interop_path
from mediaflow.domain.web_media import (
    CONTINUOUS_ANIMATION_FIELDS,
    EditableMediaManifest,
    WebAnimationTrack,
    WebAssetSpec,
    WebClipState,
    WebDataField,
    WebDataSnapshot,
    WebEasing,
    WebEditableField,
    WebFieldConstraint,
    WebInterpolation,
    WebKeyframe,
    WebLayerOverride,
    WebMediaSourcesManifest,
    WebRebindReport,
    WebRuntimePlayback,
    WebRuntimeVariant,
    WebSceneState,
    WebStateDiff,
    WebVariantResult,
    parse_editable_media_manifest_json,
    web_runtime_state,
)

MANIFEST_FILE_NAME = "editable-media.json"
_WEB_PUBLICATION_SCHEMA_VERSION = 1
_WEB_PUBLICATION_TOKEN_HEX_CHARS = 24
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _WebPackageTree:
    root: Path
    directories: tuple[str, ...]
    files: tuple[str, ...]
    source_hash: str


@dataclass(slots=True)
class _WebPackagePublication:
    asset_id: str
    manifest: EditableMediaManifest
    source_hash: str
    token: str
    staging: Path
    final: Path
    failure: Path
    receipt: Path
    failed_receipt: Path
    published: bool = False

    @property
    def entry(self) -> Path:
        return self.final.joinpath(*PurePosixPath(self.manifest.entry).parts)

    def publish(self) -> None:
        if self.published:
            raise RuntimeError("Editable media package is already published")
        atomic_write_text(
            self.receipt,
            _publication_receipt_json(
                asset_id=self.asset_id,
                source_hash=self.source_hash,
                token=self.token,
                status="pending",
            ),
            durable=True,
        )
        self.staging.replace(self.final)
        self.published = True

    def mark_committed(self) -> None:
        if not self.published or not self.final.is_dir():
            raise RuntimeError("Editable media package was not published")
        atomic_write_text(
            self.receipt,
            _publication_receipt_json(
                asset_id=self.asset_id,
                source_hash=self.source_hash,
                token=self.token,
                status="committed",
            ),
            durable=True,
        )

    def archive_failed(self) -> None:
        package = self.final if self.final.exists() else self.staging
        if package.exists():
            self.failure.parent.mkdir(parents=True, exist_ok=True)
            package.replace(self.failure)
        if self.receipt.exists():
            self.failed_receipt.parent.mkdir(parents=True, exist_ok=True)
            self.receipt.replace(self.failed_receipt)


def _publication_receipt_json(
    *,
    asset_id: str,
    source_hash: str,
    token: str,
    status: Literal["pending", "committed"],
) -> str:
    return json.dumps(
        {
            "schema_version": _WEB_PUBLICATION_SCHEMA_VERSION,
            "asset_id": asset_id,
            "source_hash": source_hash,
            "token": token,
            "directory": f"p-{token}",
            "status": status,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _scan_web_package(package_root: Path) -> _WebPackageTree:
    root = Path(package_root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink() or _is_junction(root):
        raise ValueError("Editable media package root must be a regular directory")
    directories: list[str] = []
    files: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda item: item.name.casefold())
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if entry.is_symlink() or _is_junction(path):
                raise ValueError(
                    f"Editable media packages cannot contain links or junctions: {relative}"
                )
            if entry.is_dir(follow_symlinks=False):
                directories.append(relative)
                stack.append(path)
                continue
            if entry.is_file(follow_symlinks=False):
                mode = entry.stat(follow_symlinks=False).st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError(
                        f"Editable media packages only accept regular files: {relative}"
                    )
                files.append(relative)
                continue
            raise ValueError(
                f"Editable media packages only accept files and directories: {relative}"
            )
    directories.sort()
    files.sort()
    digest = hashlib.sha256()
    inventory = sorted(
        (
            *((relative, b"D") for relative in directories),
            *((relative, b"F") for relative in files),
        ),
        key=lambda item: (item[0], item[1]),
    )
    for relative, entry_type in inventory:
        encoded = relative.encode("utf-8")
        digest.update(entry_type)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        if entry_type == b"D":
            continue
        with root.joinpath(*PurePosixPath(relative).parts).open("rb") as stream:
            content_length = os.fstat(stream.fileno()).st_size
            digest.update(content_length.to_bytes(8, "big"))
            bytes_read = 0
            while chunk := stream.read(1024 * 1024):
                bytes_read += len(chunk)
                digest.update(chunk)
        if bytes_read != content_length:
            raise RuntimeError(
                f"Editable media package changed while it was scanned: {relative}"
            )
    return _WebPackageTree(
        root=root,
        directories=tuple(directories),
        files=tuple(files),
        source_hash=digest.hexdigest(),
    )


def editable_media_source_hash(package_root: Path) -> str:
    return _scan_web_package(package_root).source_hash


def web_package_root(
    entry_path: str | Path,
    manifest: EditableMediaManifest,
) -> Path:
    """Resolve a package root without assuming that its HTML entry is top-level."""

    entry = Path(entry_path).resolve()
    relative = PurePosixPath(manifest.entry)
    root = entry
    for _part in relative.parts:
        root = root.parent
    expected = root.joinpath(*relative.parts).resolve()
    if expected != entry:
        raise ValueError("Editable media entry does not match its package manifest")
    return root


def _copy_web_package_file(source: str, destination: str) -> str:
    return shutil.copy2(source, destination)


class WebMediaService:
    """Owns editable web package imports and instance-level clip state."""

    def __init__(
        self,
        repository: WebMediaServiceDocuments,
        timeline: Callable[[str], TimelineEditor],
        runtime_validator: WebPackageValidatorPort,
    ) -> None:
        self.repository = repository
        self._timeline = timeline
        self._runtime_validator = runtime_validator
        if not repository.read_only:
            self._reconcile_publications()

    def import_package(self, source: str | Path) -> Asset:
        package_tree, manifest = self._read_package_tree(source)
        project = self.repository.catalog.get_project()
        asset_id = new_id()
        publication = self._stage_package(
            package_tree,
            manifest,
            asset_id=asset_id,
        )
        main_sequence = self.repository.catalog.get_sequence(project.main_sequence_id)
        duration_frames = max(
            1,
            round(publication.manifest.duration_ms * main_sequence.profile.fps / 1000),
        )
        default_variant = publication.manifest.default_variant
        asset = Asset(
            id=asset_id,
            project_id=project.id,
            name=(
                publication.manifest.component.name
                if publication.manifest.component
                else package_tree.root.name
            ),
            kind=AssetKind.WEB,
            origin=AssetOrigin.EXTERNAL,
            path=str(publication.entry),
            managed=True,
            metadata=MediaMetadata(
                duration_frames=duration_frames,
                width=default_variant.canvas.width,
                height=default_variant.canvas.height,
                fps_numerator=main_sequence.profile.fps_numerator,
                fps_denominator=main_sequence.profile.fps_denominator,
                has_video=True,
                has_audio=False,
            ),
        )
        return self._commit_package_publication(
            publication,
            lambda: self._save_imported_package(
                asset,
                publication,
            ),
        )

    def _save_imported_package(
        self,
        asset: Asset,
        publication: _WebPackagePublication,
    ) -> Asset:
        stored = self.repository.catalog.add_asset(asset)
        self.repository.web.save_web_asset_spec(
            WebAssetSpec(
                asset_id=asset.id,
                manifest=publication.manifest,
                source_hash=publication.source_hash,
            )
        )
        return stored

    def inspect_asset(self, asset_id: str) -> WebAssetSpec:
        asset = self.repository.catalog.get_asset(asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Asset is not editable web media")
        spec = self.repository.web.get_web_asset_spec(asset_id)
        actual_hash = editable_media_source_hash(
            web_package_root(
                self.repository.catalog.resolve_asset_path(asset),
                spec.manifest,
            )
        )
        if actual_hash != spec.source_hash:
            raise RuntimeError(
                "Editable media package changed after import; rebind it as a new package"
            )
        return spec

    def get_clip(self, clip_id: str) -> WebClipState:
        return self.repository.web.get_web_clip_state(clip_id)

    def update_clip(
        self,
        sequence_id: str,
        clip_id: str,
        updates: Mapping[str, Mapping[str, object]],
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        editor, asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        layers = dict(current_scene.layers)
        manifest_layers = {layer.id: layer for layer in spec.manifest.layers}
        media_source_ids = self._media_source_ids(
            web_package_root(
                self.repository.catalog.resolve_asset_path(asset),
                spec.manifest,
            ),
            spec.manifest,
        )
        for layer_id, patch in updates.items():
            manifest_layer = manifest_layers.get(layer_id)
            if manifest_layer is None:
                raise ValueError(f"Editable media layer is not declared: {layer_id}")
            patch_fields = set(patch)
            unknown = patch_fields - set(WebLayerOverride.model_fields)
            if unknown:
                raise ValueError(f"Unknown editable media fields: {sorted(unknown)}")
            disallowed = patch_fields - set(manifest_layer.editable)
            if disallowed:
                raise ValueError(
                    f"Layer {layer_id} does not allow fields: {sorted(disallowed)}"
                )
            locked = patch_fields & set(current_scene.locks.get(layer_id, ()))
            if actor == "automation" and locked:
                raise PermissionError(
                    f"Editable media fields are locked: {layer_id}/{sorted(locked)}"
                )
            values = layers.get(layer_id, WebLayerOverride()).model_dump()
            values.update(patch)
            candidate = WebLayerOverride.model_validate(values)
            if candidate.image is not None and candidate.image not in media_source_ids:
                raise ValueError(
                    f"Layer {layer_id} image is not declared in the v3 media-sources manifest"
                )
            for field in patch:
                self._validate_constraint(
                    layer_id,
                    field,
                    getattr(candidate, field),
                    manifest_layer.constraints.get(cast(WebEditableField, field)),
                )
            if candidate.changed_fields():
                layers[layer_id] = candidate
            else:
                layers.pop(layer_id, None)

        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"layers": layers})
        updated = current.model_copy(
            update={
                "scenes": scenes,
                "scene_id": resolved_scene_id,
                "revision": current.revision + 1,
            }
        )
        editor.set_web_clip_state(updated, expected_revision=current.revision)
        return editor.state.web_states[clip_id]

    def diff_clip_update(
        self,
        sequence_id: str,
        clip_id: str,
        updates: Mapping[str, Mapping[str, object]],
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "automation",
    ) -> WebStateDiff:
        _editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        resolved = web_runtime_state(current, spec.manifest)
        resolved_scenes = resolved.get("scenes")
        runtime_scene = (
            resolved_scenes.get(resolved_scene_id)
            if isinstance(resolved_scenes, dict)
            else None
        )
        resolved_layers = (
            runtime_scene.get("layers") if isinstance(runtime_scene, dict) else None
        )
        if not isinstance(resolved_layers, dict):
            resolved_layers = {}
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())

        def current_value(layer_id: str, field: str) -> JsonValue:
            layer_values = resolved_layers.get(layer_id)
            if not isinstance(layer_values, dict):
                return None
            return layer_values.get(field)

        locked_paths = [
            f"scenes.{resolved_scene_id}.layers.{layer_id}.{field}"
            for layer_id, patch in updates.items()
            for field in patch
            if actor == "automation" and field in current_scene.locks.get(layer_id, ())
        ]
        return WebStateDiff(
            clip_id=clip_id,
            before_revision=current.revision,
            changes={
                f"scenes.{resolved_scene_id}.layers.{layer_id}.{field}": {
                    "before": current_value(layer_id, field),
                    "after": cast(JsonValue, value),
                }
                for layer_id, patch in updates.items()
                for field, value in patch.items()
                if current_value(layer_id, field) != value
            },
            locked_paths=locked_paths,
        )

    def select_variant(
        self,
        sequence_id: str,
        clip_id: str,
        variant_id: str,
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        variant = spec.manifest.variant_for(variant_id)
        return self._save_state(
            editor,
            current,
            current.model_copy(
                update={
                    "variant": WebRuntimeVariant(
                        id=variant.id,
                        width=variant.canvas.width,
                        height=variant.canvas.height,
                    )
                }
            ),
        )

    def commit_runtime_state(
        self,
        sequence_id: str,
        clip_id: str,
        runtime_state: Mapping[str, object],
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        expected_keys = {
            "scenes",
            "theme",
            "theme_bindings",
            "variant",
            "scene_id",
            "playback",
            "revision",
        }
        if set(runtime_state) != expected_keys:
            raise ValueError(
                "Editable media runtime state must use the complete v3 state contract"
            )
        runtime_revision = runtime_state["revision"]
        if (
            not isinstance(runtime_revision, (int, float))
            or isinstance(runtime_revision, bool)
            or int(runtime_revision) != current.revision
        ):
            raise RuntimeError(
                "Editable media browser revision does not match the persisted clip revision"
            )

        variant_value = runtime_state["variant"]
        if not isinstance(variant_value, Mapping):
            raise ValueError("Editable media runtime variant must be an object")
        variant = spec.manifest.variant_for(str(variant_value.get("id") or ""))
        if (
            int(variant_value.get("width") or 0) != variant.canvas.width
            or int(variant_value.get("height") or 0) != variant.canvas.height
        ):
            raise ValueError("Editable media runtime variant dimensions do not match the manifest")

        scene_id = str(runtime_state["scene_id"])
        scene_definitions = {item.id: item for item in spec.manifest.scenes}
        if scene_id not in scene_definitions:
            raise ValueError(f"Editable media runtime scene is not declared: {scene_id}")
        scenes_value = runtime_state["scenes"]
        if not isinstance(scenes_value, Mapping) or set(scenes_value) != set(scene_definitions):
            raise ValueError("Editable media runtime must return every declared scene")

        theme_bindings = {
            item.id: item.css_variable for item in spec.manifest.theme_variables
        }
        if runtime_state["theme_bindings"] != theme_bindings:
            raise ValueError("Editable media runtime theme bindings do not match the manifest")
        theme_value = runtime_state["theme"]
        if not isinstance(theme_value, Mapping):
            raise ValueError("Editable media runtime theme must be an object")
        theme_defaults = {item.id: item.default for item in spec.manifest.theme_variables}
        if set(theme_value) != set(theme_defaults):
            raise ValueError("Editable media runtime theme must contain every declared variable")
        theme: dict[str, str | float] = {}
        variables = {item.id: item for item in spec.manifest.theme_variables}
        for variable_id, value in theme_value.items():
            variable = variables[str(variable_id)]
            if variable.kind == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"Theme variable {variable_id} must be numeric")
            elif not isinstance(value, str):
                raise ValueError(f"Theme variable {variable_id} must be text")
            self._validate_constraint(
                str(variable_id),
                "theme",
                value,
                variable.constraints,
            )
            if value != theme_defaults[variable_id]:
                theme[str(variable_id)] = cast(str | float, value)

        playback_value = runtime_state["playback"]
        if not isinstance(playback_value, Mapping):
            raise ValueError("Editable media runtime playback must be an object")
        playback = WebRuntimePlayback.model_validate(dict(playback_value))
        media_source_ids = self._media_source_ids(
            web_package_root(
                self.repository.catalog.resolve_asset_path(asset),
                spec.manifest,
            ),
            spec.manifest,
        )
        manifest_layers = {item.id: item for item in spec.manifest.layers}
        data_fields = {item.id: item for item in spec.manifest.data_fields}
        data_defaults = {item.id: item.default for item in spec.manifest.data_fields}
        scenes: dict[str, WebSceneState] = {}
        for current_scene_id, definition in scene_definitions.items():
            scene_value = scenes_value[current_scene_id]
            if not isinstance(scene_value, Mapping) or set(scene_value) != {
                "layers",
                "animations",
                "data",
                "locks",
            }:
                raise ValueError(
                    f"Editable media runtime scene {current_scene_id} is incomplete"
                )
            layers_value = scene_value["layers"]
            if not isinstance(layers_value, Mapping) or set(layers_value) != set(manifest_layers):
                raise ValueError(
                    f"Editable media runtime scene {current_scene_id} must contain every layer"
                )
            layers: dict[str, WebLayerOverride] = {}
            for layer_id, layer_definition in manifest_layers.items():
                layer_value = layers_value[layer_id]
                if not isinstance(layer_value, Mapping):
                    raise ValueError(
                        f"Editable media runtime layer {current_scene_id}/{layer_id} "
                        "must be an object"
                    )
                unknown = set(layer_value) - set(WebLayerOverride.model_fields)
                if unknown:
                    raise ValueError(
                        f"Editable media runtime layer {current_scene_id}/{layer_id} "
                        f"contains unknown fields: {sorted(unknown)}"
                    )
                disallowed = set(layer_value) - set(layer_definition.editable)
                if disallowed:
                    raise ValueError(
                        f"Editable media runtime layer {current_scene_id}/{layer_id} "
                        f"contains non-editable fields: {sorted(disallowed)}"
                    )
                base = spec.manifest.layer_values_for(variant.id, layer_id)
                overrides = {
                    str(field): value
                    for field, value in layer_value.items()
                    if field not in base or value != base[field]
                }
                candidate = WebLayerOverride.model_validate(overrides)
                if candidate.image is not None and candidate.image not in media_source_ids:
                    raise ValueError(
                        f"Layer {layer_id} image is not declared in the v3 media-sources manifest"
                    )
                for field in overrides:
                    self._validate_constraint(
                        layer_id,
                        field,
                        getattr(candidate, field),
                        layer_definition.constraints.get(cast(WebEditableField, field)),
                    )
                if candidate.changed_fields():
                    layers[layer_id] = candidate

            animations_value = scene_value["animations"]
            if not isinstance(animations_value, Mapping):
                raise ValueError("Editable media runtime animations must be an object")
            animations: dict[
                str, dict[WebEditableField, WebAnimationTrack]
            ] = {}
            for layer_id, tracks_value in animations_value.items():
                layer_definition = manifest_layers.get(str(layer_id))
                if layer_definition is None or not isinstance(tracks_value, Mapping):
                    raise ValueError(
                        f"Editable media runtime animation layer is invalid: {layer_id}"
                    )
                tracks: dict[WebEditableField, WebAnimationTrack] = {}
                for field, track_value in tracks_value.items():
                    if field not in layer_definition.editable:
                        raise ValueError(
                            f"Layer {layer_id} does not allow animation field: {field}"
                        )
                    track = WebAnimationTrack.model_validate(track_value)
                    if track.field != field:
                        raise ValueError(
                            f"Editable media animation track key does not match: "
                            f"{layer_id}/{field}"
                        )
                    if track.keyframes[-1].time_ms >= definition.duration_ms:
                        raise ValueError(
                            f"Editable media animation exceeds scene {current_scene_id}"
                        )
                    tracks[cast(WebEditableField, field)] = track
                if tracks:
                    animations[str(layer_id)] = tracks

            data_value = scene_value["data"]
            if not isinstance(data_value, Mapping) or set(data_value) != set(data_fields):
                raise ValueError(
                    f"Editable media runtime scene {current_scene_id} "
                    "must contain every declared data field"
                )
            scene_defaults = dict(data_defaults)
            scene_defaults.update(definition.data)
            data_overrides: dict[str, JsonValue] = {}
            for field_id, value in data_value.items():
                field = data_fields[str(field_id)]
                self._validate_data_value(field, value)
                if field.kind == "media-source" and value not in media_source_ids:
                    raise ValueError(
                        f"Data field {field_id} media source is not declared in "
                        "the v3 media-sources manifest"
                    )
                if value != scene_defaults[field_id]:
                    data_overrides[str(field_id)] = cast(JsonValue, value)
            previous_scene = current.scenes.get(current_scene_id, WebSceneState())
            snapshot = WebDataSnapshot(
                source_kind=previous_scene.data_snapshot.source_kind,
                source_label=previous_scene.data_snapshot.source_label,
                values=data_overrides,
            )

            locks_value = scene_value["locks"]
            if not isinstance(locks_value, Mapping):
                raise ValueError("Editable media runtime locks must be an object")
            locks: dict[str, tuple[WebEditableField, ...]] = {}
            for layer_id, fields_value in locks_value.items():
                layer_definition = manifest_layers.get(str(layer_id))
                if layer_definition is None or not isinstance(fields_value, list):
                    raise ValueError(f"Editable media runtime lock is invalid: {layer_id}")
                if any(not isinstance(field, str) for field in fields_value):
                    raise ValueError(f"Editable media runtime lock is invalid: {layer_id}")
                unknown_fields = set(fields_value) - set(layer_definition.editable)
                if unknown_fields:
                    raise ValueError(
                        f"Editable media runtime lock contains unknown fields: "
                        f"{sorted(unknown_fields)}"
                    )
                locks[str(layer_id)] = tuple(
                    field
                    for field in layer_definition.editable
                    if field in set(fields_value)
                )
            scenes[current_scene_id] = WebSceneState(
                layers=layers,
                animations=animations,
                data_snapshot=snapshot,
                locks=locks,
            )

        return self._save_state(
            editor,
            current,
            current.model_copy(
                update={
                    "scenes": scenes,
                    "theme": theme,
                    "variant": WebRuntimeVariant(
                        id=variant.id,
                        width=variant.canvas.width,
                        height=variant.canvas.height,
                    ),
                    "scene_id": scene_id,
                    "playback": playback,
                }
            ),
        )

    def set_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        field: str,
        time_ms: int,
        value: object,
        *,
        scene_id: str | None = None,
        easing: Mapping[str, object] | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        layer = next((item for item in spec.manifest.layers if item.id == layer_id), None)
        if layer is None:
            raise ValueError(f"Editable media layer is not declared: {layer_id}")
        if field not in layer.editable:
            raise ValueError(f"Layer {layer_id} does not allow field: {field}")
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        scene_definition = next(
            item for item in spec.manifest.scenes if item.id == resolved_scene_id
        )
        if time_ms >= scene_definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        if actor == "automation" and field in current_scene.locks.get(layer_id, ()):
            raise PermissionError(f"Editable media field is locked: {layer_id}/{field}")
        validated_value = self._validated_field_value(layer_id, field, value, layer.constraints.get(field))
        animations = {
            key: dict(tracks) for key, tracks in current_scene.animations.items()
        }
        layer_tracks = animations.setdefault(layer_id, {})
        existing = layer_tracks.get(field)
        keyframes = [
            item for item in (existing.keyframes if existing else []) if item.time_ms != time_ms
        ]
        keyframes.append(
            WebKeyframe(
                time_ms=time_ms,
                value=validated_value,
                easing=WebEasing.model_validate(easing or {}),
            )
        )
        keyframes.sort(key=lambda item: item.time_ms)
        interpolation = "continuous" if field in CONTINUOUS_ANIMATION_FIELDS else "discrete"
        layer_tracks[field] = WebAnimationTrack(
            field=cast(WebEditableField, field),
            interpolation=cast(WebInterpolation, interpolation),
            keyframes=keyframes,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def remove_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        field: str,
        time_ms: int,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        scene_definition = next(
            item for item in spec.manifest.scenes if item.id == resolved_scene_id
        )
        if time_ms >= scene_definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        animations = {
            key: dict(tracks) for key, tracks in current_scene.animations.items()
        }
        tracks = animations.get(layer_id)
        if not tracks or field not in tracks:
            raise KeyError(f"{layer_id}/{field}/{time_ms}")
        remaining = [item for item in tracks[field].keyframes if item.time_ms != time_ms]
        if len(remaining) == len(tracks[field].keyframes):
            raise KeyError(f"{layer_id}/{field}/{time_ms}")
        if remaining:
            tracks[field] = tracks[field].model_copy(update={"keyframes": remaining})
        else:
            tracks.pop(field)
        if not tracks:
            animations.pop(layer_id, None)
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def update_theme(
        self,
        sequence_id: str,
        clip_id: str,
        changes: Mapping[str, str | float],
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        variables = {item.id: item for item in spec.manifest.theme_variables}
        theme = dict(current.theme)
        for variable_id, value in changes.items():
            variable = variables.get(variable_id)
            if variable is None:
                raise ValueError(f"Editable media theme variable is not declared: {variable_id}")
            if variable.kind == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"Theme variable {variable_id} must be numeric")
            elif not isinstance(value, str):
                raise ValueError(f"Theme variable {variable_id} must be text")
            self._validate_constraint(variable_id, "theme", value, variable.constraints)
            theme[variable_id] = value
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"theme": theme}),
        )

    def update_data(
        self,
        sequence_id: str,
        clip_id: str,
        values: Mapping[str, object],
        *,
        scene_id: str | None = None,
        source_kind: Literal["inline", "file", "api"] = "inline",
        source_label: str = "",
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        fields = {item.id: item for item in spec.manifest.data_fields}
        merged = dict(current_scene.data_snapshot.values)
        media_source_ids = self._media_source_ids(
            web_package_root(
                self.repository.catalog.resolve_asset_path(_asset),
                spec.manifest,
            ),
            spec.manifest,
        )
        for field_id, value in values.items():
            field = fields.get(field_id)
            if field is None:
                raise ValueError(f"Editable media data field is not declared: {field_id}")
            self._validate_data_value(field, value)
            if field.kind == "media-source":
                if value not in media_source_ids:
                    raise ValueError(
                        f"Data field {field_id} media source is not declared in "
                        "the v3 media-sources manifest"
                    )
            merged[field_id] = value
        snapshot = WebDataSnapshot(
            source_kind=source_kind,
            source_label=source_label,
            values=merged,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"data_snapshot": snapshot})
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def update_data_from_file(
        self,
        sequence_id: str,
        clip_id: str,
        source: str | Path,
        *,
        scene_id: str | None = None,
        field_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        path = Path(source).expanduser().resolve(strict=True)
        suffix = path.suffix.lower()
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        elif suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                payload = list(csv.DictReader(stream))
        else:
            raise ValueError("Editable media data snapshots accept .json or .csv files")
        _editor, _asset, spec, _current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        declared = {item.id for item in spec.manifest.data_fields}
        if field_id is not None:
            values = {field_id: payload}
        elif isinstance(payload, dict) and set(payload).issubset(declared):
            values = payload
        elif len(declared) == 1:
            values = {next(iter(declared)): payload}
        else:
            raise ValueError(
                "Data file must contain declared field IDs or specify field_id when multiple fields exist"
            )
        return self.update_data(
            sequence_id,
            clip_id,
            values,
            scene_id=scene_id,
            source_kind="file",
            source_label=str(path),
            expected_revision=expected_revision,
        )

    def set_field_locks(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        fields: list[str],
        locked: bool,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        manifest_layer = next((item for item in spec.manifest.layers if item.id == layer_id), None)
        if manifest_layer is None:
            raise ValueError(f"Editable media layer is not declared: {layer_id}")
        unknown = set(fields) - set(manifest_layer.editable)
        if unknown:
            raise ValueError(f"Cannot lock undeclared fields: {sorted(unknown)}")
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        locks = {key: set(value) for key, value in current_scene.locks.items()}
        target = locks.setdefault(layer_id, set())
        if locked:
            target.update(fields)
        else:
            target.difference_update(fields)
        if not target:
            locks.pop(layer_id, None)
        normalized = {
            key: tuple(field for field in manifest_layer.editable if field in value)
            if key == layer_id
            else tuple(sorted(value))
            for key, value in locks.items()
        }
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"locks": normalized})
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def runtime_state(
        self,
        sequence_id: str,
        clip_id: str,
    ) -> dict:
        _editor, _asset, spec, current = self._clip_context(sequence_id, clip_id, None)
        return web_runtime_state(current, spec.manifest)

    def create_variants(
        self,
        source_sequence_id: str,
        clip_id: str,
        records: list[Mapping[str, object]],
        bindings: Mapping[str, str],
        *,
        name_template: str = "版本 {index}",
        actor: Literal["human", "automation"] = "automation",
    ) -> list[WebVariantResult]:
        if not records:
            raise ValueError("Batch variants require at least one record")
        source = self.repository.timeline.load_timeline(source_sequence_id)
        try:
            source_clip = next(item for item in source.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        source_asset = self.repository.catalog.get_asset(source_clip.asset_id)
        if source_asset.kind != AssetKind.WEB:
            raise ValueError("Batch variants require an editable web clip")
        results: list[WebVariantResult] = []
        sequences = SequenceService(self.repository)
        for index, raw_record in enumerate(records, start=1):
            record = {str(key): value for key, value in raw_record.items()}
            try:
                name = name_template.format(index=index, **record).strip()
            except (KeyError, ValueError) as error:
                raise ValueError(f"Invalid variant name template: {error}") from error
            name = name or f"版本 {index}"
            sequence = sequences.create_short_from_bounds(
                source_sequence_id,
                source_clip.timeline_start,
                source_clip.timeline_end,
                name=name,
            )
            copied = self.repository.timeline.load_timeline(sequence.id)
            candidates = [
                item
                for item in copied.clips
                if item.asset_id == source_clip.asset_id and item.timeline_start == 0
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"Could not identify the copied editable web clip for variant {index}"
                )
            copied_clip = candidates[0]
            scene_layer_updates: dict[str, dict[str, dict[str, object]]] = {}
            theme_updates: dict[str, str | float] = {}
            scene_data_updates: dict[str, dict[str, object]] = {}
            selected_variant_id: str | None = None
            for source_key, target_path in bindings.items():
                if source_key not in record:
                    raise ValueError(f"Variant record is missing field: {source_key}")
                value = record[source_key]
                parts = target_path.split(".")
                if len(parts) == 5 and parts[0] == "scenes" and parts[2] == "layers":
                    scene_layer_updates.setdefault(parts[1], {}).setdefault(parts[3], {})[
                        parts[4]
                    ] = value
                elif len(parts) == 2 and parts[0] == "theme":
                    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                        raise ValueError(f"Theme binding {target_path} needs text or a number")
                    theme_updates[parts[1]] = value
                elif len(parts) == 4 and parts[0] == "scenes" and parts[2] == "data":
                    scene_data_updates.setdefault(parts[1], {})[parts[3]] = value
                elif target_path == "variant.id":
                    selected_variant_id = str(value)
                else:
                    raise ValueError(f"Unsupported variant binding target: {target_path}")
            state = self.get_clip(copied_clip.id)
            for target_scene_id, layer_updates in scene_layer_updates.items():
                state = self.update_clip(
                    sequence.id,
                    copied_clip.id,
                    layer_updates,
                    scene_id=target_scene_id,
                    expected_revision=state.revision,
                    actor=actor,
                )
            if theme_updates:
                state = self.update_theme(
                    sequence.id,
                    copied_clip.id,
                    theme_updates,
                    expected_revision=state.revision,
                )
            for target_scene_id, data_updates in scene_data_updates.items():
                state = self.update_data(
                    sequence.id,
                    copied_clip.id,
                    data_updates,
                    scene_id=target_scene_id,
                    expected_revision=state.revision,
                )
            if selected_variant_id is not None:
                state = self.select_variant(
                    sequence.id,
                    copied_clip.id,
                    selected_variant_id,
                    expected_revision=state.revision,
                )
            editor = self._timeline(sequence.id)
            state = self._save_state(
                editor,
                state,
                state.model_copy(update={"batch_name": name}),
            )
            results.append(
                WebVariantResult(
                    sequence_id=sequence.id,
                    clip_id=copied_clip.id,
                    name=name,
                    revision=state.revision,
                )
            )
        return results

    @staticmethod
    def read_variant_records(source: str | Path) -> list[Mapping[str, object]]:
        path = Path(source).expanduser().resolve(strict=True)
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise ValueError("Batch variant JSON must be an array of objects")
            return [
                {str(key): value for key, value in item.items()}
                for item in payload
                if isinstance(item, dict)
            ]
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                return list(csv.DictReader(stream))
        raise ValueError("Batch variant sources accept .json or .csv files")

    def rebind_asset(
        self,
        asset_id: str,
        source: str | Path,
        *,
        dry_run: bool = True,
        allow_conflicts: bool = False,
    ) -> WebRebindReport:
        asset = self.repository.catalog.get_asset(asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Asset is not editable web media")
        old_spec = self.repository.web.get_web_asset_spec(asset_id)
        package_tree, new_manifest = self._read_package_tree(source)
        self._preflight_package_tree(package_tree)
        self._runtime_validator.validate(package_tree.root, new_manifest)
        new_hash = package_tree.source_hash
        old_package_root = web_package_root(
            self.repository.catalog.resolve_asset_path(asset),
            old_spec.manifest,
        )
        old_layers = {item.id: item for item in old_spec.manifest.layers}
        new_layers = {item.id: item for item in new_manifest.layers}
        retained = sorted(set(old_layers) & set(new_layers))
        added = sorted(set(new_layers) - set(old_layers))
        removed = sorted(set(old_layers) - set(new_layers))
        affected: list[tuple[str, WebClipState]] = []
        conflicts: list[str] = []
        new_scene_ids = {item.id for item in new_manifest.scenes}
        new_variant_ids = {item.id for item in new_manifest.variants}
        new_theme_ids = {item.id for item in new_manifest.theme_variables}
        new_data_ids = {item.id for item in new_manifest.data_fields}
        for sequence in self.repository.catalog.list_sequences(include_archived=True):
            timeline = self.repository.timeline.load_timeline(sequence.id)
            for clip in timeline.clips:
                if clip.asset_id != asset_id or clip.id not in timeline.web_states:
                    continue
                state = timeline.web_states[clip.id]
                affected.append((sequence.id, state))
                for scene_id, scene_state in state.scenes.items():
                    if scene_id not in new_scene_ids:
                        conflicts.append(f"{clip.id}: scene {scene_id} was removed")
                        continue
                    for layer_id in removed:
                        if (
                            layer_id in scene_state.layers
                            or layer_id in scene_state.animations
                            or layer_id in scene_state.locks
                        ):
                            conflicts.append(
                                f"{clip.id}: scene {scene_id} removed layer "
                                f"{layer_id} has instance state"
                            )
                    for layer_id in retained:
                        allowed = set(new_layers[layer_id].editable)
                        used = scene_state.layers.get(
                            layer_id, WebLayerOverride()
                        ).changed_fields()
                        used |= set(scene_state.animations.get(layer_id, {}))
                        used |= set(scene_state.locks.get(layer_id, ()))
                        removed_fields = used - allowed
                        for field in sorted(removed_fields):
                            conflicts.append(
                                f"{clip.id}: scene {scene_id} field "
                                f"{layer_id}.{field} is no longer editable"
                            )
                    for key in sorted(
                        set(scene_state.data_snapshot.values) - new_data_ids
                    ):
                        conflicts.append(
                            f"{clip.id}: scene {scene_id} data field {key} was removed"
                        )
                if state.variant and state.variant.id not in new_variant_ids:
                    conflicts.append(
                        f"{clip.id}: selected variant {state.variant.id} was removed"
                    )
                for key in sorted(set(state.theme) - new_theme_ids):
                    conflicts.append(f"{clip.id}: theme variable {key} was removed")
        report = WebRebindReport(
            asset_id=asset_id,
            old_source_hash=old_spec.source_hash,
            new_source_hash=new_hash,
            retained_layers=retained,
            added_layers=added,
            removed_layers=removed,
            affected_clips=[state.clip_id for _sequence_id, state in affected],
            conflicts=conflicts,
            archive_path=str(old_package_root),
        )
        if dry_run or new_hash == old_spec.source_hash:
            return report
        if conflicts and not allow_conflicts:
            raise ValueError(
                "Editable media rebind has state conflicts; inspect with dry_run or allow conflicts: "
                + "; ".join(conflicts)
            )
        main_profile = self.repository.catalog.get_sequence(
            self.repository.catalog.get_project().main_sequence_id
        ).profile
        duration_frames = max(
            1,
            round(new_manifest.duration_ms * main_profile.fps / 1000),
        )
        migrated: list[WebClipState] = []
        for _sequence_id, state in affected:
            migrated_scenes: dict[str, WebSceneState] = {}
            for scene_id, scene_state in state.scenes.items():
                if scene_id not in new_scene_ids:
                    continue
                layers: dict[str, WebLayerOverride] = {}
                animations: dict[str, dict] = {}
                locks: dict[str, tuple] = {}
                for layer_id in retained:
                    allowed = set(new_layers[layer_id].editable)
                    current_override = scene_state.layers.get(layer_id)
                    if current_override is not None:
                        values = {
                            key: value
                            for key, value in current_override.model_dump(
                                exclude_none=True
                            ).items()
                            if key in allowed
                        }
                        if values:
                            layers[layer_id] = WebLayerOverride.model_validate(values)
                    tracks = {
                        field: track
                        for field, track in scene_state.animations.get(
                            layer_id, {}
                        ).items()
                        if field in allowed
                    }
                    if tracks:
                        animations[layer_id] = tracks
                    fields = tuple(
                        field
                        for field in scene_state.locks.get(layer_id, ())
                        if field in allowed
                    )
                    if fields:
                        locks[layer_id] = fields
                snapshot = WebDataSnapshot(
                    source_kind=scene_state.data_snapshot.source_kind,
                    source_label=scene_state.data_snapshot.source_label,
                    captured_at=scene_state.data_snapshot.captured_at,
                    values={
                        key: value
                        for key, value in scene_state.data_snapshot.values.items()
                        if key in new_data_ids
                    },
                )
                migrated_scenes[scene_id] = WebSceneState(
                    layers=layers,
                    animations=animations,
                    data_snapshot=snapshot,
                    locks=locks,
                )
            selected_variant = (
                new_manifest.variant_for(state.variant.id)
                if state.variant and state.variant.id in new_variant_ids
                else new_manifest.default_variant
            )
            migrated.append(
                state.model_copy(
                    update={
                        "scenes": migrated_scenes,
                        "theme": {
                            key: value for key, value in state.theme.items() if key in new_theme_ids
                        },
                        "variant": WebRuntimeVariant(
                            id=selected_variant.id,
                            width=selected_variant.canvas.width,
                            height=selected_variant.canvas.height,
                        ),
                        "scene_id": (
                            state.scene_id
                            if state.scene_id in new_scene_ids
                            else new_manifest.scenes[0].id
                        ),
                        "source_hash": new_hash,
                        "revision": state.revision + 1,
                    }
                )
            )
        publication = self._stage_package(
            package_tree,
            new_manifest,
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

        self._commit_package_publication(
            publication,
            commit_rebind,
        )
        for sequence_id, _state in affected:
            editor = self._timeline(sequence_id)
            editor.reload()
        return report

    def _clip_context(
        self,
        sequence_id: str,
        clip_id: str,
        expected_revision: int | None,
    ):
        editor = self._timeline(sequence_id)
        state = editor.state
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self.repository.catalog.get_asset(clip.asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Clip is not editable web media")
        spec = self.inspect_asset(asset.id)
        current = state.web_states[clip_id]
        if expected_revision is not None and current.revision != expected_revision:
            raise RuntimeError(
                f"Editable media revision conflict: expected {expected_revision}, "
                f"current {current.revision}"
            )
        return editor, asset, spec, current

    @staticmethod
    def _scene_id(
        state: WebClipState,
        manifest: EditableMediaManifest,
        scene_id: str | None,
    ) -> str:
        resolved = scene_id or state.scene_id or manifest.scenes[0].id
        if resolved not in {item.id for item in manifest.scenes}:
            raise ValueError(f"Editable media scene does not exist: {resolved}")
        return resolved

    @staticmethod
    def _save_state(
        editor: TimelineEditor,
        current: WebClipState,
        candidate: WebClipState,
    ) -> WebClipState:
        updated = candidate.model_copy(update={"revision": current.revision + 1})
        editor.set_web_clip_state(updated, expected_revision=current.revision)
        return editor.state.web_states[current.clip_id]

    @staticmethod
    def _validated_field_value(
        layer_id: str,
        field: str,
        value: object,
        constraint: WebFieldConstraint | None,
    ) -> JsonValue:
        candidate = WebLayerOverride.model_validate({field: value})
        validated = getattr(candidate, field)
        WebMediaService._validate_constraint(layer_id, field, validated, constraint)
        return cast(JsonValue, validated)

    @staticmethod
    def _validate_data_value(field: WebDataField, value: object) -> None:
        field_id = field.id
        kind = field.kind
        valid = {
            "string": isinstance(value, str),
            "date": isinstance(value, str),
            "media-source": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "list": isinstance(value, list),
            "table": isinstance(value, list) and all(isinstance(row, dict) for row in value),
            "json": isinstance(value, (dict, list, str, int, float, bool)) or value is None,
        }.get(kind, False)
        if not valid:
            raise ValueError(f"Data field {field_id} does not match kind {kind}")
        if kind == "table" and field.columns:
            if not isinstance(value, list):
                raise ValueError(f"Data field {field_id} table value must be a list")
            columns = {item.id: item.kind for item in field.columns}
            for index, row in enumerate(value):
                if not isinstance(row, dict):
                    raise ValueError(f"Data field {field_id} row {index} must be an object")
                missing = set(columns) - set(row)
                unknown = set(row) - set(columns)
                if missing or unknown:
                    raise ValueError(
                        f"Data field {field_id} row {index} columns do not match; "
                        f"missing={sorted(missing)}, unknown={sorted(unknown)}"
                    )
                for column_id, column_kind in columns.items():
                    cell = row[column_id]
                    cell_valid = {
                        "string": isinstance(cell, str),
                        "date": isinstance(cell, str),
                        "media-source": isinstance(cell, str),
                        "number": isinstance(cell, (int, float)) and not isinstance(cell, bool),
                        "boolean": isinstance(cell, bool),
                    }[column_kind]
                    if not cell_valid:
                        raise ValueError(
                            f"Data field {field_id} row {index} column {column_id} "
                            f"does not match kind {column_kind}"
                        )

    @staticmethod
    def _read_package_tree(
        source: str | Path,
    ) -> tuple[_WebPackageTree, EditableMediaManifest]:
        requested = Path(source).expanduser()
        if requested.is_symlink() or _is_junction(requested):
            raise ValueError("Editable media package source cannot be a link or junction")
        requested_root = requested if requested.is_dir() else requested.parent
        if requested_root.is_symlink() or _is_junction(requested_root):
            raise ValueError("Editable media package root cannot be a link or junction")
        path = requested.resolve(strict=True)
        package_root = path if path.is_dir() else path.parent
        manifest_path = package_root / MANIFEST_FILE_NAME
        if path.is_file() and path.name != MANIFEST_FILE_NAME:
            raise ValueError(f"Editable media import expects a directory or {MANIFEST_FILE_NAME}")
        tree = _scan_web_package(package_root)
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        manifest = parse_editable_media_manifest_json(
            manifest_path.read_text(encoding="utf-8")
        )
        WebMediaService._validate_files(package_root, manifest)
        return tree, manifest

    @staticmethod
    def read_package(source: str | Path) -> tuple[Path, EditableMediaManifest]:
        tree, manifest = WebMediaService._read_package_tree(source)
        return tree.root, manifest

    def _stage_package(
        self,
        source_tree: _WebPackageTree,
        manifest: EditableMediaManifest,
        *,
        asset_id: str,
    ) -> _WebPackagePublication:
        token = hashlib.sha256(
            f"{asset_id}\0{source_tree.source_hash}\0{new_id()}".encode()
        ).hexdigest()[:_WEB_PUBLICATION_TOKEN_HEX_CHARS]
        project_dir = self.repository.project_dir.resolve()
        staging = project_dir / "staging" / "web" / f"s-{token}"
        final = project_dir / "sources" / "web" / f"p-{token}"
        failure = project_dir / "archive" / "web" / f"f-{token}"
        receipt = project_dir / "sources" / "web" / "receipts" / f"r-{token}.json"
        failed_receipt = project_dir / "archive" / "web" / f"r-{token}.json"
        publication = _WebPackagePublication(
            asset_id=asset_id,
            manifest=manifest,
            source_hash=source_tree.source_hash,
            token=token,
            staging=staging,
            final=final,
            failure=failure,
            receipt=receipt,
            failed_receipt=failed_receipt,
        )
        self._validate_publication_paths(
            source_tree,
            staging,
            final,
            failure,
        )
        for path in (staging, final, failure, receipt, failed_receipt):
            require_windows_interop_path(path)
            if path.exists():
                raise FileExistsError(path)
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(
                source_tree.root,
                staging,
                copy_function=_copy_web_package_file,
            )
            copied_tree = _scan_web_package(staging)
            if (
                copied_tree.directories != source_tree.directories
                or copied_tree.files != source_tree.files
                or copied_tree.source_hash != source_tree.source_hash
            ):
                raise RuntimeError(
                    "Editable media package changed while it was being copied"
                )
            copied_manifest = parse_editable_media_manifest_json(
                (staging / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
            )
            self._validate_files(staging, copied_manifest)
            if copied_manifest != manifest:
                raise RuntimeError(
                    "Editable media manifest changed while it was being copied"
                )
            self._runtime_validator.validate(staging, copied_manifest)
            publication.manifest = copied_manifest
            return publication
        except BaseException as error:
            try:
                publication.archive_failed()
            except BaseException as archive_error:
                raise BaseExceptionGroup(
                    "Editable media staging and failure archival both failed",
                    [error, archive_error],
                ) from error
            raise

    def _preflight_package_tree(self, tree: _WebPackageTree) -> None:
        token = "0" * _WEB_PUBLICATION_TOKEN_HEX_CHARS
        project_dir = self.repository.project_dir.resolve()
        self._validate_publication_paths(
            tree,
            project_dir / "staging" / "web" / f"s-{token}",
            project_dir / "sources" / "web" / f"p-{token}",
            project_dir / "archive" / "web" / f"f-{token}",
        )

    @staticmethod
    def _validate_publication_paths(
        tree: _WebPackageTree,
        *roots: Path,
    ) -> None:
        relative_paths = (*tree.directories, *tree.files)
        for root in roots:
            require_windows_interop_path(root)
            seen: set[str] = set()
            for relative in relative_paths:
                target = root.joinpath(*PurePosixPath(relative).parts)
                require_windows_interop_path(target)
                normalized = str(target).casefold()
                if normalized in seen:
                    raise ValueError(
                        "Editable media package contains colliding Windows paths"
                    )
                seen.add(normalized)

    def _reconcile_publications(self) -> None:
        project_dir = self.repository.project_dir.resolve()
        staging_root = project_dir / "staging" / "web"
        receipt_root = project_dir / "sources" / "web" / "receipts"
        if staging_root.is_dir():
            for staging in sorted(staging_root.iterdir()):
                token = self._publication_token(staging.name, prefix="s-")
                if token is None or not staging.is_dir():
                    continue
                receipt = receipt_root / f"r-{token}.json"
                self._archive_residual(staging, receipt, token)
        if not receipt_root.is_dir():
            return
        for receipt in sorted(receipt_root.glob("r-*.json")):
            payload = self._read_publication_receipt(receipt)
            token = str(payload["token"])
            final = project_dir / "sources" / "web" / str(payload["directory"])
            if payload["status"] == "committed":
                tree = _scan_web_package(final)
                if tree.source_hash != payload["source_hash"]:
                    raise RuntimeError(
                        "Editable media package changed after it was committed"
                    )
                continue
            references = self._web_assets_referencing(final)
            if references:
                if references != [str(payload["asset_id"])]:
                    raise RuntimeError(
                        "Editable media publication receipt does not match its asset"
                    )
                tree = _scan_web_package(final)
                if tree.source_hash != payload["source_hash"]:
                    raise RuntimeError(
                        "Editable media publication changed before it was committed"
                    )
                atomic_write_text(
                    receipt,
                    _publication_receipt_json(
                        asset_id=str(payload["asset_id"]),
                        source_hash=str(payload["source_hash"]),
                        token=token,
                        status="committed",
                    ),
                    durable=True,
                )
                continue
            self._archive_residual(final, receipt, token)

    def _web_assets_referencing(
        self,
        package_root: Path,
    ) -> list[str]:
        references: list[str] = []
        for asset in self.repository.catalog.list_assets():
            if asset.kind != AssetKind.WEB:
                continue
            spec = self.repository.web.get_web_asset_spec(asset.id)
            current_root = web_package_root(
                self.repository.catalog.resolve_asset_path(asset),
                spec.manifest,
            )
            if current_root == package_root.resolve():
                references.append(asset.id)
        return sorted(references)

    def _commit_package_publication(
        self,
        publication: _WebPackagePublication,
        change: Callable[[], T],
    ) -> T:
        try:
            with self.repository.transaction():
                publication.publish()
                self.repository.enlist_transaction_publication(
                    on_commit=publication.mark_committed,
                    on_rollback=lambda _error: publication.archive_failed(),
                )
                return change()
        except BaseException as error:
            try:
                publication.archive_failed()
            except BaseException as archive_error:
                error.add_note(
                    "网页包事务回滚后归档失败："
                    f"{archive_error}"
                )
            raise

    def _archive_residual(
        self,
        package: Path,
        receipt: Path,
        token: str,
    ) -> None:
        archive_token = token
        archive = self.repository.project_dir / "archive" / "web" / f"f-{archive_token}"
        archived_receipt = (
            self.repository.project_dir / "archive" / "web" / f"r-{archive_token}.json"
        )
        while archive.exists() or archived_receipt.exists():
            archive_token = hashlib.sha256(new_id().encode()).hexdigest()[
                :_WEB_PUBLICATION_TOKEN_HEX_CHARS
            ]
            archive = self.repository.project_dir / "archive" / "web" / f"f-{archive_token}"
            archived_receipt = (
                self.repository.project_dir
                / "archive"
                / "web"
                / f"r-{archive_token}.json"
            )
        if package.exists():
            tree = _scan_web_package(package)
            self._validate_publication_paths(tree, archive)
            archive.parent.mkdir(parents=True, exist_ok=True)
            package.replace(archive)
        if receipt.exists():
            require_windows_interop_path(archived_receipt)
            archived_receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.replace(archived_receipt)

    @staticmethod
    def _publication_token(name: str, *, prefix: str) -> str | None:
        if not name.startswith(prefix):
            return None
        token = name[len(prefix) :]
        if (
            len(token) != _WEB_PUBLICATION_TOKEN_HEX_CHARS
            or any(character not in "0123456789abcdef" for character in token)
        ):
            return None
        return token

    @staticmethod
    def _read_publication_receipt(path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError) as error:
            raise RuntimeError(
                f"Editable media publication receipt is invalid: {path}"
            ) from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "asset_id",
            "source_hash",
            "token",
            "directory",
            "status",
        }:
            raise RuntimeError(f"Editable media publication receipt is invalid: {path}")
        token = payload["token"]
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != _WEB_PUBLICATION_SCHEMA_VERSION
            or not isinstance(payload["asset_id"], str)
            or not payload["asset_id"]
            or not isinstance(payload["source_hash"], str)
            or len(payload["source_hash"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in payload["source_hash"]
            )
            or not isinstance(token, str)
            or payload["directory"] != f"p-{token}"
            or payload["status"] not in {"pending", "committed"}
            or path.name != f"r-{token}.json"
        ):
            raise RuntimeError(f"Editable media publication receipt is invalid: {path}")
        if WebMediaService._publication_token(f"p-{token}", prefix="p-") is None:
            raise RuntimeError(f"Editable media publication receipt is invalid: {path}")
        return payload

    @staticmethod
    def read_media_sources(
        package_root: Path,
        manifest: EditableMediaManifest,
    ) -> WebMediaSourcesManifest:
        path = package_root / manifest.media_sources
        if not path.is_file():
            raise FileNotFoundError(path)
        return WebMediaSourcesManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    @staticmethod
    def _media_source_ids(
        package_root: Path,
        manifest: EditableMediaManifest,
    ) -> set[str]:
        return {
            item.id
            for item in WebMediaService.read_media_sources(
                package_root,
                manifest,
            ).sources
        }

    @staticmethod
    def _validate_files(package_root: Path, manifest: EditableMediaManifest) -> None:
        media_sources = WebMediaService.read_media_sources(package_root, manifest)
        for relative in [manifest.entry, manifest.media_sources, *manifest.resources]:
            if not (package_root / relative).is_file():
                raise FileNotFoundError(package_root / relative)
        for source in media_sources.sources:
            source_file = source.file.split("#", 1)[0]
            if not (package_root / source_file).is_file():
                raise FileNotFoundError(package_root / source_file)

    @staticmethod
    def _validate_constraint(
        layer_id: str,
        field: str,
        value: object,
        constraint: WebFieldConstraint | None,
    ) -> None:
        if value is None or constraint is None:
            return
        if constraint.choices and str(value) not in constraint.choices:
            raise ValueError(f"Layer {layer_id} field {field} is outside its choices")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if constraint.minimum is not None and value < constraint.minimum:
                raise ValueError(f"Layer {layer_id} field {field} is below its minimum")
            if constraint.maximum is not None and value > constraint.maximum:
                raise ValueError(f"Layer {layer_id} field {field} exceeds its maximum")
            if constraint.step is not None:
                origin = constraint.minimum or 0.0
                steps = (float(value) - origin) / constraint.step
                if abs(steps - round(steps)) > 1e-7:
                    raise ValueError(f"Layer {layer_id} field {field} does not match its step")
