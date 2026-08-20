from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.web_clip_editing_context import web_clip_editing_context
from mediaflow.application.web_field_validation import WebFieldValidator
from mediaflow.domain.web_state import WebClipState, WebDataSnapshot, WebSceneState


class WebClipDataEditing:
    def update_theme(
        self,
        sequence_id: str,
        clip_id: str,
        changes: Mapping[str, str | float],
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
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
            WebFieldValidator.constraint(variable_id, "theme", value, variable.constraints)
            theme[variable_id] = value
        return web_clip_editing_context(self)._save_state(
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
        editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        fields = {item.id: item for item in spec.manifest.data_fields}
        merged = dict(current_scene.data_snapshot.values)
        media_source_ids = web_contract.media_source_ids(
            web_clip_editing_context(self)._media_sources(
                web_files.web_package_root(
                    web_clip_editing_context(self).repository.assets.resolve_asset_path(_asset),
                    spec.manifest,
                ),
                spec.manifest,
            )
        )
        for field_id, value in values.items():
            field = fields.get(field_id)
            if field is None:
                raise ValueError(f"Editable media data field is not declared: {field_id}")
            WebFieldValidator.data_value(field, value)
            if field.kind == "media-source":
                if value not in media_source_ids:
                    raise ValueError(
                        f"Data field {field_id} media source is not declared in the v4 media-sources manifest"
                    )
            merged[field_id] = cast(JsonValue, value)
        snapshot = WebDataSnapshot(
            source_kind=source_kind,
            source_label=source_label,
            values=merged,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"data_snapshot": snapshot})
        return web_clip_editing_context(self)._save_state(
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
        path = web_clip_editing_context(self)._structured_files.resolve_file(source)
        suffix = path.suffix.lower()
        if suffix == ".json":
            payload = web_clip_editing_context(self)._structured_files.read_json(path)
        elif suffix == ".csv":
            payload = web_clip_editing_context(self)._structured_files.read_csv(path)
        else:
            raise ValueError("Editable media data snapshots accept .json or .csv files")
        _editor, _asset, spec, _current = web_clip_editing_context(self)._clip_context(
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
