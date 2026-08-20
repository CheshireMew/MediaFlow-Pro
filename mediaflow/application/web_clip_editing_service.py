from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.ports import StructuredFileReader, WebApplicationDocuments
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_clip_data_editing import WebClipDataEditing
from mediaflow.application.web_clip_layer_editing import WebClipLayerEditing
from mediaflow.application.web_clip_parameter_editing import WebClipParameterEditing
from mediaflow.application.web_edit_document_builder import build_web_edit_document
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.application.web_runtime_state_commit import WebRuntimeStateCommit
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.web_manifest import EditableMediaManifest
from mediaflow.domain.web_media_sources import WebMediaSourcesManifest
from mediaflow.domain.web_state import (
    WebClipState,
    WebEditDocument,
    WebRuntimeVariant,
    web_runtime_state,
)


class WebClipEditingService(
    WebClipParameterEditing,
    WebClipLayerEditing,
    WebClipDataEditing,
):
    """Owns validated state changes for one editable-media clip."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        timeline: Callable[[str], TimelineEditor],
        packages: WebPackageService,
        structured_files: StructuredFileReader,
    ) -> None:
        self.repository = repository
        self._timeline = timeline
        self._packages = packages
        self._structured_files = structured_files

    def get_clip(self, clip_id: str) -> WebClipState:
        return self.repository.web.get_web_clip_state(clip_id)

    def describe_clip_editing(
        self,
        sequence_id: str,
        clip_id: str,
        *,
        scene_id: str | None = None,
    ) -> WebEditDocument:
        _editor, _asset, spec, state = self._clip_context(
            sequence_id,
            clip_id,
            None,
        )
        manifest = spec.manifest
        resolved_scene_id = self._scene_id(state, manifest, scene_id)
        return build_web_edit_document(
            clip_id=clip_id,
            manifest=manifest,
            state=state,
            scene_id=resolved_scene_id,
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
        package_root = web_files.web_package_root(
            self.repository.assets.resolve_asset_path(asset),
            spec.manifest,
        )
        media_source_ids = web_contract.media_source_ids(
            self._media_sources(package_root, spec.manifest)
        )
        candidate = WebRuntimeStateCommit.candidate(
            current,
            spec.manifest,
            runtime_state,
            media_source_ids,
        )
        return self._save_state(editor, current, candidate)

    def runtime_state(
        self,
        sequence_id: str,
        clip_id: str,
    ) -> dict:
        _editor, _asset, spec, current = self._clip_context(sequence_id, clip_id, None)
        return web_runtime_state(current, spec.manifest)

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
        asset = self.repository.assets.get_asset(clip.asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Clip is not editable web media")
        spec = self._packages.inspect_asset(asset.id)
        current = state.web_states[clip_id]
        if expected_revision is not None and current.revision != expected_revision:
            raise RuntimeError(
                f"Editable media revision conflict: expected {expected_revision}, current {current.revision}"
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

    def _media_sources(
        self,
        package_root: Path,
        manifest: EditableMediaManifest,
    ) -> WebMediaSourcesManifest:
        path = package_root.joinpath(*PurePosixPath(manifest.media_sources).parts)
        return WebMediaSourcesManifest.model_validate(
            self._structured_files.read_json(path)
        )

    def _save_state(
        self,
        editor: TimelineEditor,
        current: WebClipState,
        candidate: WebClipState,
    ) -> WebClipState:
        try:
            clip = next(item for item in editor.state.clips if item.id == current.clip_id)
        except StopIteration as error:
            raise KeyError(current.clip_id) from error
        asset = self.repository.assets.get_asset(clip.asset_id)
        spec = self.repository.web.get_web_asset_spec(asset.id)
        package_root = web_files.web_package_root(
            self.repository.assets.resolve_asset_path(asset),
            spec.manifest,
        )
        web_contract.validate_media_bindings(
            spec.manifest,
            self._media_sources(package_root, spec.manifest),
            candidate,
        )
        web_contract.validate_clip_state_contract(spec.manifest, candidate)
        updated = candidate.model_copy(update={"revision": current.revision + 1})
        editor.set_web_clip_state(updated, expected_revision=current.revision)
        return editor.state.web_states[current.clip_id]

    def set_batch_name(
        self,
        sequence_id: str,
        clip_id: str,
        name: str,
        *,
        expected_revision: int,
    ) -> WebClipState:
        editor, _asset, _spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"batch_name": name}),
        )
