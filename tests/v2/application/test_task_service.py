import json
import logging
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mediaflow.application.task_service import (
    TaskCompletion,
    TaskContext,
    TaskService,
    TaskShutdownTimeout,
    TaskStopped,
)
from mediaflow.automation import task_operations
from mediaflow.automation.contracts import AutomationRequest
from mediaflow.automation.operation_context import OperationContext
from mediaflow.composition import EditorApplication
from mediaflow.domain.asr import TranscriptionPlan
from mediaflow.domain.collaboration import ActorIdentity, ProjectMutationPlan
from mediaflow.domain.enums import (
    AssetKind,
    ExportFormat,
    TaskKind,
    TaskStatus,
    TrackKind,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import SequenceInOut
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeSequenceBoundsCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    TranscribeSequenceCommand,
)
from mediaflow.domain.tasks import (
    ArtifactReference,
    SequenceBoundaryTaskOutcome,
    Task,
)
from mediaflow.infrastructure.project_operation_repository import (
    ProjectOperationRepository,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.task_repository import TaskRepository


def test_real_task_generates_artifact_and_consumer_reads_persisted_result(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    task_repository = TaskRepository(project_repository)
    service = TaskService(task_repository, max_workers=1)
    events = []
    service.events.subscribe(events.append)

    def generate(context: TaskContext) -> TaskCompletion:
        output = context.project_dir / "generated" / "task-output.txt"
        payload = b"observable producer output"
        context.report(
            OperationProgress.determinate(
                "writing",
                completed=0,
                total=len(payload),
                unit="bytes",
            )
        )
        output.write_bytes(payload)
        context.report(
            OperationProgress.determinate(
                "writing",
                completed=output.stat().st_size,
                total=len(payload),
                unit="bytes",
            )
        )
        context.report(OperationProgress.indeterminate("verifying"))
        assert output.read_text(encoding="utf-8") == "observable producer output"
        return TaskCompletion.with_artifacts(ArtifactReference.project(context.project_dir, output))

    service.register(TaskKind.ANALYZE, generate)
    started = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://generate"),
    )
    completed = service.wait(started.id, timeout=5)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.artifacts == [ArtifactReference(scope="project", path="generated/task-output.txt")]
    assert completed.artifacts[0].resolve(root).read_text(encoding="utf-8") == ("observable producer output")
    assert task_repository.get(started.id).status == TaskStatus.COMPLETED
    assert [(item.step, item.status) for item in completed.execution_trace] == [
        ("writing", "success"),
        ("verifying", "success"),
    ]
    assert [event.event_type for event in events][-1] == "completed"
    event_tasks = [Task.model_validate(event.payload) for event in events]
    assert "kind" not in events[-1].payload
    assert event_tasks[-1] == completed

    service.shutdown()
    project_repository.close()


def test_published_task_result_retries_completion_receipt_instead_of_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Completion Receipt Retry"
    project_repository = ProjectRepository.create(
        root,
        "Completion Receipt Retry",
    )
    project = project_repository.projects.get_project()
    task_repository = TaskRepository(project_repository)
    service = TaskService(task_repository, max_workers=1)
    output = root / "generated" / "receipt-output.txt"
    completion_attempts = 0
    original_update = task_repository.update_owned

    def fail_first_completion(
        task: Task,
        owner_id: str,
        *,
        event_type: str,
        release_owner: bool = False,
    ):
        nonlocal completion_attempts
        if event_type == "completed":
            completion_attempts += 1
            if completion_attempts == 1:
                raise sqlite3.OperationalError("injected completion receipt failure")
        return original_update(
            task,
            owner_id,
            event_type=event_type,
            release_owner=release_owner,
        )

    monkeypatch.setattr(
        task_repository,
        "update_owned",
        fail_first_completion,
    )

    def publish(_context: TaskContext) -> TaskCompletion:
        output.write_text("published once", encoding="utf-8")
        return TaskCompletion.with_artifacts(ArtifactReference.project(root, output))

    service.register(TaskKind.ANALYZE, publish)
    try:
        started = service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://completion-receipt"),
        )
        completed = service.wait(started.id, timeout=5)

        assert completion_attempts == 2
        assert completed.status == TaskStatus.COMPLETED
        assert completed.error is None
        assert output.read_text(encoding="utf-8") == "published once"
        assert completed.artifacts[0].resolve(root) == output
        assert not any(event.event_type == "failed" for event in task_repository.events_after(0))
    finally:
        service.shutdown()
        project_repository.close()


def test_task_start_uses_one_sequence_identity_and_rejects_mismatch(
    tmp_path: Path,
) -> None:
    project_repository = ProjectRepository.create(
        tmp_path / "Task Sequence Identity",
        "Task Sequence Identity",
    )
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)
    service.register(
        TaskKind.EXPORT,
        lambda _context: TaskCompletion(),
    )
    command = ExportSequenceCommand(
        sequence_id=project.main_sequence_id,
        output_path=str(project_repository.project_dir / "exports" / "identity.mp4"),
    )
    try:
        with pytest.raises(
            ValueError,
            match="Task sequence must match",
        ):
            service.start(
                project_id=project.id,
                sequence_id="different-sequence",
                command=command,
            )
        assert repository.list() == []
        audio_highlights = ExportHighlightsCommand(
            sequence_id=project.main_sequence_id,
            candidate_ids=["candidate"],
            output_dir=str(project_repository.project_dir / "exports"),
            preset=ExportPreset(
                name="Audio only",
                format=ExportFormat.AUDIO,
                container="flac",
                encoder_policy=None,
                audio_codec="flac",
                pixel_format=None,
            ),
        )
        with pytest.raises(
            ValueError,
            match="高光批量导出必须使用视频预设",
        ):
            service.start(
                project_id=project.id,
                command=audio_highlights,
            )
        assert repository.list() == []

        started = service.start(
            project_id=project.id,
            command=command,
        )
        completed = service.wait(started.id, timeout=5)
        assert completed.sequence_id == project.main_sequence_id
    finally:
        service.shutdown()
        project_repository.close()


def test_non_executable_transcription_is_rejected_before_task_persistence(
    tmp_path: Path,
) -> None:
    project_repository = ProjectRepository.create(
        tmp_path / "Invalid Transcription",
        "Invalid Transcription",
    )
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)
    service.register(
        TaskKind.TRANSCRIBE,
        lambda _context: TaskCompletion(),
    )
    command = TranscribeSequenceCommand(
        plan=TranscriptionPlan(
            sequence_id=project.main_sequence_id,
            timeline_signature="empty-plan",
            dialogue_track_id="dialogue",
            timeline_start_frame=0,
            timeline_end_frame=0,
            fps_numerator=25,
            fps_denominator=1,
            sources=[],
            asr=AsrSettings(),
        )
    )
    try:
        with pytest.raises(
            ValueError,
            match="没有可识别的源音频区间",
        ):
            service.start(
                project_id=project.id,
                command=command,
            )
        assert repository.list() == []
    finally:
        service.shutdown()
        project_repository.close()


def test_cancel_before_handler_publish_keeps_sqlite_cancelled_and_no_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Pre Publish Cancel"
    project_repository = ProjectRepository.create(
        root,
        "Pre Publish Cancel",
    )
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)
    handler_started = threading.Event()
    allow_publish = threading.Event()
    output = root / "generated" / "pre-publish.txt"

    def publish(context: TaskContext) -> TaskCompletion:
        handler_started.set()
        assert allow_publish.wait(timeout=5)
        context.cancellation.raise_if_requested()
        output.write_text("must not publish", encoding="utf-8")
        return TaskCompletion.with_artifacts(ArtifactReference.project(root, output))

    service.register(TaskKind.ANALYZE, publish)
    try:
        started = service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://pre-publish"),
        )
        assert handler_started.wait(timeout=5)
        service.cancel(started.id)
        allow_publish.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.CANCELLED
        assert completed.artifacts == []
        assert repository.get(started.id) == completed
        assert output.exists() is False
    finally:
        allow_publish.set()
        service.shutdown()
        project_repository.close()


def test_cancel_after_file_publish_cannot_rewrite_success_as_cancelled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Post Publish Cancel"
    project_repository = ProjectRepository.create(
        root,
        "Post Publish Cancel",
    )
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)
    published = threading.Event()
    allow_handler_return = threading.Event()
    output = root / "generated" / "post-publish.txt"

    def publish(context: TaskContext) -> TaskCompletion:
        context.cancellation.raise_if_requested()
        output.write_text("committed output", encoding="utf-8")
        published.set()
        assert allow_handler_return.wait(timeout=5)
        return TaskCompletion.with_artifacts(ArtifactReference.project(root, output))

    service.register(TaskKind.ANALYZE, publish)
    try:
        started = service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://post-publish"),
        )
        assert published.wait(timeout=5)
        service.cancel(started.id)
        allow_handler_return.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.COMPLETED
        assert repository.get(started.id) == completed
        artifact = completed.artifacts[0].resolve(root)
        assert artifact == output.resolve()
        assert artifact.read_text(encoding="utf-8") == "committed output"
    finally:
        allow_handler_return.set()
        service.shutdown()
        project_repository.close()


def test_cancel_after_handler_return_loses_to_completed_sqlite_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Completion CAS"
    project_repository = ProjectRepository.create(root, "Completion CAS")
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)
    completion_cas_started = threading.Event()
    allow_completion_cas = threading.Event()
    output = root / "generated" / "completion-cas.txt"
    original_update_owned = repository.update_owned

    def delay_completed_cas(
        task: Task,
        owner_id: str,
        *,
        event_type: str,
        release_owner: bool = False,
    ) -> Task | None:
        if event_type == "completed":
            completion_cas_started.set()
            assert allow_completion_cas.wait(timeout=5)
        return original_update_owned(
            task,
            owner_id,
            event_type=event_type,
            release_owner=release_owner,
        )

    monkeypatch.setattr(repository, "update_owned", delay_completed_cas)

    def publish(context: TaskContext) -> TaskCompletion:
        context.cancellation.raise_if_requested()
        output.write_text("returned completion", encoding="utf-8")
        return TaskCompletion.with_artifacts(ArtifactReference.project(root, output))

    service.register(TaskKind.ANALYZE, publish)
    try:
        started = service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://completion-cas"),
        )
        assert completion_cas_started.wait(timeout=5)
        service.cancel(started.id)
        allow_completion_cas.set()
        completed = service.wait(started.id, timeout=5)

        assert completed.status == TaskStatus.COMPLETED
        assert completed.stop_request is None
        assert repository.get(started.id) == completed
        artifact = completed.artifacts[0].resolve(root)
        assert artifact.read_text(encoding="utf-8") == ("returned completion")
    finally:
        allow_completion_cas.set()
        service.shutdown()
        project_repository.close()


def test_running_task_is_resumed_after_handlers_are_registered(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    pending = repository.create(
        Task(
            project_id=project.id,
            command=ExportSequenceCommand(
                sequence_id=project.main_sequence_id,
                output_path=str(root / "exports" / "interrupted.mp4"),
            ),
        )
    )
    claimed = repository.claim(pending.id, "expired-owner", 1)
    assert claimed is not None
    running, recovered = claimed
    assert recovered is False
    time.sleep(0.01)

    service = TaskService(
        repository,
        max_workers=1,
        execution_owner_id="recovery-owner",
        recovery_poll_interval=60,
    )
    persisted_running = repository.get(running.id)
    assert persisted_running.status == TaskStatus.RUNNING
    assert persisted_running.execution_owner_id == "expired-owner"
    observed_recovery: list[bool] = []

    def finish_recovered(context: TaskContext) -> TaskCompletion:
        observed_recovery.append(context.recovered)
        return TaskCompletion()

    service.register(TaskKind.EXPORT, finish_recovered)
    assert service.recover_claimable() == 1
    assert service.recover_claimable() == 0
    assert service.wait(running.id, timeout=5).status == TaskStatus.COMPLETED
    assert observed_recovery == [True]

    service.shutdown()
    project_repository.close()


def test_failed_task_can_retry_and_history_removal_keeps_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)
    attempts = 0

    def generate(context: TaskContext) -> TaskCompletion:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first attempt failed")
        output = context.project_dir / "generated" / "retry-output.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("retry completed", encoding="utf-8")
        return TaskCompletion.with_artifacts(ArtifactReference.project(context.project_dir, output))

    service.register(TaskKind.ANALYZE, generate)
    original = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://retry"),
    )
    assert service.wait(original.id, timeout=5).status == TaskStatus.FAILED

    retried = service.retry(original.id)
    completed = service.wait(retried.id, timeout=5)
    artifact = completed.artifacts[0].resolve(root)
    assert retried.id != original.id
    assert completed.status == TaskStatus.COMPLETED
    assert repository.get(original.id).status == TaskStatus.FAILED
    assert artifact.read_text(encoding="utf-8") == "retry completed"

    service.delete(original.id)
    try:
        repository.get(original.id)
    except KeyError:
        pass
    else:
        raise AssertionError("removed task history is still persisted")
    assert service.clear_history() == 1
    assert artifact.read_text(encoding="utf-8") == "retry completed"
    assert repository.list() == []

    service.shutdown()
    project_repository.close()


def test_bulk_pause_resume_and_cancel_control_the_real_queue(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)

    def wait_for_control(context: TaskContext) -> TaskCompletion:
        while True:
            context.cancellation.raise_if_requested()
            time.sleep(0.005)

    service.register(TaskKind.ANALYZE, wait_for_control)
    tasks = [
        service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url=f"test://queue/{index}"),
        )
        for index in range(2)
    ]
    deadline = time.monotonic() + 2
    while (
        not any(service.get(task.id).status == TaskStatus.RUNNING for task in tasks)
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert service.pause_all() == 2
    assert [service.wait(task.id, timeout=5).status for task in tasks] == [
        TaskStatus.PAUSED,
        TaskStatus.PAUSED,
    ]

    for task in tasks:
        service.resume(task.id)
    assert service.cancel_all() == 2
    assert [service.wait(task.id, timeout=5).status for task in tasks] == [
        TaskStatus.CANCELLED,
        TaskStatus.CANCELLED,
    ]

    service.shutdown()
    project_repository.close()


def test_observer_failure_cannot_rewrite_a_completed_task(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    service = TaskService(TaskRepository(project_repository), max_workers=1)

    def fail_observer(_event) -> None:
        raise RuntimeError("observer failure")

    service.events.subscribe(fail_observer, include_snapshot=False)
    service.register(
        TaskKind.ANALYZE,
        lambda context: TaskCompletion.with_artifacts(
            ArtifactReference.project(context.project_dir, "generated/result.json")
        ),
    )

    started = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://observe"),
    )
    completed = service.wait(started.id, timeout=5)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.error is None
    assert completed.artifacts == [ArtifactReference(scope="project", path="generated/result.json")]
    service.shutdown()
    project_repository.close()


def test_repeated_idempotency_key_returns_one_persisted_task_and_runs_once(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    service = TaskService(repository, max_workers=1)
    executions = 0

    def execute(_context: TaskContext) -> TaskCompletion:
        nonlocal executions
        executions += 1
        return TaskCompletion()

    service.register(TaskKind.ANALYZE, execute)
    first = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://idempotent"),
        idempotency_key="automation:request-42:task.start",
    )
    assert service.wait(first.id, timeout=5).status == TaskStatus.COMPLETED
    repeated = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://idempotent"),
        idempotency_key="automation:request-42:task.start",
    )

    assert repeated.id == first.id
    assert executions == 1
    assert [task.id for task in repository.list()] == [first.id]
    service.shutdown()
    project_repository.close()


def test_stale_owner_update_cannot_overwrite_a_newer_control_request(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    first_repository = TaskRepository(project_repository)
    second_repository = TaskRepository(project_repository)
    created = first_repository.create(
        Task(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://concurrent"),
        )
    )
    claimed = first_repository.claim(created.id, "owner-a", 5_000)
    assert claimed is not None
    running, _ = claimed
    stale = second_repository.get(created.id)
    requested = first_repository.request_stop(created.id, "pause")
    assert requested is not None and requested.stop_request == "pause"

    overwritten = second_repository.update_owned(
        stale.model_copy(
            update={
                "progress": OperationProgress.indeterminate("stale_writer"),
            }
        ),
        "owner-a",
        event_type="progress",
    )

    assert overwritten is None
    persisted = first_repository.get(created.id)
    assert persisted.stop_request == "pause"
    assert persisted.progress == running.progress
    project_repository.close()


def test_cancel_escalation_wins_after_worker_observed_an_earlier_pause(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    service = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
    )
    handler_started = threading.Event()
    pause_observed = threading.Event()
    persist_stop = threading.Event()

    def controlled(context: TaskContext) -> TaskCompletion:
        handler_started.set()
        try:
            while True:
                context.cancellation.raise_if_requested()
                time.sleep(0.005)
        except TaskStopped:
            pause_observed.set()
            assert persist_stop.wait(timeout=5)
            raise

    service.register(TaskKind.ANALYZE, controlled)
    started = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(
            url="test://pause-then-cancel",
        ),
    )
    assert handler_started.wait(timeout=5)
    service.pause(started.id)
    assert pause_observed.wait(timeout=5)

    service.cancel(started.id)
    persisted_control = service.get(started.id)
    assert persisted_control.stop_request == "cancel"
    persist_stop.set()
    settled = service.wait(started.id, timeout=5)

    assert settled.status == TaskStatus.CANCELLED
    assert settled.stop_request is None
    service.shutdown()
    project_repository.close()


def test_progress_updates_are_throttled_and_finished_future_is_released(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    service = TaskService(TaskRepository(project_repository), max_workers=1)
    events = []
    service.events.subscribe(events.append, include_snapshot=False)

    def noisy(context: TaskContext) -> TaskCompletion:
        for value in range(1000):
            context.report(
                OperationProgress.determinate(
                    "working",
                    completed=value,
                    total=1000,
                    unit="items",
                )
            )
        return TaskCompletion()

    service.register(TaskKind.ANALYZE, noisy)
    started = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://noisy"),
    )
    assert service.wait(started.id, timeout=5).status == TaskStatus.COMPLETED
    deadline = time.monotonic() + 1
    while started.id in service._execution.futures and time.monotonic() < deadline:
        time.sleep(0.001)

    progress_events = [event for event in events if event.event_type == "progress"]
    assert len(progress_events) < 150
    assert started.id not in service._execution.futures
    service.shutdown()
    project_repository.close()


def test_cross_process_idempotent_wait_blocks_until_persisted_completion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    first = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        recover_expired=False,
        execution_owner_id="owner-a",
    )
    second = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        recover_expired=False,
        execution_owner_id="owner-b",
    )
    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []

    def execute(_context: TaskContext) -> TaskCompletion:
        executions.append("run")
        started.set()
        assert release.wait(5)
        return TaskCompletion()

    first.register(TaskKind.ANALYZE, execute)
    second.register(TaskKind.ANALYZE, execute)
    original = first.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://cross-process"),
        idempotency_key="automation:cross-process:task.start",
    )
    assert started.wait(5)
    repeated = second.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://cross-process"),
        idempotency_key="automation:cross-process:task.start",
    )
    assert repeated.id == original.id

    result: list[Task] = []
    waiter = threading.Thread(
        target=lambda: result.append(second.wait(repeated.id, timeout=5)),
    )
    waiter.start()
    time.sleep(0.15)
    assert waiter.is_alive()

    release.set()
    waiter.join(5)
    assert not waiter.is_alive()
    assert result[0].status == TaskStatus.COMPLETED
    assert executions == ["run"]
    first.shutdown()
    second.shutdown()
    project_repository.close()


def test_separate_python_process_waits_for_persisted_terminal_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ProcessWait"
    project_repository = ProjectRepository.create(root, "ProcessWait")
    project = project_repository.projects.get_project()
    service = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        recover_expired=False,
        execution_owner_id="producer-process",
    )
    started = threading.Event()
    release = threading.Event()

    def execute(_context: TaskContext) -> TaskCompletion:
        started.set()
        assert release.wait(10)
        return TaskCompletion()

    service.register(TaskKind.ANALYZE, execute)
    task = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://process-wait"),
    )
    assert started.wait(5)
    child_code = """
import json
import sys
import time
from mediaflow.application.task_service import TaskService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.task_repository import TaskRepository

with ProjectRepository.open(sys.argv[1], writable=False) as project:
    service = TaskService(
        TaskRepository(project),
        recover_expired=False,
    )
    print("READY", flush=True)
    started_at = time.monotonic()
    task = service.wait(sys.argv[2], timeout=10)
    service.shutdown()
print(json.dumps({
    "status": task.status.value,
    "elapsed": time.monotonic() - started_at,
}))
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_code,
            str(root),
            task.id,
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        time.sleep(0.2)
        assert child.poll() is None
        release.set()
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, stderr
        observed = json.loads(stdout)
        assert observed["status"] == TaskStatus.COMPLETED.value
        assert observed["elapsed"] >= 0.15
    finally:
        release.set()
        if child.poll() is None:
            child.kill()
            child.communicate()
        service.shutdown()
        project_repository.close()


def test_live_lease_heartbeat_prevents_second_service_recovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    first = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        recover_expired=False,
        execution_owner_id="owner-a",
        lease_duration_ms=90,
        recovery_poll_interval=0.02,
    )
    second = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        execution_owner_id="owner-b",
        lease_duration_ms=90,
        recovery_poll_interval=0.02,
    )
    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []

    def first_handler(_context: TaskContext) -> TaskCompletion:
        executions.append("owner-a")
        started.set()
        assert release.wait(5)
        return TaskCompletion()

    def second_handler(_context: TaskContext) -> TaskCompletion:
        executions.append("owner-b")
        return TaskCompletion()

    first.register(TaskKind.ANALYZE, first_handler)
    second.register(TaskKind.ANALYZE, second_handler)
    task = first.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://heartbeat"),
    )
    assert started.wait(5)
    time.sleep(0.4)
    running = first.get(task.id)
    assert running.status == TaskStatus.RUNNING
    assert running.execution_owner_id == "owner-a"
    assert executions == ["owner-a"]

    release.set()
    assert second.wait(task.id, timeout=5).status == TaskStatus.COMPLETED
    assert executions == ["owner-a"]
    first.shutdown()
    second.shutdown()
    project_repository.close()


def test_subsecond_requested_lease_uses_scheduler_safe_expiry(
    tmp_path: Path,
) -> None:
    project_repository = ProjectRepository.create(
        tmp_path / "Lease Floor",
        "Lease Floor",
    )
    project = project_repository.projects.get_project()
    service = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        recover_expired=False,
        lease_duration_ms=50,
    )
    started = threading.Event()
    release = threading.Event()

    def execute(_context: TaskContext) -> TaskCompletion:
        started.set()
        assert release.wait(5)
        return TaskCompletion()

    service.register(TaskKind.ANALYZE, execute)
    try:
        task = service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://scheduler-safe-lease"),
        )
        assert started.wait(5)
        running = service.get(task.id)
        assert running.heartbeat_at is not None
        assert running.lease_expires_at is not None
        assert (running.lease_expires_at - running.heartbeat_at) >= 1_000
    finally:
        release.set()
        service.shutdown()
        project_repository.close()


def test_recovery_poll_logs_throttled_storage_failures_and_keeps_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_repository = ProjectRepository.create(
        tmp_path / "Recovery Health",
        "Recovery Health",
    )
    repository = TaskRepository(project_repository)
    attempts = 0
    recovered_after_failures = threading.Event()

    def flaky_list_claimable(_at_ms: int) -> list[Task]:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise sqlite3.OperationalError("injected recovery storage failure")
        recovered_after_failures.set()
        return []

    monkeypatch.setattr(
        repository,
        "list_claimable",
        flaky_list_claimable,
    )
    caplog.set_level(
        logging.WARNING,
        logger="mediaflow.application.task_service",
    )
    service = TaskService(
        repository,
        recovery_poll_interval=0.01,
    )
    try:
        assert recovered_after_failures.wait(2)
    finally:
        service.shutdown()
        project_repository.close()

    recovery_logs = [
        record for record in caplog.records if "Task recovery poll failed" in record.getMessage()
    ]
    assert attempts >= 4
    assert len(recovery_logs) == 1
    assert recovery_logs[0].exc_info is not None


def test_heartbeat_logs_transient_storage_failure_and_renews_next_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_repository = ProjectRepository.create(
        tmp_path / "Heartbeat Health",
        "Heartbeat Health",
    )
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    original_renew = repository.renew_lease
    renew_attempts = 0
    renewed_after_failure = threading.Event()

    def flaky_renew(
        task_id: str,
        owner_id: str,
        lease_duration_ms: int,
    ) -> Task | None:
        nonlocal renew_attempts
        renew_attempts += 1
        if renew_attempts == 1:
            raise sqlite3.OperationalError("injected heartbeat storage failure")
        renewed = original_renew(
            task_id,
            owner_id,
            lease_duration_ms,
        )
        renewed_after_failure.set()
        return renewed

    monkeypatch.setattr(repository, "renew_lease", flaky_renew)
    caplog.set_level(
        logging.WARNING,
        logger="mediaflow.application.task_service",
    )
    service = TaskService(
        repository,
        max_workers=1,
        recover_expired=False,
        lease_duration_ms=900,
    )

    def wait_for_renewal(_context: TaskContext) -> TaskCompletion:
        assert renewed_after_failure.wait(5)
        return TaskCompletion()

    service.register(TaskKind.ANALYZE, wait_for_renewal)
    try:
        started = service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://heartbeat-storage-recovery"),
        )
        assert service.wait(started.id, timeout=5).status == TaskStatus.COMPLETED
    finally:
        service.shutdown()
        project_repository.close()

    heartbeat_logs = [
        record for record in caplog.records if "could not renew its lease" in record.getMessage()
    ]
    assert renew_attempts >= 2
    assert len(heartbeat_logs) == 1
    assert heartbeat_logs[0].exc_info is not None


def test_project_lock_prevents_a_second_python_process_writer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ProcessLease"
    project_repository = ProjectRepository.create(root, "ProcessLease")
    child_code = """
import json
import sys
from mediaflow.infrastructure.project_repository import ProjectRepository

with ProjectRepository.open(sys.argv[1], writable=True) as project:
    print(json.dumps({
        "read_only": project.read_only,
        "owns_project_lock": project.owns_project_lock,
    }))
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_code,
            str(root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, stderr
        observed = json.loads(stdout)
        assert observed == {
            "read_only": True,
            "owns_project_lock": False,
        }
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
        project_repository.close()


def test_expired_lease_is_claimed_once_and_old_owner_cannot_complete(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    repository = TaskRepository(project_repository)
    pending = repository.create(
        Task(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url="test://expired"),
        )
    )
    claimed = repository.claim(pending.id, "dead-owner", 1)
    assert claimed is not None
    stale_running, _ = claimed
    time.sleep(0.01)

    first = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        execution_owner_id="recovery-a",
        recovery_poll_interval=60,
    )
    second = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        execution_owner_id="recovery-b",
        recovery_poll_interval=60,
    )
    executions: list[str] = []

    def handler(label: str):
        def execute(context: TaskContext) -> TaskCompletion:
            assert context.recovered is True
            executions.append(label)
            return TaskCompletion()

        return execute

    first.register(TaskKind.ANALYZE, handler("recovery-a"))
    second.register(TaskKind.ANALYZE, handler("recovery-b"))
    barrier = threading.Barrier(3)

    def recover(service: TaskService) -> None:
        barrier.wait()
        service.recover_claimable()

    threads = [threading.Thread(target=recover, args=(service,)) for service in (first, second)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(5)

    completed = first.wait(pending.id, timeout=5)
    assert completed.status == TaskStatus.COMPLETED
    assert len(executions) == 1
    stale_completion = repository.update_owned(
        stale_running.model_copy(
            update={
                "status": TaskStatus.COMPLETED,
                "progress": OperationProgress.determinate(
                    "completed",
                    completed=1,
                    total=1,
                    unit="task",
                ),
            }
        ),
        "dead-owner",
        event_type="completed",
        release_owner=True,
    )
    assert stale_completion is None
    first.shutdown()
    second.shutdown()
    project_repository.close()


def test_remote_pause_is_persisted_and_resume_gets_a_fresh_lease(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.projects.get_project()
    first = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        recover_expired=False,
        execution_owner_id="owner-a",
        lease_duration_ms=120,
    )
    second = TaskService(
        TaskRepository(project_repository),
        max_workers=1,
        recover_expired=False,
        execution_owner_id="owner-b",
        lease_duration_ms=120,
    )
    started = threading.Event()
    executions = 0
    recovered_flags: list[bool] = []
    execution_lock = threading.Lock()

    def execute(context: TaskContext) -> TaskCompletion:
        nonlocal executions
        with execution_lock:
            executions += 1
            attempt = executions
        recovered_flags.append(context.recovered)
        if attempt == 1:
            started.set()
            while True:
                context.cancellation.raise_if_requested()
                time.sleep(0.01)
        return TaskCompletion()

    first.register(TaskKind.ANALYZE, execute)
    second.register(TaskKind.ANALYZE, execute)
    task = first.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://remote-pause"),
    )
    assert started.wait(5)

    second.pause(task.id)
    paused = second.wait(task.id, timeout=5)
    assert paused.status == TaskStatus.PAUSED
    assert paused.execution_owner_id is None
    assert paused.heartbeat_at is None
    assert paused.lease_expires_at is None
    assert paused.stop_request is None

    second.resume(task.id)
    completed = second.wait(task.id, timeout=5)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.execution_owner_id is None
    assert executions == 2
    assert recovered_flags == [False, False]
    first.shutdown()
    second.shutdown()
    project_repository.close()


def test_task_resume_scheduling_replays_after_receipt_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    root = tmp_path / "ReceiptCrash"
    with application.create_project(root, "ReceiptCrash") as project:
        task = project._tasks.repository.create(
            Task(
                project_id=project.get_project().id,
                command=AnalyzeDownloadCommand(url="test://receipt-crash"),
                status=TaskStatus.PAUSED,
                progress=OperationProgress.indeterminate("paused"),
            )
        )
        project._tasks._execution.handlers[TaskKind.ANALYZE] = lambda _context: TaskCompletion()
        envelope = AutomationRequest(
            operation="task.resume",
            project=str(root),
            arguments={"task_id": task.id, "timeout": 5},
            request_id="resume-after-receipt-crash",
            base_revision=project.content_revision(),
            actor={"kind": "agent", "id": "task-receipt-test"},
            client_id="pytest-task-receipt",
        )
        retry_flags: list[bool] = []

        def action(retrying: bool) -> dict:
            retry_flags.append(retrying)
            return task_operations.resume_task(
                OperationContext(
                    project,
                    application,
                    envelope,
                    retrying=retrying,
                )
            )

        initial_receipt = OperationContext.task_receipt(task)
        assert initial_receipt["task"]["status"] == TaskStatus.PAUSED.value

        original_save = ProjectOperationRepository.save_result

        def fail_receipt_write(
            _repository: ProjectRepository,
            _request_id: str,
            _operation: str,
            _input_hash: str,
            _result: dict,
        ) -> dict:
            raise OSError("injected receipt write failure")

        monkeypatch.setattr(
            ProjectOperationRepository,
            "save_result",
            fail_receipt_write,
        )
        with pytest.raises(OSError, match="receipt write failure"):
            project.execute_automation_request(
                envelope.request_id,
                envelope.operation,
                envelope.arguments,
                action,
                atomic=False,
                actor=ActorIdentity(kind="system", id="task-service-test"),
                mutation_plan=ProjectMutationPlan.scoped([]),
            )

        completed = project.wait_for_task(task.id, timeout=5)
        assert completed.status == TaskStatus.COMPLETED
        receipt = project._repository._fetchone(
            "SELECT state FROM automation_request WHERE request_id=?",
            (envelope.request_id,),
        )
        assert receipt["state"] == "running"
        assert (
            project._repository._fetchone(
                "SELECT task_id FROM task_consumption WHERE task_id=?",
                (task.id,),
            )
            is not None
        )

        monkeypatch.setattr(
            ProjectOperationRepository,
            "save_result",
            original_save,
        )
        recovered, _recovered_event = project.execute_automation_request(
            envelope.request_id,
            envelope.operation,
            envelope.arguments,
            action,
            atomic=False,
            actor=ActorIdentity(kind="system", id="task-service-test"),
            mutation_plan=ProjectMutationPlan.scoped([]),
        )
        cached, _cached_event = project.execute_automation_request(
            envelope.request_id,
            envelope.operation,
            envelope.arguments,
            action,
            atomic=False,
            actor=ActorIdentity(kind="system", id="task-service-test"),
            mutation_plan=ProjectMutationPlan.scoped([]),
        )

        assert recovered["task"]["id"] == task.id
        assert recovered["task"]["status"] == TaskStatus.COMPLETED.value
        assert cached == recovered
        assert retry_flags == [False, True]


def test_sequence_boundary_resume_reopen_does_not_reapply_consumed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    root = tmp_path / "BoundaryReceiptCrash"
    source = tmp_path / "boundary-source.mp4"
    source.write_bytes(b"sequence boundary source")
    envelope: AutomationRequest
    task_id: str
    committed_content_revision: int
    committed_timeline_revision: int

    with application.create_project(
        root,
        "BoundaryReceiptCrash",
    ) as project:
        sequence_id = project.get_project().main_sequence_id
        asset = project._repository.assets.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        asset = project._repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={
                            "duration_frames": 100,
                            "width": 1920,
                            "height": 1080,
                        }
                    )
                }
            )
        )
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=100,
        )
        project._history.clear()
        snapshot_hash = project.sequence_boundary_snapshot_hash(sequence_id)
        outcome = SequenceBoundaryTaskOutcome(
            analysis=SequenceBoundaryAnalysis(
                sequence_id=sequence_id,
                snapshot_hash=snapshot_hash,
                duration_frames=100,
                suggested=SequenceInOut(
                    in_frame=10,
                    out_frame=90,
                ),
                speech_in_frame=10,
                speech_out_frame=90,
            )
        )
        task = project._tasks.repository.create(
            Task(
                project_id=project.get_project().id,
                sequence_id=sequence_id,
                command=AnalyzeSequenceBoundsCommand(
                    sequence_id=sequence_id,
                    snapshot_hash=snapshot_hash,
                ),
                status=TaskStatus.PAUSED,
                progress=OperationProgress.indeterminate("paused"),
            )
        )
        task_id = task.id
        project._tasks._execution.handlers[TaskKind.ANALYZE] = (
            lambda _context: TaskCompletion(outcome=outcome)
        )
        envelope = AutomationRequest(
            operation="task.resume",
            project=str(root),
            arguments={"task_id": task.id, "timeout": 5},
            request_id="boundary-resume-receipt-crash",
            base_revision=project.content_revision(),
            actor={"kind": "agent", "id": "boundary-receipt-test"},
            client_id="pytest-boundary-receipt",
        )

        def action(retrying: bool) -> dict:
            return task_operations.resume_task(
                OperationContext(
                    project,
                    application,
                    envelope,
                    retrying=retrying,
                )
            )

        original_save = ProjectOperationRepository.save_result

        def fail_receipt_write(
            _repository: ProjectRepository,
            _request_id: str,
            _operation: str,
            _input_hash: str,
            _result: dict,
        ) -> dict:
            raise OSError("injected boundary receipt failure")

        monkeypatch.setattr(
            ProjectOperationRepository,
            "save_result",
            fail_receipt_write,
        )
        with pytest.raises(OSError, match="boundary receipt failure"):
            project.execute_automation_request(
                envelope.request_id,
                envelope.operation,
                envelope.arguments,
                action,
                atomic=False,
                actor=ActorIdentity(kind="system", id="task-service-test"),
                mutation_plan=ProjectMutationPlan.scoped([]),
            )

        completed = project.wait_for_task(task.id, timeout=5)
        assert completed.status == TaskStatus.COMPLETED
        assert project.load_timeline(sequence_id).sequence.in_out == SequenceInOut(
            in_frame=10,
            out_frame=90,
        )
        assert (
            project._repository._fetchone(
                "SELECT task_id FROM task_consumption WHERE task_id=?",
                (task.id,),
            )
            is not None
        )

        applied = project.load_timeline(sequence_id)
        assert applied.sequence.in_out == SequenceInOut(
            in_frame=10,
            out_frame=90,
        )
        assert len(project._history._undo) == 1
        task_group = project._repository.history.get(f"task-{task.id}")
        assert task_group is not None
        assert task_group.write_set == [f"/sequences/{sequence_id}/settings/in_out"]
        task_event = project._repository.events.for_undo_group(f"task-{task.id}")
        assert task_event is not None
        assert task_event.operation == "task.analyze_sequence_bounds"
        assert task_event.write_set == task_group.write_set
        assert task_event.changes[0].value == {
            "in_frame": 10,
            "out_frame": 90,
        }
        committed_content_revision = project.content_revision()
        committed_timeline_revision = project.get_sequence(sequence_id).timeline_revision

    monkeypatch.setattr(
        ProjectOperationRepository,
        "save_result",
        original_save,
    )
    with application.open_project(root, writable=True) as reopened:
        retry_flags: list[bool] = []

        def retry_action(retrying: bool) -> dict:
            retry_flags.append(retrying)
            return task_operations.resume_task(
                OperationContext(
                    reopened,
                    application,
                    envelope,
                    retrying=retrying,
                )
            )

        recovered, _recovered_event = reopened.execute_automation_request(
            envelope.request_id,
            envelope.operation,
            envelope.arguments,
            retry_action,
            atomic=False,
            actor=ActorIdentity(kind="system", id="task-service-test"),
            mutation_plan=ProjectMutationPlan.scoped([]),
        )
        assert recovered["task"]["id"] == task_id
        committed = reopened.committed_task_result(task_id)
        assert committed is not None
        assert committed.sequence_bounds_status == "applied"
        assert retry_flags == [True]
        assert reopened.content_revision() == committed_content_revision
        assert (
            reopened.get_sequence(reopened.get_project().main_sequence_id).timeline_revision
            == committed_timeline_revision
        )
        assert reopened.load_timeline(
            reopened.get_project().main_sequence_id
        ).sequence.in_out == SequenceInOut(
            in_frame=10,
            out_frame=90,
        )
        assert reopened._history.can_undo is False
        assert reopened.can_undo is True
        undo_result, undo_event = reopened.execute_history_command(
            "undo",
            request_id="undo-boundary-task",
            base_revision=reopened.content_revision(),
            actor=ActorIdentity(kind="agent", id="boundary-receipt-test"),
            undo_group_id=f"task-{task_id}",
        )
        assert undo_result["direction"] == "undo"
        assert undo_event.write_set == [
            f"/sequences/{reopened.get_project().main_sequence_id}/settings/in_out"
        ]
        assert reopened.load_timeline(reopened.get_project().main_sequence_id).sequence.in_out is None


def test_task_settlement_insert_failure_rolls_back_and_recovers_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    root = tmp_path / "ConsumptionRollback"
    source = tmp_path / "consumption-source.mp4"
    source.write_bytes(b"task consumption rollback source")

    with application.create_project(
        root,
        "ConsumptionRollback",
    ) as project:
        sequence_id = project.get_project().main_sequence_id
        asset = project._repository.assets.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        asset = project._repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={
                            "duration_frames": 100,
                            "width": 1920,
                            "height": 1080,
                        }
                    )
                }
            )
        )
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=100,
        )
        project._history.clear()
        snapshot_hash = project.sequence_boundary_snapshot_hash(sequence_id)
        outcome = SequenceBoundaryTaskOutcome(
            analysis=SequenceBoundaryAnalysis(
                sequence_id=sequence_id,
                snapshot_hash=snapshot_hash,
                duration_frames=100,
                suggested=SequenceInOut(
                    in_frame=10,
                    out_frame=90,
                ),
                speech_in_frame=10,
                speech_out_frame=90,
            )
        )
        handler_entered = threading.Event()
        release_handler = threading.Event()

        def complete_after_release(_context: TaskContext) -> TaskCompletion:
            handler_entered.set()
            assert release_handler.wait(timeout=5)
            return TaskCompletion(outcome=outcome)

        project._tasks._execution.handlers[TaskKind.ANALYZE] = complete_after_release
        revision_before = project.content_revision()
        timeline_revision_before = project.get_sequence(sequence_id).timeline_revision
        with project._repository.transaction() as connection:
            connection.execute(
                """CREATE TRIGGER fail_task_consumption_insert
                   BEFORE INSERT ON task_consumption
                   BEGIN
                       SELECT RAISE(
                           ABORT,
                           'injected task consumption failure'
                       );
                   END"""
            )
        started = project.start_task(
            AnalyzeSequenceBoundsCommand(
                sequence_id=sequence_id,
                snapshot_hash=snapshot_hash,
            ),
            sequence_id=sequence_id,
        )
        assert handler_entered.wait(timeout=5)
        worker = project._tasks._execution.futures[started.id]
        release_handler.set()
        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected task consumption failure",
        ):
            worker.result(timeout=5)
        unsettled = project.get_task(started.id)
        assert unsettled.status == TaskStatus.RUNNING

        assert project.load_timeline(sequence_id).sequence.in_out is None
        assert project.content_revision() == revision_before
        assert project.get_sequence(sequence_id).timeline_revision == timeline_revision_before
        assert project._history.can_undo is False
        assert (
            project._repository._fetchone(
                "SELECT task_id FROM task_consumption WHERE task_id=?",
                (started.id,),
            )
            is None
        )
        with project._repository.transaction() as connection:
            connection.execute("DROP TRIGGER fail_task_consumption_insert")
            connection.execute(
                "UPDATE task SET heartbeat_at=0, lease_expires_at=1 WHERE id=?",
                (started.id,),
            )

        recovered = project._tasks.recover_claimable()
        assert recovered == 1
        completed = project.wait_for_task(started.id, timeout=5)
        assert completed.status == TaskStatus.COMPLETED
        applied = project.committed_task_result(completed.id)
        assert applied is not None
        replayed = project.committed_task_result(completed.id)

        assert applied.sequence_bounds_status == "applied"
        assert replayed == applied
        assert project.load_timeline(sequence_id).sequence.in_out == SequenceInOut(
            in_frame=10,
            out_frame=90,
        )


def test_task_preparation_cannot_write_project_outside_command_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    with application.create_project(
        tmp_path / "TaskWriteGuard",
        "TaskWriteGuard",
    ) as project:
        revision = project.content_revision()

        def illegal_write(_context: TaskContext) -> TaskCompletion:
            project._repository.sequences.create_short_sequence("illegal")
            return TaskCompletion()

        project._tasks._execution.handlers[TaskKind.ANALYZE] = illegal_write
        started = project.start_task(AnalyzeDownloadCommand(url="test://illegal-project-write"))
        failed = project.wait_for_task(started.id, timeout=5)

        assert failed.status == TaskStatus.FAILED
        assert "后台任务准备阶段不能直接修改项目" in str(failed.error)
        assert project.content_revision() == revision
        assert len(project.list_sequences(include_archived=True)) == 1
        result = project.committed_task_result(failed.id)
        assert result is not None
        assert result.sequence_id == project.get_project().main_sequence_id


def test_project_close_keeps_lock_until_slow_task_worker_has_joined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    root = tmp_path / "SlowClose"
    project = application.create_project(root, "SlowClose")
    handler_started = threading.Event()
    stop_observed = threading.Event()
    allow_handler_to_stop = threading.Event()

    def slow_stop(context: TaskContext) -> TaskCompletion:
        handler_started.set()
        try:
            while True:
                context.cancellation.raise_if_requested()
                time.sleep(0.005)
        except TaskStopped:
            stop_observed.set()
            if not allow_handler_to_stop.wait(timeout=5):
                raise RuntimeError("test did not release the slow task") from None
            raise

    project._tasks._execution.handlers[TaskKind.ANALYZE] = slow_stop
    task = project.start_task(AnalyzeDownloadCommand(url="test://slow-close"))
    assert handler_started.wait(timeout=5)
    worker_threads = tuple(project._tasks._execution.executor._threads)
    maintenance_thread = project._tasks._maintenance_thread

    with pytest.raises(TaskShutdownTimeout) as shutdown_error:
        project.close(timeout=0.05)

    assert shutdown_error.value.unfinished_task_ids == (task.id,)
    assert stop_observed.is_set()
    assert project.get_project().name == "SlowClose"
    observer = ProjectRepository.open(root, writable=True)
    try:
        assert observer.read_only is True
    finally:
        observer.close()

    allow_handler_to_stop.set()
    project.close(timeout=5)

    assert worker_threads
    assert all(not worker.is_alive() for worker in worker_threads)
    assert maintenance_thread is None or not maintenance_thread.is_alive()
    with application.open_project(root, writable=True) as reopened:
        assert reopened.read_only is False
        assert reopened.get_task(task.id).status == TaskStatus.PAUSED


def test_second_project_instance_is_read_only_across_every_task_write_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_application = EditorApplication()
    observer_application = EditorApplication()
    root = tmp_path / "DualInstance"
    with owner_application.create_project(root, "DualInstance"):
        with observer_application.open_project(root, writable=True) as observer:
            assert observer.read_only is True
            assert observer.list_tasks() == []

            guarded_actions = (
                lambda: observer.start_task(AnalyzeDownloadCommand(url="test://read-only")),
                lambda: observer.resume_task("missing"),
                lambda: observer.retry_task("missing"),
                lambda: observer.pause_task("missing"),
                lambda: observer.cancel_task("missing"),
                observer.pause_all_tasks,
                observer.cancel_all_tasks,
                lambda: observer.delete_task("missing"),
                observer.clear_task_history,
            )
            for action in guarded_actions:
                with pytest.raises(PermissionError, match="只读"):
                    action()

            read_only_store = TaskRepository(observer)
            with pytest.raises(PermissionError, match="read-only"):
                read_only_store.create(
                    Task(
                        project_id=observer.get_project().id,
                        command=AnalyzeDownloadCommand(url="test://repository-read-only"),
                    )
                )


def test_expired_task_recovery_stays_inside_the_project_writer(
    tmp_path: Path,
) -> None:
    application = EditorApplication()
    project = application.create_project(
        tmp_path / "OwnedRecovery",
        "OwnedRecovery",
    )
    recovered: list[bool] = []
    try:
        assert project.read_only is False
        assert project._repository.owns_project_lock is True

        def execute(context: TaskContext) -> TaskCompletion:
            recovered.append(context.recovered)
            return TaskCompletion()

        project._tasks._execution.handlers[TaskKind.ANALYZE] = execute
        repository = project._tasks.repository
        pending = repository.create(
            Task(
                project_id=project.get_project().id,
                command=AnalyzeDownloadCommand(url="test://owned-recovery"),
            )
        )
        assert repository.claim(pending.id, "crashed-process", 1) is not None
        time.sleep(0.01)

        completed = project.wait_for_task(pending.id, timeout=5)
        assert completed.status == TaskStatus.COMPLETED
        assert recovered == [True]
    finally:
        project.close()
