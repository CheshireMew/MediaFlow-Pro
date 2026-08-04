from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from mediaflow.application.analysis_task_handlers import AnalysisTaskHandlers
from mediaflow.application.asset_service import AssetService
from mediaflow.application.asset_task_handlers import (
    AssetTaskHandlers,
    DownloadTaskHandler,
)
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import (
    CancellationToken,
    TaskContext,
    TaskService,
)
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.downloads import DownloadEntry, DownloadPlan, DownloadRequest
from mediaflow.domain.enums import AssetKind, TaskKind, TaskStatus, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import MediaMetadata, ProjectProfile
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeScenesCommand,
    DownloadMediaCommand,
    ImportAssetCommand,
    TrackSubjectCommand,
)
from mediaflow.domain.tasks import (
    DownloadAnalysisTaskOutcome,
    ImportedAssetTaskOutcome,
    Task,
)
from mediaflow.domain.timeline import ClipTransform, ClipTransformKeyframe
from mediaflow.infrastructure.media_probe import ProbeResult
from mediaflow.infrastructure.output_reservation import output_set_transaction
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.visual_analysis import write_visual_analysis


class _Probe:
    def __init__(
        self,
        kind: AssetKind,
        *,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ):
        self.kind = kind
        self.entered = entered
        self.release = release

    def probe(
        self,
        _path: str | Path,
        *,
        timeline_profile: ProjectProfile | None = None,
    ) -> ProbeResult:
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(timeout=5)
        return ProbeResult(
            kind=self.kind,
            metadata=MediaMetadata(
                duration_frames=120,
                width=320 if self.kind == AssetKind.VIDEO else None,
                height=180 if self.kind == AssetKind.VIDEO else None,
                has_video=self.kind == AssetKind.VIDEO,
                has_audio=self.kind == AssetKind.AUDIO,
            ),
            suggested_profile=None,
        )


class _UnusedAssetRuntime:
    def generate_proxy(self, *_args, **_kwargs):
        raise AssertionError("proxy runtime is not used by import tests")

    def generate_waveform(self, *_args, **_kwargs):
        raise AssertionError("waveform runtime is not used by import tests")


class _DownloadRuntime:
    def __init__(self, paths: list[Path]):
        self.paths = paths

    def download_media(
        self,
        _request,
        _settings,
        *,
        progress,
        check_cancelled,
    ) -> list[Path]:
        check_cancelled()
        return list(self.paths)

    def archive_unrecorded_downloads(
        self,
        _paths: list[Path],
    ) -> tuple[Path, ...]:
        # These fixtures model already existing external files rather than
        # downloader-owned publications.
        return ()


class _AnalysisRuntime:
    def __init__(
        self,
        *,
        download_plan: DownloadPlan | None = None,
        analyze_entered: threading.Event | None = None,
        analyze_release: threading.Event | None = None,
    ):
        self.download_plan = download_plan
        self.analyze_entered = analyze_entered
        self.analyze_release = analyze_release
        self.write_calls = 0

    @staticmethod
    def output_transaction(destinations, *, overwrite):
        return output_set_transaction(destinations, overwrite=overwrite)

    def analyze_sequence_bounds(self, *_args, **_kwargs):
        raise AssertionError("sequence boundary runtime is not used by these tests")

    def analyze_loudness(self, *_args, **_kwargs):
        raise AssertionError("loudness runtime is not used by these tests")

    def detect_scenes(
        self,
        _source,
        _clip,
        _profile,
        *,
        threshold,
        check_cancelled,
        progress,
    ) -> list[int]:
        check_cancelled()
        return [10, 30]

    def track_subject(
        self,
        _source,
        _clip,
        _profile,
        *,
        mode,
        check_cancelled,
        progress,
    ) -> list[ClipTransformKeyframe]:
        check_cancelled()
        return [
            ClipTransformKeyframe(
                source_frame=0,
                transform=ClipTransform(scale_x=1.1, scale_y=1.1),
                source=mode,
                confidence=0.9,
            ),
            ClipTransformKeyframe(
                source_frame=60,
                transform=ClipTransform(x=-5.0, scale_x=1.1, scale_y=1.1),
                source=mode,
                confidence=0.8,
            ),
        ]

    def write_visual_analysis(self, path: Path, payload: dict) -> Path:
        self.write_calls += 1
        return write_visual_analysis(path, payload)

    def analyze_download(
        self,
        _url: str,
        _settings,
        *,
        check_cancelled,
    ) -> DownloadPlan:
        if self.analyze_entered is not None:
            self.analyze_entered.set()
        if self.analyze_release is not None:
            assert self.analyze_release.wait(timeout=5)
        check_cancelled()
        assert self.download_plan is not None
        return self.download_plan


def _task_service(
    repository: ProjectRepository,
    kind: TaskKind,
    handler,
) -> TaskService:
    def commit_completion(_task, persist, changes):
        with repository.transaction(), repository.coalesced_revision():
            completed = persist()
            for change in changes:
                change()
        return completed

    service = TaskService(
        TaskRepository(repository),
        max_workers=1,
        recover_expired=False,
        settlement_committer=commit_completion,
    )
    service.register(kind, handler)
    return service


def _subtitle_acquisition(
    repository: ProjectRepository,
) -> SubtitleAcquisitionService:
    return SubtitleAcquisitionService(
        repository,
        SubtitlePublicationService(repository),
    )


def _fail_outermost_transaction_commit(
    repository: ProjectRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_transaction = repository.transaction
    depth = 0

    @contextmanager
    def failing_transaction() -> Iterator[object]:
        nonlocal depth
        outermost = depth == 0
        depth += 1
        try:
            with original_transaction() as connection:
                yield connection
                if outermost:
                    raise RuntimeError(
                        "injected download database commit failure"
                    )
        finally:
            depth -= 1

    monkeypatch.setattr(
        repository,
        "transaction",
        failing_transaction,
    )


def _download_plan(url: str = "https://example.invalid/video") -> DownloadPlan:
    entry = DownloadEntry(
        index=1,
        media_id="video",
        title="Video",
        page_url=url,
        download_url=url,
    )
    return DownloadPlan(
        source_url=url,
        kind="single",
        title="Video",
        extractor="test",
        entries=[entry],
    )


def _visual_timeline(
    repository: ProjectRepository,
    root: Path,
):
    source = root / "visual-source.mp4"
    source.write_bytes(b"observable visual source")
    asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
    asset = repository.catalog.update_asset(
        asset.model_copy(
            update={
                "metadata": MediaMetadata(
                    duration_frames=120,
                    width=320,
                    height=180,
                    has_video=True,
                )
            }
        )
    )
    sequence_id = repository.catalog.get_project().main_sequence_id
    editor = TimelineEditor(repository, sequence_id)
    track = editor.add_track(TrackKind.VIDEO)
    clip = editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=120,
    )
    return sequence_id, asset, clip


def test_import_cancelled_during_probe_never_registers_asset(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Import Before Commit",
        "Import Before Commit",
    )
    source = tmp_path / "import-source.mp4"
    source.write_bytes(b"source")
    probe_entered = threading.Event()
    allow_probe_return = threading.Event()
    assets = AssetService(
        repository,
        _Probe(
            AssetKind.VIDEO,
            entered=probe_entered,
            release=allow_probe_return,
        ),
    )
    handlers = AssetTaskHandlers(
        repository,
        assets,
        _UnusedAssetRuntime(),
        _subtitle_acquisition(repository),
    )
    service = _task_service(repository, TaskKind.IMPORT, handlers.import_asset)
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            command=ImportAssetCommand(source_path=str(source.resolve())),
        )
        assert probe_entered.wait(timeout=5)
        service.cancel(started.id)
        allow_probe_return.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.CANCELLED
        assert completed.artifacts == []
        assert repository.catalog.list_assets() == []
        assert repository.subtitles.list_subtitle_documents() == []
    finally:
        allow_probe_return.set()
        service.shutdown()
        repository.close()


def test_import_cancelled_after_publication_completes_with_readable_asset(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Import After Commit",
        "Import After Commit",
    )
    source = tmp_path / "published-source.mp4"
    source.write_bytes(b"published source")
    assets = AssetService(repository, _Probe(AssetKind.VIDEO))
    handlers = AssetTaskHandlers(
        repository,
        assets,
        _UnusedAssetRuntime(),
        _subtitle_acquisition(repository),
    )
    published = threading.Event()
    allow_return = threading.Event()

    def delayed_return(context: TaskContext):
        completion = handlers.import_asset(context)
        published.set()
        assert allow_return.wait(timeout=5)
        return completion

    service = _task_service(repository, TaskKind.IMPORT, delayed_return)
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            command=ImportAssetCommand(source_path=str(source.resolve())),
        )
        assert published.wait(timeout=5)
        service.cancel(started.id)
        allow_return.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.COMPLETED
        assert isinstance(completed.outcome, ImportedAssetTaskOutcome)
        asset = repository.catalog.get_asset(completed.outcome.asset_id)
        assert repository.catalog.resolve_asset_path(asset).read_bytes() == (b"published source")
        assert completed.artifacts[0].resolve(repository.project_dir) == (source.resolve())
    finally:
        allow_return.set()
        service.shutdown()
        repository.close()


def test_download_asset_and_subtitle_registration_rolls_back_as_one_unit(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Atomic Download Registration",
        "Atomic Download Registration",
    )
    first = tmp_path / "first.srt"
    first.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nFirst\n",
        encoding="utf-8",
    )
    second = tmp_path / "second.srt"
    second.write_text("not a subtitle", encoding="utf-8")
    handler = DownloadTaskHandler(
        repository,
        AssetService(repository, _Probe(AssetKind.SUBTITLE)),
        _DownloadRuntime([first, second]),
        _subtitle_acquisition(repository),
        lambda: ServiceSettings(),
    )
    service = _task_service(repository, TaskKind.DOWNLOAD, handler.handle)
    plan = _download_plan()
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            command=DownloadMediaCommand(
                request=DownloadRequest(
                    entry=plan.entries[0],
                    output_directory=str(tmp_path.resolve()),
                )
            ),
        )
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.FAILED
        assert completed.artifacts == []
        assert repository.catalog.list_assets() == []
        assert repository.subtitles.list_subtitle_documents() == []
        assert not list(
            (
                repository.project_dir
                / "generated"
                / "subtitles"
            ).rglob("*.srt")
        )
        assert first.is_file()
        assert second.is_file()
    finally:
        service.shutdown()
        repository.close()


def test_download_database_commit_failure_withdraws_every_generated_subtitle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Download Commit Failure",
        "Download Commit Failure",
    )
    first = tmp_path / "first.srt"
    first.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nFirst\n",
        encoding="utf-8",
    )
    second = tmp_path / "second.srt"
    second.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nSecond\n",
        encoding="utf-8",
    )
    handler = DownloadTaskHandler(
        repository,
        AssetService(repository, _Probe(AssetKind.SUBTITLE)),
        _DownloadRuntime([first, second]),
        _subtitle_acquisition(repository),
        lambda: ServiceSettings(),
    )
    plan = _download_plan()
    task = Task(
        project_id=repository.catalog.get_project().id,
        command=DownloadMediaCommand(
            request=DownloadRequest(
                entry=plan.entries[0],
                output_directory=str(tmp_path.resolve()),
            )
        ),
    )
    context = TaskContext(
        task=task,
        project_dir=repository.project_dir,
        cancellation=CancellationToken(),
        report=lambda _progress: None,
    )
    try:
        _fail_outermost_transaction_commit(repository, monkeypatch)
        with pytest.raises(
            RuntimeError,
            match="injected download database commit failure",
        ):
            handler.handle(context)
            with (
                repository.transaction(),
                repository.coalesced_revision(),
            ):
                for change in context.project_changes():
                    change()

        assert repository.catalog.list_assets() == []
        assert repository.subtitles.list_subtitle_documents() == []
        assert not list(
            (
                repository.project_dir
                / "generated"
                / "subtitles"
            ).rglob("*.srt")
        )
        archived = list(
            (
                repository.project_dir
                / "archive"
                / "subtitle-publications"
            ).rglob("*.srt")
        )
        assert len(archived) == 2
        assert {
            path.read_text(encoding="utf-8-sig").strip().splitlines()[-1]
            for path in archived
        } == {"First", "Second"}
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("command_type", "saving_code"),
    [
        ("scene", "scene_detection_saving"),
        ("tracking", "subject_tracking_saving"),
    ],
)
def test_visual_analysis_cancelled_at_saving_publishes_no_file_or_timeline_edit(
    tmp_path: Path,
    command_type: str,
    saving_code: str,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / f"Visual Before Commit {command_type}",
        f"Visual Before Commit {command_type}",
    )
    sequence_id, asset, clip = _visual_timeline(repository, tmp_path)
    runtime = _AnalysisRuntime()
    handler = AnalysisTaskHandlers(
        repository,
        runtime,
        lambda: ServiceSettings(),
    )
    service = _task_service(repository, TaskKind.ANALYZE, handler.handle)
    cancellation_requested = threading.Event()

    def cancel_at_saving(event) -> None:
        progress = OperationProgress.model_validate(event.payload["progress"])
        if progress.message_code == saving_code and not cancellation_requested.is_set():
            cancellation_requested.set()
            service.cancel(event.task_id)

    service.events.subscribe(cancel_at_saving)
    command = (
        AnalyzeScenesCommand(
            sequence_id=sequence_id,
            clip_id=clip.id,
            threshold=0.35,
        )
        if command_type == "scene"
        else TrackSubjectCommand(
            sequence_id=sequence_id,
            clip_id=clip.id,
            mode="subject_tracking",
        )
    )
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            sequence_id=sequence_id,
            command=command,
            input_asset_ids=[asset.id],
        )
        completed = service.wait(started.id, timeout=5)
        state = repository.timeline.load_timeline(sequence_id)

        assert cancellation_requested.is_set()
        assert completed.status == TaskStatus.CANCELLED
        assert completed.artifacts == []
        assert runtime.write_calls == 0
        assert (
            repository.project_dir / "generated" / "visual-analysis" / f"{started.id}.json"
        ).exists() is False
        if command_type == "scene":
            assert state.markers == []
        else:
            persisted = next(item for item in state.clips if item.id == clip.id)
            assert persisted.transform_keyframes == []
    finally:
        service.shutdown()
        repository.close()


def test_visual_analysis_timeline_failure_rolls_back_db_and_archives_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Visual Failed Commit",
        "Visual Failed Commit",
    )
    sequence_id, asset, clip = _visual_timeline(repository, tmp_path)
    runtime = _AnalysisRuntime()
    handler = AnalysisTaskHandlers(
        repository,
        runtime,
        lambda: ServiceSettings(),
    )
    original = TimelineEditor.replace_scene_markers

    def fail_after_timeline_write(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("injected failure after timeline write")

    monkeypatch.setattr(
        TimelineEditor,
        "replace_scene_markers",
        fail_after_timeline_write,
    )
    service = _task_service(repository, TaskKind.ANALYZE, handler.handle)
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            sequence_id=sequence_id,
            command=AnalyzeScenesCommand(
                sequence_id=sequence_id,
                clip_id=clip.id,
            ),
            input_asset_ids=[asset.id],
        )
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.FAILED
        assert repository.timeline.load_timeline(sequence_id).markers == []
        assert (
            repository.project_dir / "generated" / "visual-analysis" / f"{started.id}.json"
        ).exists() is False
        archived = list((repository.project_dir / "archive" / "failed-task-artifacts").glob("*.json"))
        assert len(archived) == 1
        assert json.loads(archived[0].read_text(encoding="utf-8"))["frames"] == [
            10,
            30,
        ]
    finally:
        service.shutdown()
        repository.close()


def test_visual_analysis_cancelled_after_publication_completes_and_is_consumable(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Visual After Commit",
        "Visual After Commit",
    )
    sequence_id, asset, clip = _visual_timeline(repository, tmp_path)
    runtime = _AnalysisRuntime()
    handler = AnalysisTaskHandlers(
        repository,
        runtime,
        lambda: ServiceSettings(),
    )
    published = threading.Event()
    allow_return = threading.Event()

    def delayed_return(context: TaskContext):
        completion = handler.handle(context)
        published.set()
        assert allow_return.wait(timeout=5)
        return completion

    service = _task_service(repository, TaskKind.ANALYZE, delayed_return)
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            sequence_id=sequence_id,
            command=AnalyzeScenesCommand(
                sequence_id=sequence_id,
                clip_id=clip.id,
            ),
            input_asset_ids=[asset.id],
        )
        assert published.wait(timeout=5)
        service.cancel(started.id)
        allow_return.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.COMPLETED
        artifact = completed.artifacts[0].resolve(repository.project_dir)
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        markers = repository.timeline.load_timeline(sequence_id).markers
        assert payload["frames"] == [10, 30]
        assert [marker.frame for marker in markers] == payload["frames"]
    finally:
        allow_return.set()
        service.shutdown()
        repository.close()


def test_download_analysis_cancellation_reaches_runtime_before_artifact_write(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Download Analysis Cancellation",
        "Download Analysis Cancellation",
    )
    analyze_entered = threading.Event()
    allow_analyze_return = threading.Event()
    runtime = _AnalysisRuntime(
        download_plan=_download_plan(),
        analyze_entered=analyze_entered,
        analyze_release=allow_analyze_return,
    )
    handler = AnalysisTaskHandlers(
        repository,
        runtime,
        lambda: ServiceSettings(),
    )
    service = _task_service(repository, TaskKind.ANALYZE, handler.handle)
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            command=AnalyzeDownloadCommand(
                url="https://example.invalid/video",
            ),
        )
        assert analyze_entered.wait(timeout=5)
        service.cancel(started.id)
        allow_analyze_return.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.CANCELLED
        assert completed.artifacts == []
        assert (
            repository.project_dir / "cache" / "download-analysis" / f"{started.id}.json"
        ).exists() is False
    finally:
        allow_analyze_return.set()
        service.shutdown()
        repository.close()


def test_download_analysis_cancelled_after_write_completes_with_persisted_outcome(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Download Analysis After Commit",
        "Download Analysis After Commit",
    )
    plan = _download_plan()
    handler = AnalysisTaskHandlers(
        repository,
        _AnalysisRuntime(download_plan=plan),
        lambda: ServiceSettings(),
    )
    published = threading.Event()
    allow_return = threading.Event()

    def delayed_return(context: TaskContext):
        completion = handler.handle(context)
        published.set()
        assert allow_return.wait(timeout=5)
        return completion

    service = _task_service(repository, TaskKind.ANALYZE, delayed_return)
    try:
        started = service.start(
            project_id=repository.catalog.get_project().id,
            command=AnalyzeDownloadCommand(url=plan.source_url),
        )
        assert published.wait(timeout=5)
        service.cancel(started.id)
        allow_return.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.COMPLETED
        assert isinstance(completed.outcome, DownloadAnalysisTaskOutcome)
        assert completed.outcome.plan == plan
        artifact = completed.artifacts[0].resolve(repository.project_dir)
        assert DownloadPlan.model_validate_json(artifact.read_text(encoding="utf-8")) == plan
    finally:
        allow_return.set()
        service.shutdown()
        repository.close()
