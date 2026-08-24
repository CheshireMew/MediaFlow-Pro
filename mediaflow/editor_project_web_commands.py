from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_batch_service import WebBatchService
from mediaflow.application.web_clip_editing_service import WebClipEditingService
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.application.web_rebind_service import WebRebindService
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_manifest import WebAssetSpec
from mediaflow.domain.web_rendering import WebRenderPlan
from mediaflow.domain.web_state import (
    WebClipState,
    WebEditDocument,
    WebRebindCommitReport,
    WebRebindPlan,
    WebStateDiff,
    WebVariantResult,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_render_service import WebRenderService


class EditorProjectWebCommands:
    _repository: ProjectRepository
    _paths: RuntimePaths
    _web_packages: WebPackageService
    _web_clips: WebClipEditingService
    _web_batches: WebBatchService
    _web_rebind: WebRebindService

    if TYPE_CHECKING:

        def _require_writable(self) -> None: ...

        def timeline(self, sequence_id: str) -> TimelineEditor: ...

    def import_web_package(self, source: str | Path) -> Asset:
        return self._web_packages.import_package(source)

    def populate_sample_project(self) -> None:
        from mediaflow.application.sample_project_service import SampleProjectService
        from mediaflow.infrastructure.sample_project_storage import LocalSampleProjectStorage

        self._require_writable()
        SampleProjectService(
            self._repository,
            self.timeline,
            self._repository.project_dir,
            LocalSampleProjectStorage(),
        ).populate()

    def inspect_web_asset(self, asset_id: str) -> WebAssetSpec:
        return self._web_packages.inspect_asset(asset_id)

    def get_web_clip(self, clip_id: str) -> WebClipState:
        return self._web_clips.get_clip(clip_id)

    def describe_web_clip_editing(
        self,
        sequence_id: str,
        clip_id: str,
        *,
        scene_id: str | None = None,
    ) -> WebEditDocument:
        return self._web_clips.describe_clip_editing(
            sequence_id,
            clip_id,
            scene_id=scene_id,
        )

    def update_web_clip(
        self,
        sequence_id: str,
        clip_id: str,
        updates: Mapping[str, Mapping[str, object]],
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        return self._web_clips.update_clip(
            sequence_id,
            clip_id,
            updates,
            scene_id=scene_id,
            expected_revision=expected_revision,
            actor=actor,
        )

    def diff_web_clip_update(
        self,
        sequence_id: str,
        clip_id: str,
        updates: Mapping[str, Mapping[str, object]],
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "automation",
    ) -> WebStateDiff:
        return self._web_clips.diff_clip_update(
            sequence_id,
            clip_id,
            updates,
            scene_id=scene_id,
            expected_revision=expected_revision,
            actor=actor,
        )

    def select_web_variant(
        self,
        sequence_id: str,
        clip_id: str,
        variant_id: str,
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.select_variant(
            sequence_id,
            clip_id,
            variant_id,
            expected_revision=expected_revision,
        )

    def commit_web_runtime_state(
        self,
        sequence_id: str,
        clip_id: str,
        runtime_state: Mapping[str, object],
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.commit_runtime_state(
            sequence_id,
            clip_id,
            runtime_state,
            expected_revision=expected_revision,
        )

    def set_web_keyframe(
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
        return self._web_clips.set_keyframe(
            sequence_id,
            clip_id,
            layer_id,
            field,
            time_ms,
            value,
            scene_id=scene_id,
            easing=easing,
            expected_revision=expected_revision,
            actor=actor,
        )

    def remove_web_keyframe(
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
        return self._web_clips.remove_keyframe(
            sequence_id,
            clip_id,
            layer_id,
            field,
            time_ms,
            scene_id=scene_id,
            expected_revision=expected_revision,
        )

    def move_web_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        field: str,
        old_time_ms: int,
        new_time_ms: int,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.move_keyframe(
            sequence_id,
            clip_id,
            layer_id,
            field,
            old_time_ms,
            new_time_ms,
            scene_id=scene_id,
            expected_revision=expected_revision,
        )

    def update_web_parameter(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        value: JsonValue,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        return self._web_clips.update_parameter(
            sequence_id,
            clip_id,
            parameter_id,
            value,
            scene_id=scene_id,
            expected_revision=expected_revision,
            actor=actor,
        )

    def set_web_parameter_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        time_ms: int,
        value: JsonValue,
        *,
        scene_id: str | None = None,
        easing: Mapping[str, object] | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        return self._web_clips.set_parameter_keyframe(
            sequence_id,
            clip_id,
            parameter_id,
            time_ms,
            value,
            scene_id=scene_id,
            easing=easing,
            expected_revision=expected_revision,
            actor=actor,
        )

    def remove_web_parameter_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        time_ms: int,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.remove_parameter_keyframe(
            sequence_id,
            clip_id,
            parameter_id,
            time_ms,
            scene_id=scene_id,
            expected_revision=expected_revision,
        )

    def move_web_parameter_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        old_time_ms: int,
        new_time_ms: int,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.move_parameter_keyframe(
            sequence_id,
            clip_id,
            parameter_id,
            old_time_ms,
            new_time_ms,
            scene_id=scene_id,
            expected_revision=expected_revision,
        )

    def set_web_parameter_lock(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        locked: bool,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.set_parameter_lock(
            sequence_id,
            clip_id,
            parameter_id,
            locked,
            scene_id=scene_id,
            expected_revision=expected_revision,
        )

    def update_web_theme(
        self,
        sequence_id: str,
        clip_id: str,
        changes: Mapping[str, str | float],
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.update_theme(
            sequence_id,
            clip_id,
            changes,
            expected_revision=expected_revision,
        )

    def update_web_data(
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
        return self._web_clips.update_data(
            sequence_id,
            clip_id,
            values,
            scene_id=scene_id,
            source_kind=source_kind,
            source_label=source_label,
            expected_revision=expected_revision,
        )

    def update_web_data_from_file(
        self,
        sequence_id: str,
        clip_id: str,
        source: str | Path,
        *,
        scene_id: str | None = None,
        field_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        return self._web_clips.update_data_from_file(
            sequence_id,
            clip_id,
            source,
            scene_id=scene_id,
            field_id=field_id,
            expected_revision=expected_revision,
        )

    def set_web_field_locks(
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
        return self._web_clips.set_field_locks(
            sequence_id,
            clip_id,
            layer_id,
            fields,
            locked,
            scene_id=scene_id,
            expected_revision=expected_revision,
        )

    def web_runtime_state(self, sequence_id: str, clip_id: str) -> dict[str, object]:
        return self._web_clips.runtime_state(sequence_id, clip_id)

    def create_web_variants(
        self,
        source_sequence_id: str,
        clip_id: str,
        records: list[Mapping[str, object]],
        bindings: Mapping[str, str],
        *,
        name_template: str = "版本 {index}",
        actor: Literal["human", "automation"] = "automation",
    ) -> list[WebVariantResult]:
        return self._web_batches.create_variants(
            source_sequence_id,
            clip_id,
            records,
            bindings,
            name_template=name_template,
            actor=actor,
        )

    def read_web_variant_records(self, source: str | Path) -> list[Mapping[str, object]]:
        return self._web_batches.read_variant_records(source)

    def plan_web_asset_rebind(
        self,
        asset_id: str,
        source: str | Path,
    ) -> WebRebindPlan:
        return self._web_rebind.plan_rebind_asset(asset_id, source)

    def commit_web_asset_rebind(
        self,
        asset_id: str,
        source: str | Path,
        plan_digest: str,
        resolutions: Mapping[str, str],
    ) -> WebRebindCommitReport:
        return self._web_rebind.commit_rebind_asset(
            asset_id,
            source,
            plan_digest,
            resolutions,
        )

    def prepare_web_sequence(self, state: TimelineState) -> None:
        WebRenderService(self._repository, self._paths).ensure_sequence(state)

    def inspect_web_clip_render(
        self,
        sequence_id: str,
        clip_id: str,
    ) -> WebRenderPlan:
        state = self._repository.timeline.load_timeline(sequence_id)
        return WebRenderService(self._repository, self._paths).inspect_clip_render(
            state,
            clip_id,
        )
