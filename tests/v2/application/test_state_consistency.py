from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.application.project_command_queue import ProjectCommandQueue
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskCompletion, TaskService
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.collaboration import ActorIdentity, ProjectChangeEvent
from mediaflow.domain.enums import AssetKind, SequenceKind, TaskKind, TaskStatus
from mediaflow.domain.project import Project, Sequence
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import AnalyzeDownloadCommand
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.subtitle_publication_storage import LocalSubtitlePublicationStorage
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.service.codec import decode_transport, encode_transport
from mediaflow.service.desktop_event_stream import _ProjectEventStream
from mediaflow.service.desktop_sessions import DesktopProjectOperations
from mediaflow.service.execution import ServiceExecutionPools
from mediaflow.service.remote_project import RemoteEditorProject
from mediaflow.service.remote_timeline import RemoteTimelineEditor
from mediaflow.service.request_dispatcher import ServiceRequestDispatcher


def _event(root: Path, cursor: int, actor: str = "external") -> ProjectChangeEvent:
    return ProjectChangeEvent(
        cursor=cursor,
        project_id="project",
        project_path=str(root),
        base_revision=cursor - 1,
        project_revision=cursor,
        operation="timeline.edit",
        actor=ActorIdentity(kind="human", id=actor),
        request_id=f"r{cursor}",
        undo_group_id="",
        write_set=["/sequences/main"],
        changes=[],
        operation_result={},
        created_at=0,
    )


def test_own_write_ack_keeps_earlier_external_event_and_monotonic_revision(tmp_path: Path) -> None:
    stream = _ProjectEventStream(tmp_path, "project", 0, 0, workspace_session_id="w", client_id="desktop")
    delivered = []
    stream._project_handlers[1] = delivered.append
    stream.acknowledge_project_event(cursor=2, project_revision=2)
    assert stream.project_cursor == 0
    stream.finish_write("r2", projected=True)
    for cursor, actor in [(1, "external"), (2, "desktop")]:
        stream._accept({"type": "project.changed", "payload": _event(tmp_path, cursor, actor).model_dump()})
    assert [item.cursor for item in delivered] == [1]
    assert stream.project_cursor == stream.project_revision == 2


def test_initial_subscription_keeps_project_replay_until_handler_is_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _ProjectEventStream(tmp_path, "project", 0, 0, workspace_session_id="w", client_id="desktop")
    stream._accept({"type": "project.changed", "payload": _event(tmp_path, 1).model_dump()})
    monkeypatch.setattr(stream, "_ensure_ready", lambda: None)
    delivered = []
    stream.subscribe_project(delivered.append)
    assert [item.cursor for item in delivered] == [1]


def test_subscription_replays_every_task_page_before_confirming_cursor(tmp_path: Path) -> None:
    from mediaflow.application.events import TaskEvent

    events = [TaskEvent(f"t{i}", "project", "completed", 1, {}, i) for i in range(1, 1203)]
    calls = []

    def page(after):
        calls.append(after)
        return [item for item in events if item.cursor > after][:500]

    project = SimpleNamespace(
        get_project=lambda: SimpleNamespace(id="project"),
        task_event_cursor=lambda: 1202,
        content_revision=lambda: 0,
        project_event_cursor=lambda: 0,
        list_project_events=lambda **kwargs: [],
        task_events_after=page,
    )

    @contextmanager
    def locked(*args, **kwargs):
        yield SimpleNamespace(project=project)

    registry = SimpleNamespace(
        locked_session=locked,
        task_event_document=lambda path, event: {"cursor": event.cursor},
    )
    operations = DesktopProjectOperations(registry)
    cursor = 0
    received = []
    while True:
        result = operations.project_subscription(
            tmp_path, project_cursor=0, task_cursor=cursor, task_head=1202,
        )
        assert len(result["task_events"]) <= 500
        received.extend(item["cursor"] for item in result["task_events"])
        cursor = result["next_task_cursor"]
        if not result["has_more"]:
            break
    assert calls == [0, 500, 1000]
    assert received == list(range(1, 1203))
    assert result["task_cursor"] == 1202



def test_timeline_read_retries_stale_response_instead_of_marking_it_current() -> None:
    state = TimelineState(sequence=Sequence(project_id="p", name="old", kind=SequenceKind.MAIN))
    project = SimpleNamespace(known_content_revision=1)
    replies = []

    def read(*args):
        if not replies:
            replies.append(1)
            project.known_content_revision = 2
            return state, 1
        replies.append(2)
        return state.model_copy(update={"sequence": state.sequence.model_copy(update={"name": "new"})}), 2

    project._call_revisioned = read
    timeline = RemoteTimelineEditor(project, state.sequence.id)
    assert timeline.state.sequence.name == "new"
    assert timeline.state.sequence.name == "new"
    assert replies == [1, 2]
    assert timeline._cached_revision == 2


@pytest.mark.parametrize("cached_revision,base_revision,new_revision", [(1, 2, 3), (2, 2, 4)])
def test_unrelated_write_cannot_relabel_timeline_past_unread_external_changes(
    cached_revision: int,
    base_revision: int,
    new_revision: int,
) -> None:
    state = TimelineState(sequence=Sequence(project_id="p", name="old", kind=SequenceKind.MAIN))
    project = SimpleNamespace(
        known_content_revision=cached_revision,
        _call_revisioned=lambda *args: (
            state,
            cached_revision,
        ),
    )
    timeline = RemoteTimelineEditor(project, state.sequence.id)
    assert timeline.state.sequence.name == "old"
    project.known_content_revision = new_revision
    current = state.model_copy(update={"sequence": state.sequence.model_copy(update={"name": "current"})})
    project._call_revisioned = lambda *args: (current, new_revision)
    timeline.advance_revision(new_revision, base_revision=base_revision)
    assert timeline.state.sequence.name == "current"


def test_project_cache_entry_retains_its_response_revision_when_other_reads_advance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = RemoteEditorProject(
        {
            "project": str(tmp_path),
            "project_id": "p",
            "project_revision": 2,
        },
        actor=ActorIdentity(kind="human", id="desktop"),
        workspace_session_id="workspace",
    )
    old = Project(id="p", name="old", main_sequence_id="main")
    current = old.model_copy(update={"name": "current"})
    key = json.dumps(["get_project", "", {}], separators=(",", ":"))
    # A delayed response may finish after another read advanced the shared
    # cache generation. Its individual response revision must still be checked.
    project._read_cache[key] = old, 1
    calls = []

    def respond(*args, **kwargs):
        calls.append(args)
        return {"value": encode_transport(current), "project_revision": 2}

    monkeypatch.setattr("mediaflow.service.remote_project.call_sync", respond)
    assert project.get_project().name == "current"
    assert project.get_project().name == "current"
    assert len(calls) == 1


def test_read_and_response_revision_hold_one_short_project_scope(tmp_path: Path) -> None:
    gate = ProjectCommandQueue()
    read_entered = threading.Event()
    writer_attempted = threading.Event()
    finished = threading.Event()
    with ProjectRepository.create(tmp_path / "Read", "before") as repository:
        repository._bind_mutation_gate(gate)

        def read():
            document = repository.projects.get_project()
            read_entered.set()
            assert writer_attempted.wait(2)
            return document

        project = SimpleNamespace(
            _repository=SimpleNamespace(projects=SimpleNamespace(get_project=read)),
            content_revision=repository.content_revision,
        )

        @contextmanager
        def locked(*args, **kwargs):
            with gate:
                yield SimpleNamespace(project=project)

        def write():
            assert read_entered.wait(2)
            writer_attempted.set()
            repository.projects.rename_project("after")
            finished.set()

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        result = DesktopProjectOperations(SimpleNamespace(locked_session=locked)).execute_desktop_command(
            path=repository.project_dir,
            target="project",
            sequence_id="",
            command="get_project",
            arguments_value={},
            base_revision=0,
            request_id="read",
            actor_value={"kind": "human", "id": "d"},
        )
        assert result["project_revision"] == 0
        assert decode_transport(result["value"]).name == "before"
        assert finished.wait(2)
        writer.join(2)


def test_task_admission_waiting_for_writer_does_not_hold_execution_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with ProjectRepository.create(tmp_path / "Locks", "Locks") as repository:
        gate = ProjectCommandQueue()
        repository._bind_mutation_gate(gate)
        store = TaskRepository(repository)
        service = TaskService(store, recover_expired=False)
        service.register(TaskKind.ANALYZE, lambda _: TaskCompletion())
        attempted = threading.Event()
        original_scope = store.command_scope

        @contextmanager
        def admission():
            attempted.set()
            with original_scope():
                yield

        monkeypatch.setattr(store, "command_scope", admission)
        results = []
        errors = []

        def start():
            try:
                results.append(
                    service.start(
                        project_id=repository.projects.get_project().id,
                        command=AnalyzeDownloadCommand(url="test://background"),
                    )
                )
            except Exception as error:
                errors.append(error)

        try:
            with gate:
                worker = threading.Thread(target=start, daemon=True)
                worker.start()
                assert attempted.wait(2)
                # This must not wait for the background caller's execution lock.
                assert service._execution.lock.acquire(timeout=1)
                service._execution.lock.release()
                foreground = service.start(
                    project_id=repository.projects.get_project().id,
                    command=AnalyzeDownloadCommand(url="test://foreground"),
                )
            worker.join(3)
            assert not worker.is_alive() and not errors
            assert service.wait(foreground.id, 3).status == TaskStatus.COMPLETED
            assert service.wait(results[0].id, 3).status == TaskStatus.COMPLETED
        finally:
            service.shutdown(timeout=5)


class _Cache:
    def get(self, request):
        return None

    def put(self, request, texts):
        pass


class _Translator:
    def complete_json(self, *, system, payload):
        return {"segments": [{"id": item["id"], "text": "translated"} for item in payload["segments"]]}


@pytest.mark.parametrize("changed", ["source", "target", "unrelated"])
def test_document_translation_checks_both_document_baselines(tmp_path: Path, changed: str) -> None:
    with ProjectRepository.create(tmp_path / "Translation", "Translation") as repository:
        source_path = tmp_path / "source.srt"
        source_path.write_text("source", encoding="utf-8")
        asset = repository.assets.import_external_asset(source_path, AssetKind.SUBTITLE)
        source = SubtitleDocument(project_id=asset.project_id, asset_id=asset.id, language="en")
        segment = SubtitleSegment(document_id=source.id, start_frame=0, end_frame=30, text="source")
        repository.subtitles.create_subtitle_document(source, [segment])
        service = TranslationService(
            repository, lambda _: _Translator(), _Cache(),
            SubtitlePublicationService(repository, LocalSubtitlePublicationStorage()),
        )
        provider = LlmProviderSettings(name="test", model="test", base_url="http://localhost.invalid")
        target = service.translate_document(
            source.id, target_language="zh", provider=provider, operation_id="target",
        )
        prepared = service.prepare_document_translation(
            source.id, target_language="zh", provider=provider, operation_id=target.id,
        )
        if changed == "unrelated":
            repository.projects.rename_project("unrelated rename")
            service.commit_document_translation(prepared)
            assert repository.subtitles.list_subtitle_segments(target.id)[0].text == "translated"
        else:
            document_id = source.id if changed == "source" else target.id
            original = repository.subtitles.list_subtitle_segments(document_id)[0]
            manual = original.model_copy(update={"text": "manual change"})
            repository.subtitles.save_subtitle_segments(document_id, [manual])
            with pytest.raises(RuntimeError, match="字幕已修改"):
                service.commit_document_translation(prepared)
            assert repository.subtitles.list_subtitle_segments(document_id) == [manual]


@pytest.mark.parametrize("edit_selected", [False, True])
def test_segment_translation_merges_unrelated_edits_and_rejects_target_conflicts(
    tmp_path: Path,
    edit_selected: bool,
) -> None:
    with ProjectRepository.create(tmp_path / "Translation", "Translation") as repository:
        source = tmp_path / "source.srt"
        source.write_text("source", encoding="utf-8")
        asset = repository.assets.import_external_asset(source, AssetKind.SUBTITLE)
        document = SubtitleDocument(
            project_id=asset.project_id,
            asset_id=asset.id,
            language="en",
        )
        segments = [
            SubtitleSegment(
                document_id=document.id, start_frame=i * 30, end_frame=(i + 1) * 30, text=f"original-{i}"
            )
            for i in range(2)
        ]
        repository.subtitles.create_subtitle_document(document, segments)
        publication = SubtitlePublicationService(repository, LocalSubtitlePublicationStorage())
        service = TranslationService(repository, lambda _: _Translator(), _Cache(), publication)
        prepared = service.prepare_selected_in_document_translation(
            document.id,
            [segments[0].id],
            target_language="zh",
            provider=LlmProviderSettings(name="test", model="test", base_url="http://localhost.invalid"),
        )
        addition = SubtitleSegment(document_id=document.id, start_frame=60, end_frame=90, text="new manual")
        updated = [*segments]
        index = 0 if edit_selected else 1
        updated[index] = updated[index].model_copy(update={"text": "manual"})
        repository.subtitles.save_subtitle_segments(document.id, [*updated, addition])
        if edit_selected:
            with pytest.raises(RuntimeError, match="字幕已修改"):
                service.commit_segment_translation(prepared)
            expected = ["manual", "original-1", "new manual"]
        else:
            service.commit_segment_translation(prepared)
            expected = ["translated", "manual", "new manual"]
        assert [item.text for item in repository.subtitles.list_subtitle_segments(document.id)] == expected


async def test_public_wait_requests_leave_control_and_editing_executors_available() -> None:
    release = threading.Event()
    count = 0
    lock = threading.Lock()

    def execute(request):
        nonlocal count
        if request.operation == "task.wait":
            with lock:
                count += 1
            assert release.wait(5)
        return {"result": {}}

    pools = ServiceExecutionPools()
    dispatcher = ServiceRequestDispatcher(
        SimpleNamespace(automation=SimpleNamespace(execute=execute)),
        None,
        None,
        lambda: None,
        pools,
    )

    def request(operation, arguments):
        return {
            "request": {
                "operation": operation,
                "arguments": arguments,
                "actor": {"kind": "human", "id": "test"},
                "client_id": "test",
            }
        }

    waits = [
        asyncio.create_task(
            dispatcher.dispatch(
                "operation.execute",
                request("task.wait", {"task_id": str(i), "timeout": 4}),
            )
        )
        for i in range(8)
    ]
    try:
        for _ in range(200):
            if count == 8:
                break
            await asyncio.sleep(0.01)
        assert count == 8
        for operation, arguments in [("task.cancel", {"task_id": "1"}), ("project.inspect", {})]:
            result = await asyncio.wait_for(
                dispatcher.dispatch(
                    "operation.execute",
                    request(
                        operation,
                        arguments,
                    ),
                ),
                timeout=1,
            )
            assert result == {"result": {}}
    finally:
        release.set()
        await asyncio.gather(*waits)
        pools.close()
