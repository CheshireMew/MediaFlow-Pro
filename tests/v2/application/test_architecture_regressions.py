from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.application.project_command_queue import ProjectCommandQueue
from mediaflow.application.task_execution_types import TaskLeaseLost
from mediaflow.application.task_service import TaskCompletion, TaskService
from mediaflow.domain.collaboration import ActorIdentity, ProjectChangeEvent
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.storage_names import python_io_path, utf16_units
from mediaflow.domain.task_commands import (
    DiagnosticsBundleCommand,
    ExportFcpxmlCommand,
    ExportSequenceCommand,
    ImportAssetCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.infrastructure.output_reservation import (
    output_set_transaction,
    reserve_output,
    reserve_python_output,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.storage_inventory import registered_roots_inventory
from mediaflow.infrastructure.task_output_journal import TaskOutputCoordinator
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.service.codec import decode_transport, encode_transport
from mediaflow.service.desktop_event_stream import _ProjectEventStream
from mediaflow.service.execution import ServiceBusyError, _BoundedExecutionPool, _PoolConfiguration
from mediaflow.service.remote_project import RemoteEditorProject
from mediaflow.service.server import EditorServiceServer
from mediaflow.service.session_registry import ProjectSession, ProjectSessionRegistry
from mediaflow.service.workspaces import WorkspaceRegistry


def test_native_preview_cancellation_interrupts_cache_admission_and_rejects_late_work(tmp_path, monkeypatch):
    import mediaflow.infrastructure.storage_inventory as inventory
    import mediaflow.project_presentation as presentation_module
    from mediaflow.domain.timeline import TimelineState

    paths = RuntimePaths(tmp_path / "runtime", Path("ffmpeg"), Path("ffprobe"))
    presentation = presentation_module.ProjectPresentationService(paths)
    started, release = threading.Event(), threading.Event()
    real_inventory = inventory.registered_roots_inventory

    def blocked_inventory(roots, *, check_cancelled=None):
        assert check_cancelled is not None
        started.set()
        while not release.wait(0.01):
            check_cancelled()
        return real_inventory(roots, check_cancelled=check_cancelled)

    monkeypatch.setattr(inventory, "registered_roots_inventory", blocked_inventory)
    monkeypatch.setattr(
        presentation_module.TimelineCompiler,
        "compile",
        lambda *_a, **_kw: SimpleNamespace(
            xml="<mlt/>",
        ),
    )
    with ProjectRepository.create(tmp_path / "p", "Cancel preview") as repository:
        state = TimelineState(
            sequence=repository.sequences.get_sequence(
                repository.projects.get_project().main_sequence_id,
            )
        )
        arguments = {
            "use_proxies": False,
            "prefer_sdr_preview_proxy": False,
            "request_owner": "desktop",
            "request_generation": 1,
        }
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                presentation.write_preview_snapshot,
                repository.project_dir,
                state,
                **arguments,
            )
            try:
                assert started.wait(3)
                presentation.cancel_preview_snapshot_requests(
                    repository.project_dir,
                    request_owner="other",
                    request_generation=2,
                )
                assert not pending.done()
                presentation.cancel_preview_snapshot_requests(
                    repository.project_dir,
                    request_owner="desktop",
                    request_generation=2,
                )
                with pytest.raises(CancelledError):
                    pending.result(timeout=2)
                with pytest.raises(CancelledError):
                    presentation.write_preview_snapshot(repository.project_dir, state, **arguments)
                assert not list(paths.project_cache_dir(repository.project_dir).glob("mlt/*.mlt"))
            finally:
                release.set()


def test_task_creation_reads_its_transaction_and_publishes_only_after_commit(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "project", "Transaction") as project:
        store = TaskRepository(project)
        service = TaskService(store, recover_expired=False)
        events = []
        service.events.subscribe(events.append, include_snapshot=False)
        try:
            candidate = Task(
                project_id=project.projects.get_project().id,
                command=ImportAssetCommand(source_path="not-executed"),
            )
            with pytest.raises(RuntimeError, match="rollback"):
                with project.transaction():
                    store.create(candidate)
                    service._publish(candidate, "created")
                    assert store.get(candidate.id) == candidate
                    assert events == []
                    raise RuntimeError("rollback")
            assert store.list() == []
            assert events == []
            with project.transaction():
                store.create(candidate)
                service._publish(candidate, "created")
                assert events == []
            assert [event.task_id for event in events] == [candidate.id]
            with project.transaction():
                service.cancel(candidate.id)
                assert store.get(candidate.id).status == TaskStatus.CANCELLED
            assert events[-1].payload["status"] == "cancelled"
        finally:
            service.shutdown(timeout=2)


@pytest.mark.parametrize("control", ["cancel", "pause", "cancel_all"])
def test_rolled_back_task_control_does_not_interrupt_the_running_workers(
    tmp_path: Path,
    control: str,
) -> None:
    with ProjectRepository.create(tmp_path / "p", "Atomic task control") as project:
        store = TaskRepository(project)
        service = TaskService(store, max_workers=2, recover_expired=False)
        examine, finish = threading.Event(), threading.Event()
        started = [threading.Event(), threading.Event()]
        checked = [threading.Event(), threading.Event()]
        interrupted = []
        allocation_lock = threading.Lock()
        workers = []

        def handler(context):
            with allocation_lock:
                index = len(workers)
                workers.append(context.task.id)
            started[index].set()
            assert examine.wait(3)
            try:
                context.cancellation.raise_if_requested()
            except BaseException:
                interrupted.append(context.task.id)
                raise
            finally:
                checked[index].set()
            assert finish.wait(3)
            return TaskCompletion()

        service.register(TaskKind.DIAGNOSTICS, handler)
        try:
            tasks = [
                service.start(
                    project_id=project.projects.get_project().id,
                    command=DiagnosticsBundleCommand(output_path=str(tmp_path / f"output-{index}.zip")),
                )
                for index in range(2)
            ]
            assert all(event.wait(3) for event in started)
            with pytest.raises(RuntimeError, match="rollback"):
                with project.transaction():
                    if control == "cancel_all":
                        service.cancel_all()
                    else:
                        for task in tasks:
                            getattr(service, control)(task.id)
                    examine.set()
                    assert all(event.wait(3) for event in checked)
                    assert interrupted == []
                    raise RuntimeError("rollback")
            assert all(store.get(task.id).stop_request is None for task in tasks)
            finish.set()
            assert all(service.wait(task.id, timeout=3).status == TaskStatus.COMPLETED for task in tasks)
        finally:
            examine.set()
            finish.set()
            service.shutdown(timeout=3)


def test_cancel_before_settlement_does_not_apply_prepared_project_changes(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "project", "Cancel") as project:
        store = TaskRepository(project)
        prepared, release = threading.Event(), threading.Event()
        applied = []

        def settle(_task, persist, changes):
            with project.transaction():
                task = persist()
                for change in changes:
                    change()
                return task

        service = TaskService(store, recover_expired=False, settlement_committer=settle)

        def handler(context):
            context.defer_project_change(lambda: applied.append("must not commit"))
            prepared.set()
            assert release.wait(3)
            return TaskCompletion()

        service.register(TaskKind.IMPORT, handler)
        try:
            task = service.start(
                project_id=project.projects.get_project().id,
                command=ImportAssetCommand(source_path="not-executed"),
            )
            assert prepared.wait(3)
            service.cancel(task.id)
            release.set()
            assert service.wait(task.id, timeout=3).status == TaskStatus.CANCELLED
            assert applied == []
        finally:
            release.set()
            service.shutdown(timeout=3)


def test_service_close_allows_workers_to_acquire_the_project_gate(tmp_path: Path) -> None:
    project = ProjectRepository.create(tmp_path / "project", "Close")
    gate = ProjectCommandQueue()
    project._bind_mutation_gate(gate)
    service = TaskService(TaskRepository(project), recover_expired=False)
    started = threading.Event()

    def handler(context):
        started.set()
        while True:
            context.cancellation.raise_if_requested()
            threading.Event().wait(0.01)

    service.register(TaskKind.IMPORT, handler)
    task = service.start(
        project_id=project.projects.get_project().id, command=ImportAssetCommand(source_path="not-executed")
    )
    assert started.wait(3)
    adapter = SimpleNamespace(
        project_dir=project.project_dir,
        close=lambda **_kwargs: service.shutdown(timeout=2),
        unsubscribe_task_events=lambda _token: None,
    )
    registry = ProjectSessionRegistry(
        SimpleNamespace(release_preview_owner=lambda *_args: None),
        SimpleNamespace(),
    )
    session = ProjectSession(adapter, threading.Condition(), gate, 0)
    registry._sessions[project.project_dir] = session
    try:
        registry.close()
        assert service.get(task.id).status == TaskStatus.PAUSED
        assert not registry.has_session(project.project_dir)
    finally:
        service.shutdown(timeout=3)
        project.close()


async def test_cancelled_request_keeps_capacity_until_the_worker_really_finishes() -> None:
    pool = _BoundedExecutionPool("regression", _PoolConfiguration(1, 1, 0.03))
    started, release = threading.Event(), threading.Event()

    def handler():
        started.set()
        assert release.wait(3)

    first = asyncio.create_task(pool.run(handler))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(ServiceBusyError):
            await pool.run(lambda: None)
    finally:
        release.set()
        await asyncio.to_thread(pool.close)


@pytest.mark.parametrize(
    "value",
    [
        {"$mediaflow_type": "path", "value": "ordinary data"},
        {"$mediaflow_type": "unknown", "schema": "", "value": [{"$mediaflow_type": "tuple"}]},
        {"nested": {"$mediaflow_type": "mapping", "value": [["x", 3]]}},
    ],
)
def test_transport_preserves_user_json_that_contains_protocol_field_names(value) -> None:
    assert decode_transport(encode_transport(value)) == value


def test_workspaces_can_reattach_after_the_service_loses_in_memory_state() -> None:
    old = WorkspaceRegistry().attach(client_id="desktop")
    restarted = WorkspaceRegistry()
    restored = restarted.attach(client_id="desktop", workspace_session_id=old["workspace_session_id"])
    assert restored["workspace_session_id"] == old["workspace_session_id"]
    assert restarted.connect(restored["workspace_session_id"], "desktop")["connected"]


def test_draft_baseline_survives_focus_changes_until_successful_commit(monkeypatch) -> None:
    import mediaflow.service.remote_project as remote_module
    from mediaflow.desktop.coordinators.project_lifecycle import ProjectLifecycle
    from mediaflow.service.client import EditorServiceUnavailable

    project = RemoteEditorProject(
        {"project": "E:/unused", "project_id": "p", "project_revision": 1, "project_event_cursor": 0},
        actor=ActorIdentity(kind="human", id="desktop"),
        workspace_session_id="workspace",
    )
    path = "/subtitles/documents/doc/segments/seg/text"
    project.begin_draft(path)
    project._events.project_revision = 2
    lifecycle = SimpleNamespace(
        _context=SimpleNamespace(state=SimpleNamespace(binding=SimpleNamespace(current=project))),
        _active_draft_path=path,
        _flush_deferred_events=lambda: None,
    )
    ProjectLifecycle._on_focus_object_changed(lifecycle, SimpleNamespace(property=lambda _name: None))
    sent = []

    def unavailable(_method, arguments, **_kwargs):
        sent.append(arguments)
        raise EditorServiceUnavailable("lost response")

    monkeypatch.setattr(remote_module, "call_sync", unavailable)
    for _ in range(2):
        with pytest.raises(EditorServiceUnavailable):
            project.update_subtitle_segment("doc", "seg", start_frame=0, end_frame=30, text="draft")
    assert {request["base_revision"] for request in sent} == {1}
    assert len({request["request_id"] for request in sent}) == 1


@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("python_only", [False, True])
def test_export_publication_and_task_commit_restore_the_correct_file(
    tmp_path: Path,
    commit: bool,
    python_only: bool,
) -> None:
    with ProjectRepository.create(tmp_path / "p", "Output") as repository:
        output = tmp_path / "delivery.mp4"
        if python_only:
            output = tmp_path / ("f" * (264 - utf16_units(str(tmp_path)) - 8) + ".fcpxml")
            assert utf16_units(str(output)) == 264
        python_io_path(output).write_bytes(b"previous user output")
        command_type = ExportFcpxmlCommand if python_only else ExportSequenceCommand
        reserve = reserve_python_output if python_only else reserve_output
        store = TaskRepository(repository)
        task = store.create(
            Task(
                project_id=repository.projects.get_project().id,
                command=command_type(
                    sequence_id=repository.projects.get_project().main_sequence_id,
                    output_path=str(output),
                    overwrite=True,
                ),
            )
        )
        claimed = store.claim(task.id, "owner", 10_000)
        assert claimed is not None
        task = claimed[0]
        coordinator = TaskOutputCoordinator(repository)
        with coordinator.preparation(task):
            with output_set_transaction((output,), overwrite=True, python_only=python_only) as publication:
                publication.temporary_path(output, "export").write_bytes(b"new export")
                publication.commit()
        with pytest.raises(RuntimeError, match="already writing"):
            with reserve(output):
                pass
        scope = nullcontext() if commit else pytest.raises(RuntimeError, match="SQL failure")
        with scope, repository.transaction():
            completed = store.update_owned(
                task.model_copy(update={"status": TaskStatus.COMPLETED}),
                "owner",
                event_type="completed",
                release_owner=True,
            )
            assert completed is not None
            coordinator.enlist(completed)
            if not commit:
                raise RuntimeError("SQL failure")
        assert python_io_path(output).read_bytes() == (b"new export" if commit else b"previous user output")
        assert store.get(task.id).status == (TaskStatus.COMPLETED if commit else TaskStatus.RUNNING)
        with reserve(output):
            pass
        assert list(coordinator.root.glob("*.json")) == []


@pytest.mark.parametrize("user_replaced_output", [False, True])
@pytest.mark.parametrize("python_only", [False, True])
def test_export_recovery_withdraws_only_its_own_uncommitted_output(
    tmp_path: Path,
    user_replaced_output: bool,
    python_only: bool,
) -> None:
    with ProjectRepository.create(tmp_path / "p", "Recovery") as repository:
        output = tmp_path / "delivery.mp4"
        if python_only:
            output = tmp_path / ("f" * (264 - utf16_units(str(tmp_path)) - 8) + ".fcpxml")
            assert utf16_units(str(output)) == 264
        command_type = ExportFcpxmlCommand if python_only else ExportSequenceCommand
        store = TaskRepository(repository)
        task = store.create(
            Task(
                project_id=repository.projects.get_project().id,
                command=command_type(sequence_id="main", output_path=str(output)),
            )
        )
        claim = store.claim(task.id, "dead-owner", 1000)
        assert claim is not None
        coordinator = TaskOutputCoordinator(repository)
        with coordinator.preparation(claim[0]):
            with output_set_transaction((output,), overwrite=False, python_only=python_only) as publication:
                publication.temporary_path(output, "export").write_bytes(b"unfinished receipt")
                publication.commit()
        coordinator._journals[task.id].reservations.close()
        if user_replaced_output:
            python_io_path(output).write_bytes(b"user replacement after crash")
        TaskOutputCoordinator(repository)
        if user_replaced_output:
            assert python_io_path(output).read_bytes() == b"user replacement after crash"
            assert store.get(task.id).status == TaskStatus.FAILED
            with pytest.raises(FileExistsError):
                with output_set_transaction((output,), overwrite=False, python_only=python_only):
                    pass
            TaskOutputCoordinator(repository)
            assert python_io_path(output).read_bytes() == b"user replacement after crash"
            return
        assert not python_io_path(output).exists()
        with output_set_transaction((output,), overwrite=False, python_only=python_only) as retry:
            retry.temporary_path(output, "retry").write_bytes(b"retried without overwrite")
            retry.commit()
        assert python_io_path(output).read_bytes() == b"retried without overwrite"


def test_lost_task_lease_releases_prepared_outputs_without_committing(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "p", "Lease loss") as repository:
        output = tmp_path / "delivery.mp4"
        output.write_bytes(b"old output")
        coordinator = TaskOutputCoordinator(repository)
        discarded = threading.Event()

        def discard(task):
            coordinator.discard(task.id)
            discarded.set()

        def lose_lease(_task, _persist, _changes):
            raise TaskLeaseLost("execution lease lost before commit")

        service = TaskService(
            TaskRepository(repository),
            recover_expired=False,
            preparation_scope=coordinator.preparation,
            discard_preparation=discard,
            settlement_committer=lose_lease,
        )

        def handler(context):
            with output_set_transaction((output,), overwrite=True) as publication:
                publication.temporary_path(output, "prepared").write_bytes(b"new output")
                publication.commit()
            return TaskCompletion()

        service.register(TaskKind.EXPORT, handler)
        try:
            service.start(
                project_id=repository.projects.get_project().id,
                command=ExportSequenceCommand(
                    sequence_id=repository.projects.get_project().main_sequence_id,
                    output_path=str(output),
                    overwrite=True,
                ),
            )
            assert discarded.wait(3)
            assert output.read_bytes() == b"old output"
            with reserve_output(output):
                pass
        finally:
            service.shutdown(timeout=3)


def test_idle_reaper_waits_for_all_connections_and_active_calls(tmp_path: Path) -> None:
    closed, released = [], []
    project = SimpleNamespace(
        project_dir=tmp_path.resolve(),
        list_tasks=lambda: [],
        close=lambda **kwargs: closed.append(True),
        unsubscribe_task_events=lambda _token: None,
    )
    registry = ProjectSessionRegistry(
        SimpleNamespace(release_preview_owner=lambda root, owner: released.append((root, owner))),
        SimpleNamespace(),
    )
    root = tmp_path.resolve()
    session = ProjectSession(project, threading.Condition(), ProjectCommandQueue(), 0, last_access=0)
    registry._sessions[root] = session
    registry.desktop_connected(root, "desktop")
    registry.desktop_connected(root, "desktop")
    registry.desktop_disconnected(root, "desktop")
    future = time.monotonic() + 120
    assert registry.reap_idle(now=future) == 0
    registry.desktop_disconnected(root, "desktop")
    session.active_calls = 1
    assert registry.reap_idle(now=future) == 0
    session.active_calls = 0
    assert registry.reap_idle(now=future) == 1
    assert closed == [True] and released == [(root, "desktop"), (root, None)]
    assert not registry.has_session(root)


def test_global_cache_inventory_observes_cancellation_between_files(tmp_path: Path, monkeypatch) -> None:
    from mediaflow.infrastructure import storage_inventory

    root = tmp_path / "cache"
    root.mkdir()
    for index in range(150):
        (root / f"{index}.bin").write_bytes(b"small cache entry")
    original = storage_inventory.file_allocation
    measured = []

    def allocation(path, *args, **kwargs):
        measured.append(path)
        return original(path, *args, **kwargs)

    def check_cancelled():
        if measured:
            raise CancelledError("request cancelled during inventory")

    monkeypatch.setattr(storage_inventory, "file_allocation", allocation)
    with pytest.raises(CancelledError):
        registered_roots_inventory({"cache": root}, check_cancelled=check_cancelled)
    assert 0 < len(measured) < 150


def test_runtime_snapshot_excludes_journals_and_event_reads_are_paged(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "p", "History") as repository:
        with repository.transaction():
            for index in range(1005):
                repository.events.append(
                    base_revision=index,
                    project_revision=index + 1,
                    operation="fixture.edit",
                    actor=ActorIdentity(kind="agent", id="a"),
                    request_id=f"request-{index}",
                    undo_group_id="",
                    write_set=["/project/name"],
                    changes=[],
                    operation_result={},
                )
        assert len(repository.events.list_events()) == 500
        assert len(list(repository.events.iter_after_revision(0))) == 1005
        with repository.read_snapshot() as snapshot:
            assert snapshot.projects.get_project() == repository.projects.get_project()
            assert snapshot.events.latest_cursor() == 0
        with repository.read_snapshot(include_history=True) as snapshot:
            assert snapshot.events.latest_cursor() == 1005


def test_lost_own_write_response_delivers_the_committed_event(tmp_path: Path) -> None:
    stream = _ProjectEventStream(tmp_path, "p", 0, 0, workspace_session_id="w", client_id="desktop")
    received = []
    stream._project_handlers[1] = received.append
    event = ProjectChangeEvent(
        cursor=1,
        project_id="p",
        project_path=str(tmp_path),
        base_revision=0,
        project_revision=1,
        operation="edit",
        actor=ActorIdentity(kind="human", id="desktop"),
        request_id="lost",
        undo_group_id="",
        write_set=["/project/name"],
        changes=[],
        operation_result={},
        created_at=0,
    )
    stream.begin_write("lost")
    stream._accept({"type": "project.changed", "payload": event.model_dump(mode="json")})
    assert received == []
    stream.finish_write("lost", projected=False)
    assert received == [event]


def test_preview_graph_cache_bounds_recent_versions_and_protects_other_owners(tmp_path: Path) -> None:
    from mediaflow.project_presentation import ProjectPresentationService

    paths = RuntimePaths(tmp_path / "runtime", Path("ffmpeg"), Path("ffprobe"))
    project = tmp_path / "p"
    project.mkdir()
    presentation = ProjectPresentationService(paths)
    cache = presentation._preview_cache(project)
    protected = cache.publish("<mlt>other preview</mlt>", namespace="pv-main", owner="other")
    source = cache.publish(
        "<mlt>source preview</mlt>", namespace="source-asset", owner="desktop", surface="source"
    )
    for index in range(50):
        current = cache.publish(f"<mlt>{index}</mlt>", namespace="pv-main", owner="desktop")
        assert protected.is_file()
        assert current.is_file()
        assert source.is_file()
    assert len(list(current.parent.glob("*.mlt"))) <= 16
    cache.release("desktop")
    for index in range(17):
        cache.publish(
            f"<mlt>new source {index}</mlt>", namespace="source-asset", owner="replacement", surface="source"
        )
    assert not source.exists() and protected.is_file()
    presentation.release_preview_owner(project, None)
    assert project.resolve() not in presentation._preview_caches


def test_rpc_failure_returns_the_identifier_logged_with_original_traceback(caplog) -> None:
    try:
        raise LookupError("backend origin")
    except LookupError as error:
        response = EditorServiceServer._unexpected_rpc_error(1, "regression", -32603, error)
    error_id = response["error"]["data"]["error_id"]
    assert error_id in response["error"]["message"]
    assert error_id in caplog.text and "backend origin" in caplog.text
    assert caplog.records[-1].exc_info[0] is LookupError
