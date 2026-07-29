from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from mediaflow.application.asset_service import AssetService
from mediaflow.application.edit_history import ProjectEditCommand, ProjectEditHistory
from mediaflow.application.events import TaskEvent
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.ports import MediaProbePort
from mediaflow.application.project_task_handlers import ProjectTaskHandlers
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.transcript_editing import TranscriptEditingService
from mediaflow.application.translation_service import TranslationService
from mediaflow.application.web_media_service import WebMediaService, web_package_root
from mediaflow.application.workflow_stage_handlers import WorkflowUpdate
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.enums import (
    TaskStatus,
)
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import GlobalSettings, LlmProviderSettings
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.domain.task_commands import (
    ImportAssetCommand,
    TaskCommand,
)
from mediaflow.domain.tasks import (
    DownloadAnalysisTaskOutcome,
    ImportedAssetTaskOutcome,
    LoudnessTaskOutcome,
    SequenceBoundaryTaskOutcome,
    Task,
)
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.cache_manager import CacheManager
from mediaflow.infrastructure.cookie_store import CookieStore
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.font_assets import subtitle_font_options
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.media_thumbnail_service import MediaThumbnailService
from mediaflow.infrastructure.mlt import (
    LoudnessAnalysisService,
    SequenceBoundaryAnalysisService,
    TimelineCompiler,
)
from mediaflow.infrastructure.project_cover_service import ProjectCoverService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.runtime_tools import RuntimeToolService
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.storage_paths import default_media_root, default_project_root
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.task_runtime import InfrastructureTaskRuntimes
from mediaflow.infrastructure.translation_cache import TranslationCache
from mediaflow.infrastructure.web_browser import (
    BrowserWebPackageValidator,
    WebPackagePreviewServer,
)
from mediaflow.infrastructure.web_render_service import WebRenderCache, WebRenderService
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService


@dataclass(frozen=True, slots=True)
class RecentProjectSnapshot:
    items: list[dict]
    totals: dict[str, int]


@dataclass(frozen=True, slots=True)
class ProjectTaskResult:
    workflow: WorkflowUpdate = field(default_factory=WorkflowUpdate)
    imported_asset_id: str = ""
    imported_document_id: str = ""
    imported_purpose: str = ""
    download_plan: DownloadPlan | None = None
    sequence_bounds_status: str = ""
    sequence_id: str = ""
    audio_metrics: dict[str, float] | None = None

    def as_dict(self) -> dict:
        return {
            "workflow": {
                "selected_asset_ids": list(self.workflow.selected_asset_ids),
                "status_message": self.workflow.status_message,
            },
            "imported_asset_id": self.imported_asset_id,
            "imported_document_id": self.imported_document_id,
            "imported_purpose": self.imported_purpose,
            "download_plan": (
                self.download_plan.model_dump(mode="json") if self.download_plan is not None else None
            ),
            "sequence_bounds_status": self.sequence_bounds_status,
            "sequence_id": self.sequence_id,
            "audio_metrics": self.audio_metrics,
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ProjectTaskResult:
        workflow = values.get("workflow") or {}
        download_plan = values.get("download_plan")
        audio_metrics = values.get("audio_metrics")
        return cls(
            workflow=WorkflowUpdate(
                selected_asset_ids=[
                    str(value)
                    for value in workflow.get("selected_asset_ids") or []
                ],
                status_message=str(workflow.get("status_message") or ""),
            ),
            imported_asset_id=str(values.get("imported_asset_id") or ""),
            imported_document_id=str(
                values.get("imported_document_id") or ""
            ),
            imported_purpose=str(values.get("imported_purpose") or ""),
            download_plan=(
                DownloadPlan.model_validate(download_plan)
                if download_plan is not None
                else None
            ),
            sequence_bounds_status=str(
                values.get("sequence_bounds_status") or ""
            ),
            sequence_id=str(values.get("sequence_id") or ""),
            audio_metrics=(
                {
                    str(key): float(value)
                    for key, value in audio_metrics.items()
                }
                if isinstance(audio_metrics, dict)
                else None
            ),
        )


class EditorProject:
    """One open project and all operations that depend on its document boundary."""

    def __init__(
        self,
        repository: ProjectRepository,
        *,
        settings: GlobalSettings,
        paths: RuntimePaths,
    ):
        self._repository = repository
        self._closed = False
        self._settings = settings
        self._paths = paths
        self._cookies = CookieStore(paths.runtime_dir / "cookies")
        self._history = ProjectEditHistory()
        self._timelines: dict[str, TimelineEditor] = {}
        self._assets = AssetService(
            repository,
            cast(MediaProbePort, MediaProbe(paths)),
            fingerprint_file,
        )
        web_validator = BrowserWebPackageValidator()
        self._web = WebMediaService(repository, self.timeline, web_validator)
        self._web_preview_server: WebPackagePreviewServer | None = None
        self._web_preview_root: Path | None = None
        self._subtitle_publication = SubtitlePublicationService(repository)
        self._subtitle_publication.reconcile_document_srts()
        self._subtitle_acquisition = SubtitleAcquisitionService(
            repository,
            self._subtitle_publication,
        )
        self._subtitle_editing = SubtitleEditingService(
            repository,
            self._subtitle_publication,
            history=self._history,
        )
        self._transcript_editing = TranscriptEditingService(
            repository,
            self._subtitle_publication,
            self._history,
        )
        self._highlights = HighlightService(repository, OpenAIJsonClient)
        self._translations = TranslationService(
            repository,
            OpenAIJsonClient,
            TranslationCache(
                paths.project_cache_dir(repository.project_dir)
                / "translations"
            ),
            self._subtitle_publication,
        )
        self._sequences = SequenceService(repository)
        self._tasks = TaskService(
            TaskRepository(repository),
            recover_expired=not repository.read_only,
        )
        self._workflows = ProjectWorkflowService(
            repository,
            self._tasks,
            settings,
            start_task=self.start_task,
            proxy_decision=self.proxy_decision,
            create_highlight_short=self._highlights.create_short_sequence,
        )
        self._task_followup_updates: dict[str, WorkflowUpdate] = {}
        self._task_followup_lock = threading.RLock()
        self._task_followup_subscription: int | None = None
        self._task_handlers = ProjectTaskHandlers(
            self._repository,
            self._assets,
            InfrastructureTaskRuntimes.create(
                self._repository,
                self._paths,
                self._cookies,
            ),
            self._subtitle_acquisition,
            self._subtitle_editing,
            self._subtitle_publication,
            self._highlights,
            self._translations,
            settings=lambda: self._settings,
            active_llm_provider=self._active_llm_provider,
        )
        self._task_handlers.register_with(self._tasks)
        if not repository.read_only:
            self._task_followup_subscription = self._tasks.events.subscribe(
                self._handle_task_followup_event,
                include_snapshot=True,
            )

    @property
    def project_dir(self) -> Path:
        return self._repository.project_dir

    @property
    def read_only(self) -> bool:
        return self._repository.read_only

    @property
    def known_content_revision(self) -> int:
        return self._repository.known_content_revision

    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("项目以只读方式打开")

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    def undo(self) -> None:
        self._history.undo()

    def redo(self) -> None:
        self._history.redo()

    def execute_automation_request(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        action: Callable[[bool], dict[str, Any]],
        *,
        atomic: bool,
    ) -> dict[str, Any]:
        if not request_id:
            return action(False)
        input_hash = hashlib.sha256(
            json.dumps(
                arguments,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if not atomic:
            cached, retrying = self._repository.begin_automation_request(
                request_id,
                operation,
                input_hash,
            )
            if cached is not None:
                return cached
            return self._repository.save_automation_result(
                request_id,
                operation,
                input_hash,
                action(retrying),
            )
        history_checkpoint = self._history.checkpoint()
        try:
            with self._repository.transaction():
                cached = self._repository.automation_result(
                    request_id,
                    operation,
                    input_hash,
                )
                if cached is not None:
                    return cached
                result = action(False)
                return self._repository.save_automation_result(
                    request_id,
                    operation,
                    input_hash,
                    result,
                )
        except BaseException:
            self._history.restore(history_checkpoint)
            for sequence_id, editor in list(
                self._timelines.items()
            ):
                try:
                    editor.reload()
                except Exception:
                    self._timelines.pop(sequence_id, None)
            raise

    # This class is the sole application API for an open project. Desktop and
    # automation callers do not receive repositories or concrete services.
    def get_project(self):
        return self._repository.catalog.get_project()

    def content_revision(self) -> int:
        return self._repository.content_revision()

    def get_sequence(self, sequence_id: str):
        return self._repository.catalog.get_sequence(sequence_id)

    def list_sequences(self, *, include_archived: bool = False):
        return self._repository.catalog.list_sequences(include_archived=include_archived)

    def create_short_sequence(self, name: str, profile: ProjectProfile | None = None):
        return self._repository.catalog.create_short_sequence(name, profile)

    def get_asset(self, asset_id: str):
        return self._repository.catalog.get_asset(asset_id)

    def list_assets(self):
        return self._repository.catalog.list_assets()

    def resolve_asset_path(self, asset):
        return self._repository.catalog.resolve_asset_path(asset)

    def load_timeline(self, sequence_id: str) -> TimelineState:
        return self._repository.timeline.load_timeline(sequence_id)

    def get_subtitle_document(self, document_id: str):
        return self._repository.subtitles.get_subtitle_document(document_id)

    def list_subtitle_documents(
        self,
        asset_id: str | None = None,
        *,
        sequence_id: str | None = None,
    ):
        return self._repository.subtitles.list_subtitle_documents(
            asset_id,
            sequence_id=sequence_id,
        )

    def list_subtitle_segments(self, document_id: str):
        return self._repository.subtitles.list_subtitle_segments(document_id)

    def list_subtitle_words(self, document_id: str, *, include_excluded: bool = True):
        return self._repository.subtitles.list_subtitle_words(
            document_id,
            include_excluded=include_excluded,
        )

    def subtitle_segment_summary(self, document_id: str) -> tuple[int, int, int]:
        return self._repository.subtitles.subtitle_segment_summary(document_id)

    def place_subtitle_document(self, *args: Any, **kwargs: Any):
        return self._repository.subtitles.place_subtitle_document(*args, **kwargs)

    def list_subtitle_placements(self, track_id: str):
        return self._repository.subtitles.list_subtitle_placements(track_id)

    def get_subtitle_placement(self, placement_id: str):
        return self._repository.subtitles.get_subtitle_placement(placement_id)

    def update_subtitle_placement_text(self, placement_id: str, text_override: str | None):
        return self._repository.subtitles.update_subtitle_placement_text(
            placement_id,
            text_override,
        )

    def apply_subtitle_placement_to_document(self, placement_id: str, text: str):
        return self._repository.subtitles.apply_subtitle_placement_to_document(
            placement_id,
            text,
        )

    def get_web_asset_spec(self, asset_id: str):
        return self._web.inspect_asset(asset_id)

    def list_web_assets(self):
        return self._repository.web.list_web_asset_specs()

    def web_editor_entry_url(self, asset_id: str) -> str:
        asset = self._repository.catalog.get_asset(asset_id)
        spec = self._web.inspect_asset(asset_id)
        package_root = web_package_root(
            self._repository.catalog.resolve_asset_path(asset),
            spec.manifest,
        )
        if (
            self._web_preview_server is None
            or self._web_preview_root != package_root
        ):
            self.close_web_preview()
            self._web_preview_server = WebPackagePreviewServer(package_root)
            self._web_preview_root = package_root
        return self._web_preview_server.url_for(
            spec.manifest.entry,
            query=(
                f"capture=1&variant={spec.manifest.default_variant_id}"
                f"&scene={spec.manifest.scenes[0].id}"
            ),
        )

    def close_web_preview(self) -> None:
        if self._web_preview_server is not None:
            self._web_preview_server.close()
        self._web_preview_server = None
        self._web_preview_root = None

    def web_render_cache_ready(self, state: TimelineState, clip_id: str) -> bool:
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self._repository.catalog.get_asset(clip.asset_id)
        return WebRenderCache(self._repository).target(state, clip, asset).path.is_file()

    def list_audio_buses(self, sequence_id: str):
        return self._repository.audio.list_audio_buses(sequence_id)

    def save_audio_bus(self, bus):
        return self._repository.audio.save_audio_bus(bus)

    def list_audio_effects(self, bus_id: str):
        return self._repository.audio.list_audio_effects(bus_id)

    def save_audio_effect(self, effect):
        return self._repository.audio.save_audio_effect(effect)

    def save_audio_effect_chain(self, bus_id: str, effects: list):
        return self._repository.audio.save_audio_effect_chain(bus_id, effects)

    def remove_audio_effect(self, effect_id: str) -> None:
        self._repository.audio.remove_audio_effect(effect_id)

    def list_export_history(self, sequence_id: str | None = None):
        return self._repository.records.list_export_history(sequence_id)

    def save_sequence_export_preset(self, sequence_id: str, preset):
        return self._repository.catalog.save_sequence_export_preset(sequence_id, preset)

    def list_highlights(self, asset_id: str | None = None):
        return self._repository.highlights.list_highlights(asset_id)

    def list_workflow_runs(self, *, active_only: bool = False):
        return self._repository.catalog.list_workflow_runs(active_only=active_only)

    def import_external_asset(self, source: str | Path, *, expected_kind=None):
        return self._assets.import_external(source, expected_kind=expected_kind)

    def relink_asset(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ):
        return self._assets.relink(
            asset_id,
            replacement,
            allow_different_content=allow_different_content,
        )

    def relink_offline_assets(self, directory: str | Path):
        return self._assets.relink_offline_from_directory(directory)

    def suggested_profile(self, asset_id: str) -> ProjectProfile | None:
        return self._assets.suggested_profile(asset_id)

    def adopt_main_profile_from_video(self, asset_id: str):
        return self._assets.adopt_main_profile_from_video(asset_id)

    def update_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.update_segment(*args, **kwargs)

    def add_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.add_segment(*args, **kwargs)

    def delete_subtitle_segments(self, document_id: str, segment_ids: list[str]) -> int:
        return self._subtitle_editing.delete_segments(document_id, segment_ids)

    def merge_subtitle_segments(self, document_id: str, segment_ids: list[str]):
        return self._subtitle_editing.merge_segments(document_id, segment_ids)

    def split_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.split_segment(*args, **kwargs)

    def smart_split_subtitle_document(self, document_id: str, *, text_limit: int = 24) -> int:
        return self._subtitle_editing.smart_split_document(document_id, text_limit=text_limit)

    def fix_subtitle_overlaps(self, document_id: str) -> int:
        return self._subtitle_editing.fix_overlaps(document_id)

    def selected_subtitle_segments_srt(self, document_id: str, segment_ids: list[str]) -> str:
        return self._subtitle_editing.selected_segments_srt(document_id, segment_ids)

    def replace_selected_subtitle_texts(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_selected_texts(*args, **kwargs)

    def replace_all_subtitle_text(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_all(*args, **kwargs)

    def replace_subtitle_match(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_match(*args, **kwargs)

    def find_subtitle_matches(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.find_matches(*args, **kwargs)

    def update_subtitle_placement_range(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.update_placement_range(*args, **kwargs)

    def reset_subtitle_placement_range(self, placement_id: str):
        return self._subtitle_editing.reset_placement_range(placement_id)

    def write_subtitle_srt(
        self,
        document_id: str,
        destination: str | Path | None = None,
    ) -> Path:
        return self._subtitle_publication.write_document_srt(document_id, destination)

    def inspect_transcript(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.inspect_transcript(*args, **kwargs)

    def preview_transcript_edit(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.preview_plan(*args, **kwargs)

    def apply_transcript_edit(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.apply_plan(*args, **kwargs)

    def add_manual_highlight(self, *args: Any, **kwargs: Any):
        return self._highlights.add_manual_candidate(*args, **kwargs)

    def update_highlight(self, *args: Any, **kwargs: Any):
        return self._highlights.update_candidate(*args, **kwargs)

    def set_highlight_selected(self, candidate_id: str, selected: bool):
        return self._highlights.set_selected(candidate_id, selected)

    def delete_highlight(self, candidate_id: str) -> None:
        self._highlights.delete_candidate(candidate_id)

    def create_highlight_short(self, candidate_id: str, *, name: str | None = None):
        return self._highlights.create_short_sequence(candidate_id, name=name)

    def selected_highlights(self, asset_id: str | None = None):
        return self._highlights.selected_candidates(asset_id)

    def create_short_from_range(self, *args: Any, **kwargs: Any):
        return self._sequences.create_short_from_range(*args, **kwargs)

    def create_short_from_bounds(self, *args: Any, **kwargs: Any):
        return self._sequences.create_short_from_bounds(*args, **kwargs)

    def subscribe_task_events(
        self,
        callback: Callable[[TaskEvent], None],
        *,
        include_snapshot: bool = True,
    ) -> int:
        return self._tasks.events.subscribe(callback, include_snapshot=include_snapshot)

    def unsubscribe_task_events(self, token: int) -> None:
        self._tasks.events.unsubscribe(token)

    def list_tasks(self) -> list[Task]:
        return self._tasks.list()

    def list_unconsumed_terminal_tasks(self) -> list[Task]:
        return self._tasks.list_unconsumed_terminal()

    def task_snapshot(self) -> tuple[list[Task], int]:
        return self._tasks.snapshot()

    def task_events_after(self, cursor: int, *, limit: int = 500) -> list[TaskEvent]:
        return self._tasks.events_after(cursor, limit=limit)

    def get_task(self, task_id: str) -> Task:
        return self._tasks.get(task_id)

    def wait_for_task(self, task_id: str, timeout: float | None = None) -> Task:
        return self._tasks.wait(task_id, timeout)

    def resume_task(
        self,
        task_id: str,
        *,
        allow_existing: bool = False,
    ) -> Task:
        self._require_writable()
        return self._tasks.resume(
            task_id,
            allow_existing=allow_existing,
        )

    def retry_task(self, task_id: str) -> Task:
        self._require_writable()
        return self._tasks.retry(task_id)

    def pause_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.pause(task_id)

    def cancel_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.cancel(task_id)

    def pause_all_tasks(self) -> int:
        self._require_writable()
        return self._tasks.pause_all()

    def cancel_all_tasks(self) -> int:
        self._require_writable()
        return self._tasks.cancel_all()

    def delete_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.delete(task_id)

    def clear_task_history(self) -> int:
        self._require_writable()
        return self._tasks.clear_history()

    def active_workflow(self):
        return self._workflows.active_run()

    def set_workflow_mode(self, value: bool | None) -> None:
        self._workflows.set_project_mode(value)

    def begin_download_workflow(self, *args: Any, **kwargs: Any):
        return self._workflows.begin_download(*args, **kwargs)

    def attach_export_task(self, run_id: str, task_id: str) -> None:
        self._workflows.attach_export_task(run_id, task_id)

    def cancel_workflow(self, run_id: str) -> WorkflowUpdate:
        return self._workflows.cancel(run_id)

    def skip_workflow(self, run_id: str) -> WorkflowUpdate:
        return self._workflows.skip(run_id)

    def continue_workflow(self, *args: Any, **kwargs: Any) -> WorkflowUpdate:
        return self._workflows.continue_run(*args, **kwargs)

    def reconcile_workflow(self) -> None:
        self._require_writable()
        self._workflows.reconcile_interrupted()

    def import_web_package(self, source: str | Path):
        return self._web.import_package(source)

    def inspect_web_asset(self, asset_id: str):
        return self._web.inspect_asset(asset_id)

    def get_web_clip(self, clip_id: str):
        return self._web.get_clip(clip_id)

    def update_web_clip(self, *args: Any, **kwargs: Any):
        return self._web.update_clip(*args, **kwargs)

    def diff_web_clip_update(self, *args: Any, **kwargs: Any):
        return self._web.diff_clip_update(*args, **kwargs)

    def select_web_variant(self, *args: Any, **kwargs: Any):
        return self._web.select_variant(*args, **kwargs)

    def commit_web_runtime_state(self, *args: Any, **kwargs: Any):
        return self._web.commit_runtime_state(*args, **kwargs)

    def set_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web.set_keyframe(*args, **kwargs)

    def remove_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web.remove_keyframe(*args, **kwargs)

    def update_web_theme(self, *args: Any, **kwargs: Any):
        return self._web.update_theme(*args, **kwargs)

    def update_web_data(self, *args: Any, **kwargs: Any):
        return self._web.update_data(*args, **kwargs)

    def update_web_data_from_file(self, *args: Any, **kwargs: Any):
        return self._web.update_data_from_file(*args, **kwargs)

    def set_web_field_locks(self, *args: Any, **kwargs: Any):
        return self._web.set_field_locks(*args, **kwargs)

    def web_runtime_state(self, *args: Any, **kwargs: Any):
        return self._web.runtime_state(*args, **kwargs)

    def create_web_variants(self, *args: Any, **kwargs: Any):
        return self._web.create_variants(*args, **kwargs)

    def read_web_variant_records(self, source: str | Path):
        return self._web.read_variant_records(source)

    def rebind_web_asset(self, *args: Any, **kwargs: Any):
        return self._web.rebind_asset(*args, **kwargs)

    def prepare_web_sequence(self, state: TimelineState) -> None:
        WebRenderService(self._repository, self._paths).ensure_sequence(state)

    def export_web_clip(self, *args: Any, **kwargs: Any):
        return WebRenderService(self._repository, self._paths).export_clip(*args, **kwargs)

    def timeline(self, sequence_id: str) -> TimelineEditor:
        editor = self._timelines.get(sequence_id)
        if editor is None:
            editor = TimelineEditor(self._repository, sequence_id, self._history)
            self._timelines[sequence_id] = editor
        return editor

    def create_version(self, name: str):
        return self._repository.records.create_project_version(name)

    def list_versions(self):
        return self._repository.records.list_project_versions()

    def restore_version(self, version_id: str):
        with self._repository.transaction():
            record = self._repository.records.restore_project_version(
                version_id
            )
            self._subtitle_publication.reconcile_document_srts()
        sequence_ids = {
            sequence.id for sequence in self._repository.catalog.list_sequences(include_archived=True)
        }
        for sequence_id, editor in list(self._timelines.items()):
            if sequence_id in sequence_ids:
                try:
                    editor.reload()
                except Exception:
                    self._timelines.pop(sequence_id, None)
            else:
                self._timelines.pop(sequence_id)
        self._history.clear()
        return record

    def export_fcpxml(
        self,
        sequence_id: str,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        state = self._repository.timeline.load_timeline(sequence_id)
        exporter = FcpxmlExportService(self._repository)
        output = exporter.preflight(
            state,
            destination,
            overwrite=overwrite,
        )
        WebRenderService(
            self._repository,
            self._paths,
        ).ensure_sequence(state)
        return exporter.export(
            state,
            output,
            overwrite=overwrite,
        )

    def proxy_decision(self, asset, *, dropped_frames: int = 0, manual: bool = False):
        return ProxyService.decision(asset, dropped_frames=dropped_frames, manual=manual)

    def sequence_boundary_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return SequenceBoundaryAnalysisService(
            TimelineCompiler(self._repository),
            self._paths,
        ).snapshot_hash(state)

    def loudness_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return LoudnessAnalysisService(
            TimelineCompiler(self._repository),
            self._paths,
        ).snapshot_hash(state)

    def read_loudness_metrics(self, sequence_id: str) -> dict[str, float]:
        snapshot_hash = self.loudness_snapshot_hash(sequence_id)
        path = LoudnessAnalysisService.result_path(
            self.project_dir,
            sequence_id,
            snapshot_hash,
        )
        metrics = LoudnessAnalysisService.read_metrics(
            path,
            expected_sequence_id=sequence_id,
            expected_snapshot_hash=snapshot_hash,
        )
        return metrics.desktop_payload() if metrics is not None else {}

    def start_task(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        self._require_writable()
        project = self._repository.catalog.get_project()
        return self._tasks.start(
            project_id=project.id,
            sequence_id=sequence_id or project.main_sequence_id,
            command=command,
            input_asset_ids=input_asset_ids,
            idempotency_key=idempotency_key,
        )

    def import_asset(
        self,
        source: str | Path,
        *,
        sequence_id: str | None = None,
        purpose: str = "media",
        language: str = "auto",
        media_asset_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        path = Path(source).expanduser().resolve(strict=True)
        if purpose not in {"media", "subtitle", "watermark"}:
            raise ValueError(f"Unknown import purpose: {purpose}")
        import_purpose = cast(Literal["media", "subtitle", "watermark"], purpose)
        return self.start_task(
            ImportAssetCommand(
                source_path=str(path),
                purpose=import_purpose,
                language=language,
                media_asset_id=media_asset_id,
            ),
            sequence_id=sequence_id,
            idempotency_key=idempotency_key,
        )

    def consume_task_result(self, task: Task) -> ProjectTaskResult:
        project = self._repository.catalog.get_project()
        if task.project_id != project.id:
            raise ValueError("Task does not belong to this project")
        if not task.status.is_consumable:
            raise ValueError("Only terminal task state can be consumed")
        self._require_writable()
        workflow = self._settle_task_followups(task)
        history_checkpoint = self._history.checkpoint()
        try:
            stored, _applied = self._repository.consume_task_result_once(
                task.id,
                task.project_id,
                task.revision,
                lambda: self._apply_task_result(
                    task,
                    workflow,
                ).as_dict(),
            )
        except Exception:
            self._history.restore(history_checkpoint)
            for editor in self._timelines.values():
                editor.reload()
            raise
        return ProjectTaskResult.from_dict(stored)

    def _apply_task_result(
        self,
        task: Task,
        workflow: WorkflowUpdate,
    ) -> ProjectTaskResult:
        imported_asset_id = ""
        imported_document_id = ""
        imported_purpose = ""
        download_plan = None
        sequence_bounds_status = ""
        sequence_id = task.sequence_id or ""
        audio_metrics = None
        if task.status == TaskStatus.COMPLETED:
            if isinstance(task.outcome, ImportedAssetTaskOutcome):
                imported_asset_id = task.outcome.asset_id
                imported_document_id = task.outcome.document_id or ""
                imported_purpose = task.outcome.purpose
            elif isinstance(task.outcome, DownloadAnalysisTaskOutcome):
                download_plan = task.outcome.plan
            elif isinstance(task.outcome, SequenceBoundaryTaskOutcome):
                analysis = task.outcome.analysis
                sequence_id = analysis.sequence_id
                current_hash = self.sequence_boundary_snapshot_hash(sequence_id)
                if analysis.snapshot_hash != current_hash:
                    sequence_bounds_status = "stale"
                else:
                    self.timeline(sequence_id).set_sequence_in_out(
                        analysis.suggested.in_frame,
                        analysis.suggested.out_frame,
                    )
                    sequence_bounds_status = (
                        "applied_without_speech" if analysis.speech_in_frame is None else "applied"
                    )
            elif isinstance(task.outcome, LoudnessTaskOutcome):
                current_hash = self.loudness_snapshot_hash(sequence_id)
                audio_metrics = {}
                if task.artifacts:
                    metrics = LoudnessAnalysisService.read_metrics(
                        task.artifacts[-1].resolve(self.project_dir),
                        expected_sequence_id=sequence_id,
                        expected_snapshot_hash=current_hash,
                    )
                    if metrics is not None:
                        audio_metrics = metrics.desktop_payload()
        return ProjectTaskResult(
            workflow=workflow,
            imported_asset_id=imported_asset_id,
            imported_document_id=imported_document_id,
            imported_purpose=imported_purpose,
            download_plan=download_plan,
            sequence_bounds_status=sequence_bounds_status,
            sequence_id=sequence_id,
            audio_metrics=audio_metrics,
        )

    def archive_short_sequence(self, sequence_id: str) -> None:
        sequence = self._repository.catalog.archive_short_sequence(sequence_id)

        def restore() -> None:
            self._repository.catalog.restore_short_sequence(sequence.id)

        def archive() -> None:
            self._repository.catalog.archive_short_sequence(sequence.id)

        self._history.push(
            ProjectEditCommand(
                label="删除短视频序列",
                undo_action=restore,
                redo_action=archive,
            )
        )

    def refresh_workflow_mode(self) -> ProjectWorkflowService:
        self._workflows.update_settings(self._settings)
        return self._workflows

    def update_settings(self, settings: GlobalSettings) -> None:
        self._settings = settings
        self.refresh_workflow_mode()

    def close(self, *, timeout: float | None = None) -> None:
        if self._closed:
            return
        self.close_web_preview()
        self._tasks.shutdown(timeout=timeout)
        if self._task_followup_subscription is not None:
            self._tasks.events.unsubscribe(self._task_followup_subscription)
            self._task_followup_subscription = None
        self._repository.close()
        self._closed = True

    def _handle_task_followup_event(self, event: TaskEvent) -> None:
        if event.event_type == "deleted":
            return
        task = Task.model_validate(event.payload)
        if task.status.is_terminal:
            self._settle_task_followups(task)

    def _settle_task_followups(self, task: Task) -> WorkflowUpdate:
        starts_import_workflow = (
            task.status == TaskStatus.COMPLETED
            and isinstance(task.outcome, ImportedAssetTaskOutcome)
            and task.outcome.purpose == "media"
        )
        if not task.status.is_terminal or self.read_only:
            return WorkflowUpdate()
        if task.command.workflow is None and not starts_import_workflow:
            return WorkflowUpdate()
        with self._task_followup_lock:
            previous = self._task_followup_updates.get(task.id)
            current = (
                self._workflows.handle_task(task)
                if task.command.workflow is not None
                else WorkflowUpdate()
            )
            if starts_import_workflow:
                outcome = task.outcome
                if not isinstance(outcome, ImportedAssetTaskOutcome):
                    raise RuntimeError(
                        "Imported task follow-up lost its persisted outcome"
                    )
                current = current.merge(
                    self._workflows.begin_import(
                        task.sequence_id
                        or self._repository.catalog.get_project().main_sequence_id,
                        outcome.asset_id,
                        source_task_id=task.id,
                    )
                )
            merged = current if previous is None else previous.merge(current)
            self._task_followup_updates[task.id] = merged
            return merged

    def reload_external_changes(self) -> None:
        self._repository.acknowledge_content_revision()
        self._history.clear()
        for editor in self._timelines.values():
            editor.reload()

    def __enter__(self) -> EditorProject:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _active_llm_provider(self) -> LlmProviderSettings:
        active_id = self._settings.active_llm_provider_id
        if active_id:
            active = next(
                (
                    provider
                    for provider in self._settings.llm_providers
                    if provider.id == active_id and provider.enabled
                ),
                None,
            )
            if active is not None:
                return active
        try:
            return next(provider for provider in self._settings.llm_providers if provider.enabled)
        except StopIteration as error:
            raise RuntimeError("请先在设置中配置并启用一个 LLM 提供商") from error


class EditorApplication:
    """Single composition root shared by desktop and headless entry points."""

    def __init__(self):
        self._paths = RuntimePaths.discover()
        CacheManager(self._paths.runtime_dir / "cache").prune_runs()
        self._settings_repository = SettingsRepository()
        self.settings = self._settings_repository.load()
        self._settings_repository.prepare_storage(self.settings)
        self.cookies = CookieStore(self._paths.runtime_dir / "cookies")
        self._media_thumbnails = MediaThumbnailService(self._paths)
        self._project_covers = ProjectCoverService(self._paths)

    @property
    def mlt_runtime_root(self) -> str:
        return str(self._paths.melt.parent) if self._paths.melt else ""

    @property
    def native_qml_root(self) -> Path | None:
        return self._paths.native_qml

    @property
    def runtime_paths(self) -> RuntimePaths:
        return self._paths

    @property
    def default_media_directory(self) -> str:
        return default_media_root()

    @property
    def default_project_directory(self) -> str:
        return default_project_root()

    def save_settings(self) -> None:
        self._settings_repository.save(self.settings)

    def replace_settings(self, settings: GlobalSettings) -> None:
        # Persist first so a disk error cannot leave the running application in
        # a state that was never durably accepted.
        self._settings_repository.save(settings)
        self.settings = self._settings_repository.with_storage_defaults(settings)
        self._settings_repository.prepare_storage(self.settings)

    def discover_video_encoder_options(self) -> list[dict]:
        return EncoderDiscoveryService(self._paths).video_options()

    def analyze_download_url(
        self,
        url: str,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> DownloadPlan:
        return YtDlpDownloadService.analyze_configured(
            url,
            settings=self.settings.download,
            cookies=self.cookies,
            check_cancelled=check_cancelled,
        )

    @staticmethod
    def test_llm_provider(provider: LlmProviderSettings) -> None:
        OpenAIJsonClient(provider).test_connection()

    @staticmethod
    def subtitle_font_options() -> list[dict]:
        return subtitle_font_options()

    def runtime_tool_status(self) -> dict:
        return RuntimeToolService(self.settings.asr, self._paths).status()

    def run_runtime_tool(
        self,
        operation: str,
        *,
        progress: Callable[[OperationProgress], None],
        check_cancelled: Callable[[], None],
    ) -> object:
        tools = RuntimeToolService(self.settings.asr, self._paths)
        if operation == "inspect":
            return tools.cuda_readiness()
        if operation == "update_ytdlp":
            return tools.update_ytdlp(
                progress=progress,
                check_cancelled=check_cancelled,
            )
        if operation == "install_asr_cli":
            return str(
                tools.install_faster_whisper_cli(
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
        if operation == "prewarm_asr_cli":
            return str(
                tools.prewarm_cli(
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
        raise ValueError(f"Unknown runtime tool operation: {operation}")

    def write_preview_snapshot(
        self,
        project_dir: str | Path,
        state: TimelineState,
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> Path:
        # Preview compilation owns an independent read connection so the active
        # project can be switched or closed without sharing its repository
        # connection with a worker thread.
        with ProjectRepository.open(project_dir, writable=False) as repository:
            document = TimelineCompiler(repository).compile(
                state,
                use_proxies=use_proxies,
                native_preview=True,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            )
            sequence_namespace = (
                "pv-"
                + hashlib.sha256(
                    state.sequence.id.encode("utf-8")
                ).hexdigest()[:12]
            )
            preview_cache = self._paths.project_cache_dir(project_dir)
            destination = content_addressed_child_path(
                preview_cache / "mlt",
                document.xml,
                namespace=sequence_namespace,
                suffix=".mlt",
            )
            if not destination.is_file():
                atomic_write_text(destination, document.xml)
            CacheManager(preview_cache).prune_files(
                "mlt",
                f"{sequence_namespace}-*.mlt",
                keep=16,
                max_age_seconds=7 * 24 * 60 * 60,
            )
        return destination

    def create_project(
        self,
        root: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> EditorProject:
        repository = ProjectRepository.create(root, name, profile)
        return EditorProject(repository, settings=self.settings, paths=self._paths)

    def open_project(
        self,
        root: str | Path,
        *,
        writable: bool = True,
        cooperative: bool = False,
    ) -> EditorProject:
        repository = ProjectRepository.open(root, writable=writable, cooperative=cooperative)
        return EditorProject(repository, settings=self.settings, paths=self._paths)

    def recent_projects(self, paths: list[str]) -> RecentProjectSnapshot:
        items: list[dict] = []
        totals = {
            "runningTaskCount": 0,
            "failedTaskCount": 0,
            "offlineAssetCount": 0,
            "pendingWorkflowCount": 0,
            "recentArtifactCount": 0,
        }
        for path_value in paths:
            path = Path(path_value)
            item = {
                "name": path.name,
                "path": str(path),
                "available": (path / "project.mfp").is_file(),
                "runningTaskCount": 0,
                "failedTaskCount": 0,
                "offlineAssetCount": 0,
                "pendingWorkflowCount": 0,
                "recentArtifact": "",
                "coverPath": "",
            }
            if item["available"]:
                try:
                    with ProjectRepository.open(path, writable=False) as repository:
                        tasks = TaskRepository(repository).list()
                        item["runningTaskCount"] = sum(task.status.is_active for task in tasks)
                        item["failedTaskCount"] = sum(task.status == TaskStatus.FAILED for task in tasks)
                        item["offlineAssetCount"] = sum(
                            not repository.catalog.resolve_asset_path(asset).is_file()
                            for asset in repository.catalog.list_assets()
                        )
                        item["pendingWorkflowCount"] = len(
                            repository.catalog.list_workflow_runs(active_only=True)
                        )
                        cover = self._project_covers.cover_for(repository)
                        item["coverPath"] = str(cover) if cover else ""
                        artifacts = [
                            value.resolve(path)
                            for task in reversed(tasks)
                            for value in reversed(task.artifacts)
                            if value.resolve(path).is_file()
                        ]
                        item["recentArtifact"] = str(artifacts[0]) if artifacts else ""
                except (RuntimeError, sqlite3.Error):
                    # A project with an older, writable-migratable schema is
                    # still available. The home screen simply omits live metrics
                    # until the user opens it through the writable boundary.
                    pass
                except OSError:
                    item["available"] = (path / "project.mfp").is_file()
            items.append(item)
            for key in (
                "runningTaskCount",
                "failedTaskCount",
                "offlineAssetCount",
                "pendingWorkflowCount",
            ):
                count = item[key]
                if isinstance(count, int):
                    totals[key] += count
            totals["recentArtifactCount"] += bool(item["recentArtifact"])
        return RecentProjectSnapshot(items=items, totals=totals)

    def asset_thumbnail_paths(
        self,
        project_dir: str | Path,
        *,
        width: int = 160,
        height: int = 90,
    ) -> dict[str, str]:
        thumbnails: dict[str, str] = {}
        with ProjectRepository.open(project_dir, writable=False) as repository:
            for asset in repository.catalog.list_assets():
                thumbnail = self._media_thumbnails.thumbnail_for(
                    repository,
                    asset,
                    width=width,
                    height=height,
                )
                if thumbnail is not None:
                    thumbnails[asset.id] = str(thumbnail)
        return thumbnails
