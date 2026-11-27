from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorApplication
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.enums import AssetKind, SequenceKind, TrackKind
from mediaflow.domain.project import MediaMetadata, Sequence
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.timeline_history import TimelineStateChange
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.service.codec import decode_transport, encode_transport
from mediaflow.service.desktop_sessions import DesktopProjectOperations
from mediaflow.service.events import EventHub
from mediaflow.service.remote_project import RemoteEditorProject, project_write_affects_timeline
from mediaflow.service.remote_timeline import RemoteTimelineEditor
from mediaflow.service.remote_timeline_cache import apply_timeline_change
from mediaflow.service.session_registry import ProjectSessionRegistry


def test_write_patch_preserves_a_committed_snapshot_read_while_the_response_was_in_flight() -> None:
    before = TimelineState(sequence=Sequence(project_id="project", name="before", kind=SequenceKind.MAIN))
    after = before.model_copy(update={"sequence": before.sequence.model_copy(update={"name": "after"})})
    reads = []

    def read(*_args):
        reads.append(True)
        return after, 2

    project = SimpleNamespace(known_content_revision=2, _call_revisioned=read)
    timeline = RemoteTimelineEditor(project, before.sequence.id)
    assert timeline.state is after
    timeline.apply_committed_change(TimelineStateChange.between(before, after, base_revision=1), revision=2)
    assert timeline.state is after
    assert reads == [True]
    timeline.apply_committed_change(None, revision=2)
    assert timeline._cached_state is None


@pytest.mark.asyncio
async def test_remote_commands_apply_the_actual_committed_patch_and_replay_invalidates_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"authoritative transport regression source")
    project_path = tmp_path / "Project"
    with ProjectRepository.create(project_path, "Transport") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": MediaMetadata(duration_frames=300, has_video=True),
                }
            )
        )
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        first = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=100,
        )
        second = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=100,
            source_in=100,
            duration=50,
        )
        editor.create_compound_clip([first.id, second.id])
        editor.set_sequence_in_out(12, 140)
        sequence_id = editor.sequence_id
    registry = ProjectSessionRegistry(EditorApplication(), EventHub(asyncio.get_running_loop()))
    operations = DesktopProjectOperations(registry)
    actor = ActorIdentity(kind="human", id="patch-regression")
    requests = []

    def rpc(method, params, **_kwargs):
        assert method == "desktop.project.call"
        arguments = {
            "path": project_path,
            "target": params["target"],
            "sequence_id": params["sequence_id"],
            "command": params["command"],
            "arguments_value": params["arguments"],
            "base_revision": params["base_revision"],
            "request_id": params["request_id"],
            "actor_value": params["actor"],
        }
        requests.append(arguments)
        return operations.execute_desktop_command(**arguments)

    monkeypatch.setattr("mediaflow.service.remote_project.call_sync", rpc)
    try:
        descriptor = registry.open_desktop_project(project_path, actor.id)
        remote = RemoteEditorProject(descriptor, actor=actor, workspace_session_id="patch-workspace")
        timeline = remote.timeline(sequence_id)
        assert timeline.state.sequence.in_out.out_frame == 140
        timeline.trim_clip(second.id, timeline_start=100, source_in=100, duration=20)
        with registry.locked_session(project_path) as session:
            committed = session.project.timeline(sequence_id).state
        assert timeline.state == committed
        assert timeline.state.sequence.in_out.out_frame == 120
        timeline.split_clip(first.id, 30)
        with registry.locked_session(project_path) as session:
            committed = session.project.timeline(sequence_id).state
        assert timeline.state == committed
        assert timeline.state.compounds == []
        # The two writes used patches and required no additional state read.
        assert [item["command"] for item in requests] == ["state", "trim_clip", "split_clip"]
        replay = operations.execute_desktop_command(**requests[-1])
        assert replay["timeline_change"] is None
        timeline.apply_committed_change(None, revision=replay["project_revision"])
        assert timeline._cached_state is None
        assert timeline.state == committed
    finally:
        registry.close()


@pytest.mark.parametrize("command", ["trim", "delete", "split", "copy", "move", "track"])
def test_committed_timeline_patch_matches_the_real_database(tmp_path: Path, command: str) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"timeline patch source; no decoder required")
    with ProjectRepository.create(tmp_path / "Project", "Patch") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": MediaMetadata(duration_frames=300, has_video=True),
                }
            )
        )
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        first = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=100,
        )
        second = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=100,
            source_in=100,
            duration=50,
        )
        editor.create_compound_clip([first.id, second.id])
        editor.set_sequence_in_out(12, 140)
        before = editor.state
        if command == "trim":
            editor.trim_clip(second.id, timeline_start=100, source_in=100, duration=20)
        elif command == "delete":
            editor.delete_clip(second.id)
        elif command == "split":
            editor.split_clip(first.id, 30)
        elif command == "copy":
            editor.copy_clip(first.id, timeline_start=160)
        elif command == "move":
            editor.move_clip(second.id, timeline_start=160)
        else:
            editor.add_track(TrackKind.AUDIO)
        after = repository.timeline.load_timeline(editor.sequence_id)
        change = TimelineStateChange.between(before, after, base_revision=7)
        transported = decode_transport(encode_transport(change))
        assert isinstance(transported, TimelineStateChange)
        assert apply_timeline_change(before, transported) == after
        assert change.before.clips != before.clips or change.after.clips != after.clips


def test_project_write_only_invalidates_the_timeline_it_can_change() -> None:
    assert not project_write_affects_timeline(
        ["/subtitles/documents/document/segments/segment"],
        "main",
    )
    assert not project_write_affects_timeline(["/sequences/short/clips/clip"], "main")
    assert project_write_affects_timeline(["/sequences/main/clips/clip"], "main")
    assert project_write_affects_timeline(["/web/clips/clip"], "main")
    assert project_write_affects_timeline(["/project"], "main")


def test_remote_project_invalidates_revision_cached_reads_before_projecting_task_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = object.__new__(RemoteEditorProject)
    invalidations: list[bool] = []
    monkeypatch.setattr(
        project,
        "_invalidate_read_cache",
        lambda *, invalidate_timelines=True: invalidations.append(invalidate_timelines),
    )

    project.invalidate_task_result_cache()

    assert invalidations == [False]
