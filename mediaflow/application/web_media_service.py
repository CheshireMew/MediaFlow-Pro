from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.application.ports import WebMediaServiceDocuments, WebPackageValidatorPort
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AssetKind, AssetOrigin
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import Asset, MediaMetadata
from mediaflow.domain.web_media import (
    CONTINUOUS_ANIMATION_FIELDS,
    LAYOUT_FIELDS,
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
    WebRebindReport,
    WebStateDiff,
    WebVariantResult,
    web_runtime_state,
)

MANIFEST_FILE_NAME = "editable-media.json"


def editable_media_source_hash(package_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (path for path in package_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(package_root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


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

    def import_package(self, source: str | Path) -> Asset:
        package_root, manifest = self.read_package(source)
        self._runtime_validator.validate(package_root, manifest)
        source_hash = editable_media_source_hash(package_root)
        project = self.repository.get_project()
        asset_id = new_id()
        destination = self.repository.project_dir / "sources" / "web" / asset_id
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(package_root, destination)
        copied_manifest = EditableMediaManifest.model_validate_json(
            (destination / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
        )
        self._validate_files(destination, copied_manifest)
        main_sequence = self.repository.get_sequence(project.main_sequence_id)
        duration_frames = max(
            1,
            round(copied_manifest.timeline.duration_ms * main_sequence.profile.fps / 1000),
        )
        asset = Asset(
            id=asset_id,
            project_id=project.id,
            name=package_root.name,
            kind=AssetKind.WEB,
            origin=AssetOrigin.EXTERNAL,
            path=str(destination / copied_manifest.entry),
            managed=True,
            metadata=MediaMetadata(
                duration_frames=duration_frames,
                width=copied_manifest.canvas.width,
                height=copied_manifest.canvas.height,
                fps_numerator=main_sequence.profile.fps_numerator,
                fps_denominator=main_sequence.profile.fps_denominator,
                has_video=True,
                has_audio=False,
            ),
        )
        with self.repository.transaction():
            stored = self.repository.add_asset(asset)
            self.repository.save_web_asset_spec(
                WebAssetSpec(
                    asset_id=asset_id,
                    manifest=copied_manifest,
                    source_hash=source_hash,
                )
            )
        return stored

    def inspect_asset(self, asset_id: str) -> WebAssetSpec:
        asset = self.repository.get_asset(asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Asset is not editable web media")
        spec = self.repository.get_web_asset_spec(asset_id)
        actual_hash = editable_media_source_hash(self.repository.resolve_asset_path(asset).parent)
        if actual_hash != spec.source_hash:
            spec = spec.model_copy(update={"source_hash": actual_hash})
            if not self.repository.read_only:
                self.repository.save_web_asset_spec(spec)
        return spec

    def get_clip(self, clip_id: str) -> WebClipState:
        return self.repository.get_web_clip_state(clip_id)

    def update_clip(
        self,
        sequence_id: str,
        clip_id: str,
        updates: Mapping[str, Mapping[str, object]],
        *,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
        layout_id: str | None = None,
    ) -> WebClipState:
        editor, asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        layers = dict(current.layers)
        layout_overrides = {
            key: dict(value) for key, value in current.layout_overrides.items()
        }
        if layout_id is not None:
            spec.manifest.layout_for(layout_id, 1, 1)
        manifest_layers = {layer.id: layer for layer in spec.manifest.layers}
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
            locked = patch_fields & set(current.locks.get(layer_id, ()))
            if actor == "automation" and locked:
                raise PermissionError(
                    f"Editable media fields are locked: {layer_id}/{sorted(locked)}"
                )
            layout_patch = (
                {field: value for field, value in patch.items() if field in LAYOUT_FIELDS}
                if layout_id is not None
                else {}
            )
            global_patch = {
                field: value for field, value in patch.items() if field not in layout_patch
            }

            def apply_patch(
                target: dict[str, WebLayerOverride],
                values_patch: Mapping[str, object],
                *,
                target_layer_id: str = layer_id,
                target_manifest_layer=manifest_layer,
            ) -> None:
                if not values_patch:
                    return
                values = target.get(target_layer_id, WebLayerOverride()).model_dump()
                values.update(values_patch)
                candidate = WebLayerOverride.model_validate(values)
                if candidate.image is not None:
                    if candidate.image not in spec.manifest.resources:
                        raise ValueError(
                            f"Layer {target_layer_id} image is not declared in editable media resources"
                        )
                    package_root = self.repository.resolve_asset_path(asset).parent
                    if not (package_root / candidate.image).is_file():
                        raise FileNotFoundError(package_root / candidate.image)
                for field in values_patch:
                    self._validate_constraint(
                        target_layer_id,
                        field,
                        getattr(candidate, field),
                        target_manifest_layer.constraints.get(field),
                    )
                if candidate.changed_fields():
                    target[target_layer_id] = candidate
                else:
                    target.pop(target_layer_id, None)

            apply_patch(layers, global_patch)
            if layout_patch and layout_id is not None:
                apply_patch(layout_overrides.setdefault(layout_id, {}), layout_patch)

        updated = current.model_copy(
            update={
                "layers": layers,
                "layout_overrides": layout_overrides,
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
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "automation",
        layout_id: str | None = None,
    ) -> WebStateDiff:
        _editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        profile = self.repository.get_sequence(sequence_id).profile
        resolved = web_runtime_state(
            current,
            spec.manifest,
            width=profile.width,
            height=profile.height,
        )
        resolved_layers = resolved.get("layers")
        if not isinstance(resolved_layers, dict):
            resolved_layers = {}

        def current_value(layer_id: str, field: str) -> JsonValue:
            layer_values = resolved_layers.get(layer_id)
            if not isinstance(layer_values, dict):
                return None
            return layer_values.get(field)

        locked_paths = [
            f"layers.{layer_id}.{field}"
            for layer_id, patch in updates.items()
            for field in patch
            if actor == "automation" and field in current.locks.get(layer_id, ())
        ]
        return WebStateDiff(
            clip_id=clip_id,
            before_revision=current.revision,
            changes={
                f"layers.{layer_id}.{field}": {
                    "before": current_value(layer_id, field),
                    "after": cast(JsonValue, value),
                }
                for layer_id, patch in updates.items()
                for field, value in patch.items()
                if current_value(layer_id, field) != value
            }
            | (
                {"layout_id": {"before": current.layout_id, "after": layout_id}}
                if layout_id is not None and layout_id != current.layout_id
                else {}
            ),
            locked_paths=locked_paths,
        )

    def select_layout(
        self,
        sequence_id: str,
        clip_id: str,
        layout_id: str | None,
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        if layout_id is not None:
            spec.manifest.layout_for(layout_id, 1, 1)
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"layout_id": layout_id}),
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
        if actor == "automation" and field in current.locks.get(layer_id, ()):
            raise PermissionError(f"Editable media field is locked: {layer_id}/{field}")
        validated_value = self._validated_field_value(layer_id, field, value, layer.constraints.get(field))
        animations = {
            key: dict(tracks) for key, tracks in current.animations.items()
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
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"animations": animations}),
        )

    def remove_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        field: str,
        time_ms: int,
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, _spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        animations = {
            key: dict(tracks) for key, tracks in current.animations.items()
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
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"animations": animations}),
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
        source_kind: Literal["inline", "file", "api"] = "inline",
        source_label: str = "",
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        fields = {item.id: item for item in spec.manifest.data_fields}
        merged = dict(current.data_snapshot.values)
        for field_id, value in values.items():
            field = fields.get(field_id)
            if field is None:
                raise ValueError(f"Editable media data field is not declared: {field_id}")
            self._validate_data_value(field, value)
            if field.kind == "image":
                if value not in spec.manifest.resources:
                    raise ValueError(
                        f"Data field {field_id} image is not declared in editable media resources"
                    )
                package_root = self.repository.resolve_asset_path(_asset).parent
                if not (package_root / str(value)).is_file():
                    raise FileNotFoundError(package_root / str(value))
            merged[field_id] = value
        snapshot = WebDataSnapshot(
            source_kind=source_kind,
            source_label=source_label,
            values=merged,
        )
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"data_snapshot": snapshot}),
        )

    def update_data_from_file(
        self,
        sequence_id: str,
        clip_id: str,
        source: str | Path,
        *,
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
        locks = {key: set(value) for key, value in current.locks.items()}
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
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"locks": normalized}),
        )

    def runtime_state(
        self,
        sequence_id: str,
        clip_id: str,
    ) -> dict:
        _editor, _asset, spec, current = self._clip_context(sequence_id, clip_id, None)
        profile = self.repository.get_sequence(sequence_id).profile
        return web_runtime_state(
            current,
            spec.manifest,
            width=profile.width,
            height=profile.height,
        )

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
        source = self.repository.load_timeline(source_sequence_id)
        try:
            source_clip = next(item for item in source.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        source_asset = self.repository.get_asset(source_clip.asset_id)
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
            copied = self.repository.load_timeline(sequence.id)
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
            layer_updates: dict[str, dict[str, object]] = {}
            theme_updates: dict[str, str | float] = {}
            data_updates: dict[str, object] = {}
            layout_id: str | None = None
            for source_key, target_path in bindings.items():
                if source_key not in record:
                    raise ValueError(f"Variant record is missing field: {source_key}")
                value = record[source_key]
                parts = target_path.split(".")
                if len(parts) == 3 and parts[0] == "layers":
                    layer_updates.setdefault(parts[1], {})[parts[2]] = value
                elif len(parts) == 2 and parts[0] == "theme":
                    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                        raise ValueError(f"Theme binding {target_path} needs text or a number")
                    theme_updates[parts[1]] = value
                elif len(parts) == 2 and parts[0] == "data":
                    data_updates[parts[1]] = value
                elif target_path == "layout_id":
                    layout_id = str(value)
                else:
                    raise ValueError(f"Unsupported variant binding target: {target_path}")
            state = self.get_clip(copied_clip.id)
            if layer_updates:
                state = self.update_clip(
                    sequence.id,
                    copied_clip.id,
                    layer_updates,
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
            if data_updates:
                state = self.update_data(
                    sequence.id,
                    copied_clip.id,
                    data_updates,
                    expected_revision=state.revision,
                )
            if layout_id is not None:
                state = self.select_layout(
                    sequence.id,
                    copied_clip.id,
                    layout_id,
                    expected_revision=state.revision,
                )
            editor = self._timeline(sequence.id)
            state = self._save_state(
                editor,
                state,
                state.model_copy(update={"variant_name": name}),
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
        asset = self.repository.get_asset(asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Asset is not editable web media")
        old_spec = self.repository.get_web_asset_spec(asset_id)
        package_root, new_manifest = self.read_package(source)
        self._runtime_validator.validate(package_root, new_manifest)
        new_hash = editable_media_source_hash(package_root)
        old_layers = {item.id: item for item in old_spec.manifest.layers}
        new_layers = {item.id: item for item in new_manifest.layers}
        retained = sorted(set(old_layers) & set(new_layers))
        added = sorted(set(new_layers) - set(old_layers))
        removed = sorted(set(old_layers) - set(new_layers))
        affected: list[tuple[str, WebClipState]] = []
        conflicts: list[str] = []
        new_layout_ids = {item.id for item in new_manifest.layouts} or {"default"}
        new_theme_ids = {item.id for item in new_manifest.theme_variables}
        new_data_ids = {item.id for item in new_manifest.data_fields}
        for sequence in self.repository.list_sequences(include_archived=True):
            timeline = self.repository.load_timeline(sequence.id)
            for clip in timeline.clips:
                if clip.asset_id != asset_id or clip.id not in timeline.web_states:
                    continue
                state = timeline.web_states[clip.id]
                affected.append((sequence.id, state))
                for layer_id in removed:
                    if (
                        layer_id in state.layers
                        or layer_id in state.animations
                        or layer_id in state.locks
                        or any(layer_id in values for values in state.layout_overrides.values())
                    ):
                        conflicts.append(f"{clip.id}: removed layer {layer_id} has instance state")
                for layer_id in retained:
                    allowed = set(new_layers[layer_id].editable)
                    used = state.layers.get(layer_id, WebLayerOverride()).changed_fields()
                    used |= set(state.animations.get(layer_id, {}))
                    used |= set(state.locks.get(layer_id, ()))
                    removed_fields = used - allowed
                    for field in sorted(removed_fields):
                        conflicts.append(f"{clip.id}: field {layer_id}.{field} is no longer editable")
                if state.layout_id and state.layout_id not in new_layout_ids:
                    conflicts.append(f"{clip.id}: selected layout {state.layout_id} was removed")
                for key in sorted(set(state.theme) - new_theme_ids):
                    conflicts.append(f"{clip.id}: theme variable {key} was removed")
                for key in sorted(set(state.data_snapshot.values) - new_data_ids):
                    conflicts.append(f"{clip.id}: data field {key} was removed")
        report = WebRebindReport(
            asset_id=asset_id,
            old_source_hash=old_spec.source_hash,
            new_source_hash=new_hash,
            retained_layers=retained,
            added_layers=added,
            removed_layers=removed,
            affected_clips=[state.clip_id for _sequence_id, state in affected],
            conflicts=conflicts,
            archive_path=str(self.repository.resolve_asset_path(asset).parent.resolve()),
        )
        if dry_run or new_hash == old_spec.source_hash:
            return report
        if conflicts and not allow_conflicts:
            raise ValueError(
                "Editable media rebind has state conflicts; inspect with dry_run or allow conflicts: "
                + "; ".join(conflicts)
            )
        destination = (
            self.repository.project_dir
            / "sources"
            / "web"
            / f"{asset_id}-{new_hash[:12]}"
        )
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(package_root, destination)
        elif editable_media_source_hash(destination) != new_hash:
            raise FileExistsError(f"Rebind destination contains different content: {destination}")
        copied_manifest = EditableMediaManifest.model_validate_json(
            (destination / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
        )
        self._validate_files(destination, copied_manifest)
        main_profile = self.repository.get_sequence(
            self.repository.get_project().main_sequence_id
        ).profile
        duration_frames = max(
            1,
            round(copied_manifest.timeline.duration_ms * main_profile.fps / 1000),
        )
        migrated: list[WebClipState] = []
        for _sequence_id, state in affected:
            layers: dict[str, WebLayerOverride] = {}
            animations: dict[str, dict] = {}
            locks: dict[str, tuple] = {}
            for layer_id in retained:
                allowed = set(new_layers[layer_id].editable)
                current_override = state.layers.get(layer_id)
                if current_override is not None:
                    values = {
                        key: value
                        for key, value in current_override.model_dump(exclude_none=True).items()
                        if key in allowed
                    }
                    if values:
                        layers[layer_id] = WebLayerOverride.model_validate(values)
                tracks = {
                    field: track
                    for field, track in state.animations.get(layer_id, {}).items()
                    if field in allowed
                }
                if tracks:
                    animations[layer_id] = tracks
                fields = tuple(field for field in state.locks.get(layer_id, ()) if field in allowed)
                if fields:
                    locks[layer_id] = fields
            layout_overrides = {
                layout_id: {
                    layer_id: override
                    for layer_id, override in values.items()
                    if layer_id in new_layers
                }
                for layout_id, values in state.layout_overrides.items()
                if layout_id in new_layout_ids
            }
            snapshot = WebDataSnapshot(
                source_kind=state.data_snapshot.source_kind,
                source_label=state.data_snapshot.source_label,
                captured_at=state.data_snapshot.captured_at,
                values={
                    key: value
                    for key, value in state.data_snapshot.values.items()
                    if key in new_data_ids
                },
            )
            migrated.append(
                state.model_copy(
                    update={
                        "layers": layers,
                        "layout_id": (
                            state.layout_id if state.layout_id in new_layout_ids else None
                        ),
                        "layout_overrides": layout_overrides,
                        "animations": animations,
                        "theme": {
                            key: value for key, value in state.theme.items() if key in new_theme_ids
                        },
                        "data_snapshot": snapshot,
                        "locks": locks,
                        "source_hash": new_hash,
                        "revision": state.revision + 1,
                    }
                )
            )
        with self.repository.transaction():
            self.repository.update_asset(
                asset.model_copy(
                    update={
                        "path": str(destination / copied_manifest.entry),
                        "metadata": asset.metadata.model_copy(
                            update={
                                "duration_frames": duration_frames,
                                "width": copied_manifest.canvas.width,
                                "height": copied_manifest.canvas.height,
                            }
                        ),
                    }
                )
            )
            self.repository.save_web_asset_spec(
                WebAssetSpec(
                    asset_id=asset_id,
                    manifest=copied_manifest,
                    source_hash=new_hash,
                )
            )
            self.repository.save_web_clip_states(migrated)
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
        asset = self.repository.get_asset(clip.asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Clip is not editable web media")
        spec = self.repository.get_web_asset_spec(asset.id)
        current = state.web_states[clip_id]
        if expected_revision is not None and current.revision != expected_revision:
            raise RuntimeError(
                f"Editable media revision conflict: expected {expected_revision}, "
                f"current {current.revision}"
            )
        return editor, asset, spec, current

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
            "image": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
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
                        "image": isinstance(cell, str),
                        "number": isinstance(cell, (int, float)) and not isinstance(cell, bool),
                        "boolean": isinstance(cell, bool),
                    }[column_kind]
                    if not cell_valid:
                        raise ValueError(
                            f"Data field {field_id} row {index} column {column_id} "
                            f"does not match kind {column_kind}"
                        )

    @staticmethod
    def read_package(source: str | Path) -> tuple[Path, EditableMediaManifest]:
        path = Path(source).expanduser().resolve(strict=True)
        package_root = path if path.is_dir() else path.parent
        manifest_path = package_root / MANIFEST_FILE_NAME
        if path.is_file() and path.name != MANIFEST_FILE_NAME:
            raise ValueError(f"Editable media import expects a directory or {MANIFEST_FILE_NAME}")
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        manifest = EditableMediaManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        WebMediaService._validate_files(package_root, manifest)
        return package_root, manifest

    @staticmethod
    def _validate_files(package_root: Path, manifest: EditableMediaManifest) -> None:
        for relative in [manifest.entry, *manifest.resources]:
            if not (package_root / relative).is_file():
                raise FileNotFoundError(package_root / relative)

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
