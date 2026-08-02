from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath

from mediaflow.application.web_package_files import WebPackageTree
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebClipState,
    WebMediaSourcesManifest,
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
            served_mime_type = mimetypes.guess_type(source_file)[0]
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
