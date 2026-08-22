from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_batch_service import WebBatchService
from mediaflow.application.web_clip_editing_service import WebClipEditingService
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.application.web_rebind_service import WebRebindService
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_rendering import WebRenderPlan
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

    def import_web_package(self, source: str | Path):
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

    def inspect_web_asset(self, asset_id: str):
        return self._web_packages.inspect_asset(asset_id)

    def get_web_clip(self, clip_id: str):
        return self._web_clips.get_clip(clip_id)

    def describe_web_clip_editing(self, *args: Any, **kwargs: Any):
        return self._web_clips.describe_clip_editing(*args, **kwargs)

    def update_web_clip(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_clip(*args, **kwargs)

    def diff_web_clip_update(self, *args: Any, **kwargs: Any):
        return self._web_clips.diff_clip_update(*args, **kwargs)

    def select_web_variant(self, *args: Any, **kwargs: Any):
        return self._web_clips.select_variant(*args, **kwargs)

    def commit_web_runtime_state(self, *args: Any, **kwargs: Any):
        return self._web_clips.commit_runtime_state(*args, **kwargs)

    def set_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_keyframe(*args, **kwargs)

    def remove_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.remove_keyframe(*args, **kwargs)

    def move_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.move_keyframe(*args, **kwargs)

    def update_web_parameter(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_parameter(*args, **kwargs)

    def set_web_parameter_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_parameter_keyframe(*args, **kwargs)

    def remove_web_parameter_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.remove_parameter_keyframe(*args, **kwargs)

    def move_web_parameter_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.move_parameter_keyframe(*args, **kwargs)

    def set_web_parameter_lock(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_parameter_lock(*args, **kwargs)

    def update_web_theme(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_theme(*args, **kwargs)

    def update_web_data(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_data(*args, **kwargs)

    def update_web_data_from_file(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_data_from_file(*args, **kwargs)

    def set_web_field_locks(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_field_locks(*args, **kwargs)

    def web_runtime_state(self, *args: Any, **kwargs: Any):
        return self._web_clips.runtime_state(*args, **kwargs)

    def create_web_variants(self, *args: Any, **kwargs: Any):
        return self._web_batches.create_variants(*args, **kwargs)

    def read_web_variant_records(self, source: str | Path):
        return self._web_batches.read_variant_records(source)

    def plan_web_asset_rebind(self, *args: Any, **kwargs: Any):
        return self._web_rebind.plan_rebind_asset(*args, **kwargs)

    def commit_web_asset_rebind(self, *args: Any, **kwargs: Any):
        return self._web_rebind.commit_rebind_asset(*args, **kwargs)

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

    def export_web_clip(self, *args: Any, **kwargs: Any):
        return WebRenderService(self._repository, self._paths).export_clip(*args, **kwargs)
