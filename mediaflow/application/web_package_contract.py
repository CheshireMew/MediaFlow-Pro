from __future__ import annotations

from pathlib import Path, PurePosixPath

from mediaflow.application.web_package_files import WebPackageTree
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebClipState,
    WebMediaSourcesManifest,
    media_mime_type,
    media_source_ids_in_web_data,
    resolved_web_scene_data,
)


def read_media_sources(
    package_root: Path,
    manifest: EditableMediaManifest,
) -> WebMediaSourcesManifest:
    path = package_root.joinpath(*PurePosixPath(manifest.media_sources).parts)
    return WebMediaSourcesManifest.model_validate_json(path.read_text(encoding="utf-8"))


def media_source_ids(
    package_root: Path,
    manifest: EditableMediaManifest,
) -> set[str]:
    return {item.id for item in read_media_sources(package_root, manifest).sources}


def validate_package_files(
    package_tree: WebPackageTree,
    manifest: EditableMediaManifest,
) -> WebMediaSourcesManifest:
    media_sources = read_media_sources(package_tree.root, manifest)
    package_files = set(package_tree.files)
    for relative in [
        manifest.entry,
        manifest.media_sources,
        *manifest.resources,
    ]:
        if relative not in package_files:
            raise FileNotFoundError(package_tree.root / relative)
    for source in media_sources.sources:
        source_file = source.file.split("#", 1)[0]
        if source_file not in package_files:
            raise FileNotFoundError(package_tree.root / source_file)
        if source.integrity is not None:
            actual_bytes, actual_sha256 = package_tree.file_integrity[source_file]
            if actual_bytes != source.integrity.bytes or actual_sha256 != source.integrity.sha256:
                raise ValueError(f"Editable media source integrity does not match its file: {source.id}")
            served_mime_type = media_mime_type(source_file)
            if served_mime_type != source.integrity.mime_type:
                raise ValueError(
                    "Editable media source MIME type does not match its "
                    f"file name: {source.id} declares "
                    f"{source.integrity.mime_type}, server resolves "
                    f"{served_mime_type or 'unknown'}"
                )
        for run in source.provenance_runs:
            if run.capture is None:
                continue
            capture_file = run.capture.file
            if capture_file not in package_files:
                raise FileNotFoundError(package_tree.root / capture_file)
            if package_tree.file_integrity[capture_file][1] != run.capture.sha256:
                raise ValueError(
                    f"Editable media provenance capture integrity does not match: {source.id}/{capture_file}"
                )
    validate_media_bindings(
        manifest,
        media_sources,
        WebClipState(clip_id="package-validation"),
    )
    return media_sources


def validate_media_bindings(
    manifest: EditableMediaManifest,
    media_sources: WebMediaSourcesManifest,
    state: WebClipState,
) -> None:
    validate_clip_state_contract(manifest, state)
    sources_by_id = {source.id: source for source in media_sources.sources}

    def require_browser_image(source_id: str, context: str) -> None:
        source = sources_by_id.get(source_id)
        if source is None:
            raise ValueError(f"{context} references undeclared media source: {source_id}")
        if (
            source.binding.pipeline != "browser"
            or source.media_type
            not in {
                "photo",
                "screenshot",
                "video-frame",
                "icon",
                "generated",
            }
            or source.acquisition.method == "generated-in-project"
        ):
            raise ValueError(f"{context} requires a browser-rendered image source: {source_id}")

    for scene in manifest.scenes:
        scene_state = state.scenes.get(scene.id)
        if scene_state is not None:
            for layer_id, layer in scene_state.layers.items():
                if layer.image is not None:
                    require_browser_image(
                        layer.image,
                        f"Editable media scene {scene.id} layer {layer_id}",
                    )
        data = resolved_web_scene_data(
            state,
            manifest,
            scene.id,
        )
        selected_ids = media_source_ids_in_web_data(
            data,
            manifest.data_fields,
        )
        unknown_ids = set(selected_ids) - set(sources_by_id)
        if unknown_ids:
            raise ValueError(
                f"Editable media scene {scene.id} references undeclared media sources: {sorted(unknown_ids)}"
            )
        native_underlays = [
            source_id
            for source_id in selected_ids
            if sources_by_id[source_id].binding.pipeline == "native-underlay"
        ]
        if len(native_underlays) > 1:
            raise ValueError(f"Editable media scene {scene.id} selects more than one native video underlay")
        for slot_id, slot in scene.asset_slots.items():
            source_id = data.get(slot.data_field)
            if not isinstance(source_id, str) or not source_id:
                continue
            require_browser_image(
                source_id,
                f"Editable media scene {scene.id} asset slot {slot_id}",
            )


def validate_clip_state_contract(
    manifest: EditableMediaManifest,
    state: WebClipState,
) -> None:
    scenes = {item.id: item for item in manifest.scenes}
    layers = {item.id: item for item in manifest.layers}
    variants = {item.id: item for item in manifest.variants}
    data_fields = {item.id for item in manifest.data_fields}
    theme_fields = {item.id for item in manifest.theme_variables}
    parameters = {item.descriptor.id: item for item in manifest.parameters}
    global_parameters = {
        item.descriptor.id
        for item in manifest.parameters
        if item.binding.scope == "global"
    }
    scene_parameters = {
        item.descriptor.id
        for item in manifest.parameters
        if item.binding.scope == "scene"
    }
    if state.scene_id is not None and state.scene_id not in scenes:
        raise ValueError(f"Editable state references unknown scene: {state.scene_id}")
    if state.variant is not None:
        variant = variants.get(state.variant.id)
        if variant is None:
            raise ValueError(f"Editable state references unknown variant: {state.variant.id}")
        if (
            state.variant.width != variant.canvas.width
            or state.variant.height != variant.canvas.height
        ):
            raise ValueError(f"Editable state variant dimensions changed: {state.variant.id}")
    unknown_theme = set(state.theme) - theme_fields
    if unknown_theme:
        raise ValueError(f"Editable state references unknown theme fields: {sorted(unknown_theme)}")
    unknown_global_parameters = (
        set(state.parameters) | set(state.parameter_locks)
    ) - global_parameters
    if unknown_global_parameters:
        raise ValueError(
            "Editable global parameter state is invalid: "
            f"{sorted(unknown_global_parameters)}"
        )
    for parameter_id, value in state.parameters.items():
        parameters[parameter_id].descriptor.validate_value(value)
    for scene_id, scene_state in state.scenes.items():
        scene = scenes.get(scene_id)
        if scene is None:
            raise ValueError(f"Editable state references unknown scene: {scene_id}")
        referenced_layers = (
            set(scene_state.layers)
            | set(scene_state.animations)
            | set(scene_state.locks)
        )
        unknown_layers = referenced_layers - set(layers)
        if unknown_layers:
            raise ValueError(
                f"Editable state scene {scene_id} references unknown layers: "
                f"{sorted(unknown_layers)}"
            )
        for layer_id, override in scene_state.layers.items():
            override_disallowed = override.changed_fields() - set(
                layers[layer_id].editable
            )
            if override_disallowed:
                raise ValueError(
                    f"Editable state layer {layer_id} contains non-editable fields: "
                    f"{sorted(override_disallowed)}"
                )
        for layer_id, tracks in scene_state.animations.items():
            animation_disallowed = set(tracks) - set(layers[layer_id].editable)
            if animation_disallowed:
                raise ValueError(
                    f"Editable state animation {layer_id} contains non-editable fields: "
                    f"{sorted(animation_disallowed)}"
                )
            if any(track.keyframes[-1].time_ms >= scene.duration_ms for track in tracks.values()):
                raise ValueError(f"Editable state animation exceeds scene: {scene_id}/{layer_id}")
        for layer_id, fields in scene_state.locks.items():
            lock_disallowed = set(fields) - set(layers[layer_id].editable)
            if lock_disallowed:
                raise ValueError(
                    f"Editable state locks {layer_id} contain non-editable fields: "
                    f"{sorted(lock_disallowed)}"
                )
        unknown_data = set(scene_state.data_snapshot.values) - data_fields
        if unknown_data:
            raise ValueError(
                f"Editable state scene {scene_id} references unknown data fields: "
                f"{sorted(unknown_data)}"
            )
        unknown_scene_parameters = (
            set(scene_state.parameters)
            | set(scene_state.parameter_locks)
        ) - scene_parameters
        if unknown_scene_parameters:
            raise ValueError(
                f"Editable scene parameter state is invalid: "
                f"{scene_id}/{sorted(unknown_scene_parameters)}"
            )
        unknown_parameter_animations = set(scene_state.parameter_animations) - set(
            parameters
        )
        if unknown_parameter_animations:
            raise ValueError(
                f"Editable parameter animation is invalid: "
                f"{scene_id}/{sorted(unknown_parameter_animations)}"
            )
        for parameter_id, value in scene_state.parameters.items():
            parameters[parameter_id].descriptor.validate_value(value)
        for parameter_id, track in scene_state.parameter_animations.items():
            parameter = parameters[parameter_id]
            if parameter.descriptor.timeline != "keyframe":
                raise ValueError(
                    f"Editable parameter animation is invalid: {scene_id}/{parameter_id}"
                )
            if track.keyframes[-1].time_ms >= scene.duration_ms:
                raise ValueError(
                    f"Editable parameter animation exceeds scene: {scene_id}/{parameter_id}"
                )
            for keyframe in track.keyframes:
                parameter.descriptor.validate_value(keyframe.value)
