from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.interchange_import import InterchangeImportService
from mediaflow.application.portable_timeline_import import PortableTimelineImportService
from mediaflow.application.ports import StructuredFileReader
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.voiceover_editing import VoiceoverEditingService
from mediaflow.domain.collaboration import (
    ProjectChange,
    ProjectChangeSet,
    ProjectEditAction,
    ProjectEditCommand,
)
from mediaflow.domain.interchange import LoadedInterchangeTimeline
from mediaflow.domain.portable_timeline import LoadedPortableTimeline
from mediaflow.domain.project import Asset, ProjectProfile, Sequence
from mediaflow.domain.project_archive import ProjectArchiveResult
from mediaflow.domain.project_records import ProjectVersionRecord
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.task_commands import ImportAssetCommand, TaskCommand
from mediaflow.domain.tasks import Task
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.voiceover import (
    VoiceoverCue,
    VoiceoverCueStatus,
    VoiceoverLatencyCalibration,
    VoiceoverTake,
)
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.mlt import (
    LoudnessAnalysisService,
    SequenceBoundaryAnalysisService,
    TimelineCompiler,
)
from mediaflow.infrastructure.project_archive_service import ProjectArchiveService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyDecision, ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_render_service import WebRenderService
from mediaflow.project_task_settlement import ProjectTaskResult, ProjectTaskSettlement


class EditorProjectDeliveryCommands:
    _repository: ProjectRepository
    _paths: RuntimePaths
    _portable_timelines: PortableTimelineImportService
    _interchange_timelines: InterchangeImportService
    _voiceover: VoiceoverEditingService
    _subtitle_publication: SubtitlePublicationService
    _timelines: dict[str, TimelineEditor]
    _history: ProjectEditHistory
    _structured_files: StructuredFileReader
    _tasks: TaskService
    _task_settlement: ProjectTaskSettlement
    _workflows: ProjectWorkflowService
    _project_archives: ProjectArchiveService
    _settings: ServiceSettings

    if TYPE_CHECKING:

        def _require_writable(self) -> None: ...
        def _reload_timelines(self) -> None: ...

    def inspect_portable_timeline(self, path: str | Path) -> LoadedPortableTimeline:
        return self._portable_timelines.inspect(path)

    def import_portable_timeline(
        self,
        path: str | Path,
        *,
        sequence_id: str,
    ) -> tuple[LoadedPortableTimeline, TimelineState, dict[str, Asset], list[str]]:
        self._require_writable()
        return self._portable_timelines.import_timeline(
            path,
            sequence_id=sequence_id,
        )

    def inspect_interchange_timeline(
        self,
        path: str | Path,
        *,
        sequence_id: str,
        frame_rate: float | None = None,
        media_mappings: dict[str, str] | None = None,
    ) -> LoadedInterchangeTimeline:
        default_profile = self._repository.sequences.get_sequence(sequence_id).profile
        return self._interchange_timelines.inspect(
            path,
            default_profile=default_profile,
            frame_rate=frame_rate,
            media_mappings=media_mappings,
        )

    def import_interchange_timeline(
        self,
        path: str | Path,
        *,
        sequence_id: str,
        name: str | None = None,
        frame_rate: float | None = None,
        media_mappings: dict[str, str] | None = None,
    ) -> tuple[LoadedInterchangeTimeline, Sequence, TimelineState, dict[str, Asset], list[str]]:
        self._require_writable()
        default_profile: ProjectProfile = self._repository.sequences.get_sequence(sequence_id).profile
        try:
            return self._interchange_timelines.import_timeline(
                path,
                name=name,
                default_profile=default_profile,
                frame_rate=frame_rate,
                media_mappings=media_mappings,
            )
        except BaseException:
            self._reload_timelines()
            raise

    def list_voiceover_cues(
        self,
        sequence_id: str,
        *,
        include_archived: bool = False,
    ) -> list[VoiceoverCue]:
        return self._voiceover.list_cues(
            sequence_id,
            include_archived=include_archived,
        )

    def create_voiceover_cue(
        self,
        sequence_id: str,
        *,
        start_frame: int,
        end_frame: int,
        text: str,
        speaker: str = "旁白",
        notes: str = "",
    ) -> VoiceoverCue:
        self._require_writable()
        return self._voiceover.create_cue(
            sequence_id,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
            speaker=speaker,
            notes=notes,
        )

    def update_voiceover_cue(
        self,
        cue_id: str,
        *,
        expected_revision: int,
        start_frame: int,
        end_frame: int,
        text: str,
        speaker: str,
        notes: str,
        status: VoiceoverCueStatus,
    ) -> VoiceoverCue:
        self._require_writable()
        return self._voiceover.update_cue(
            cue_id,
            expected_revision=expected_revision,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
            speaker=speaker,
            notes=notes,
            status=status,
        )

    def archive_voiceover_cue(
        self,
        cue_id: str,
        *,
        expected_revision: int,
    ) -> VoiceoverCue:
        self._require_writable()
        return self._voiceover.archive_cue(cue_id, expected_revision=expected_revision)

    def add_voiceover_take(
        self,
        cue_id: str,
        source: str | Path,
        *,
        name: str | None = None,
        notes: str = "",
        calibration_device_id: str | None = None,
    ) -> tuple[VoiceoverCue, VoiceoverTake]:
        self._require_writable()
        return self._voiceover.add_take(
            cue_id,
            source,
            name=name,
            notes=notes,
            calibration_device_id=calibration_device_id,
        )

    def get_voiceover_latency_calibration(
        self,
        device_id: str,
    ) -> VoiceoverLatencyCalibration | None:
        return self._voiceover.get_latency_calibration(device_id)

    def list_voiceover_latency_calibrations(self) -> list[VoiceoverLatencyCalibration]:
        return self._voiceover.list_latency_calibrations()

    def set_voiceover_latency_calibration(
        self,
        *,
        device_id: str,
        device_name: str,
        latency_samples: int,
        sample_rate: int = 48_000,
    ) -> VoiceoverLatencyCalibration:
        self._require_writable()
        return self._voiceover.set_latency_calibration(
            device_id=device_id,
            device_name=device_name,
            latency_samples=latency_samples,
            sample_rate=sample_rate,
        )

    def analyze_voiceover_latency_calibration(
        self,
        recording: str | Path,
        *,
        device_id: str,
        device_name: str,
        marker_offset_samples: int,
    ) -> VoiceoverLatencyCalibration:
        self._require_writable()
        return self._voiceover.analyze_latency_calibration(
            recording,
            device_id=device_id,
            device_name=device_name,
            marker_offset_samples=marker_offset_samples,
        )

    def update_voiceover_take(
        self,
        take_id: str,
        *,
        name: str,
        notes: str,
        rating: int,
    ) -> VoiceoverTake:
        self._require_writable()
        return self._voiceover.update_take(
            take_id,
            name=name,
            notes=notes,
            rating=rating,
        )

    def select_voiceover_take(
        self,
        cue_id: str,
        take_id: str,
        *,
        expected_revision: int,
    ) -> VoiceoverCue:
        self._require_writable()
        return self._voiceover.select_take(
            cue_id,
            take_id,
            expected_revision=expected_revision,
        )

    def archive_voiceover_take(
        self,
        take_id: str,
    ) -> tuple[VoiceoverCue, VoiceoverTake]:
        self._require_writable()
        return self._voiceover.archive_take(take_id)

    def place_voiceover_take(
        self,
        cue_id: str,
        *,
        expected_revision: int,
    ) -> VoiceoverCue:
        self._require_writable()
        return self._voiceover.place_selected_take(
            cue_id,
            expected_revision=expected_revision,
        )

    def create_version(self, name: str) -> ProjectVersionRecord:
        return self._repository.records.create_project_version(name)

    def list_versions(self) -> list[ProjectVersionRecord]:
        return self._repository.records.list_project_versions()

    def restore_version(self, version_id: str) -> ProjectVersionRecord:
        with self._repository.transaction():
            record = self._repository.records.restore_project_version(version_id)
            self._subtitle_publication.reconcile_document_srts()
        sequence_ids = {
            sequence.id for sequence in self._repository.sequences.list_sequences(include_archived=True)
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

    def create_project_archive(self, destination: str | Path) -> ProjectArchiveResult:
        return self._project_archives.create(destination)

    def export_fcpxml(
        self,
        sequence_id: str,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        state = self._repository.timeline.load_timeline(sequence_id)
        exporter = FcpxmlExportService(self._repository, self._paths)
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

    def proxy_decision(
        self,
        asset: Asset,
        *,
        dropped_frames: int = 0,
        manual: bool = False,
    ) -> ProxyDecision:
        return ProxyService.decision(asset, dropped_frames=dropped_frames, manual=manual)

    def sequence_boundary_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return SequenceBoundaryAnalysisService(
            TimelineCompiler(self._repository, self._paths),
            self._paths,
        ).snapshot_hash(state)

    def loudness_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return LoudnessAnalysisService(
            TimelineCompiler(self._repository, self._paths),
            self._paths,
        ).snapshot_hash(state)

    def read_loudness_metrics(self, sequence_id: str) -> dict[str, float]:
        metrics = LoudnessAnalysisService.read_current_metrics(
            self._repository.project_dir,
            sequence_id,
            current_project_revision=self._repository.content_revision(),
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
        project = self._repository.projects.get_project()
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
        path = self._structured_files.resolve_file(source)
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

    def committed_task_result(self, task_id: str) -> ProjectTaskResult | None:
        return self._task_settlement.committed_result(task_id)

    def archive_short_sequence(self, sequence_id: str) -> None:
        sequence = self._repository.sequences.archive_short_sequence(sequence_id)
        self._history.push(
            ProjectEditCommand(
                label="删除短视频序列",
                undo_actions=[
                    ProjectEditAction(
                        kind="sequence.archive-state",
                        payload={"sequence_id": sequence.id, "archived": False},
                    )
                ],
                redo_actions=[
                    ProjectEditAction(
                        kind="sequence.archive-state",
                        payload={"sequence_id": sequence.id, "archived": True},
                    )
                ],
            ),
            ProjectChangeSet(
                changes=[
                    ProjectChange(
                        path=f"/sequences/{sequence.id}/settings/archived",
                        action="update",
                        value=True,
                    )
                ]
            ),
        )

    def _apply_sequence_archive_history_action(
        self,
        action: ProjectEditAction,
    ) -> None:
        sequence_id = str(action.payload.get("sequence_id") or "")
        if bool(action.payload.get("archived")):
            self._repository.sequences.archive_short_sequence(sequence_id)
        else:
            self._repository.sequences.restore_short_sequence(sequence_id)

    def refresh_workflow_mode(self) -> ProjectWorkflowService:
        self._workflows.update_settings(self._settings)
        return self._workflows

    def update_settings(self, settings: ServiceSettings) -> None:
        self._settings = settings
        self.refresh_workflow_mode()
