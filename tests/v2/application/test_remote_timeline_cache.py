from __future__ import annotations

from mediaflow.domain.enums import ClipMediaKind, SequenceKind, TrackKind
from mediaflow.domain.project import Sequence
from mediaflow.domain.timeline import Clip, TimelineState, Track
from mediaflow.domain.web_state import WebClipState
from mediaflow.service.remote_project import project_write_affects_timeline
from mediaflow.service.remote_timeline_cache import project_timeline_write


def _timeline_state() -> TimelineState:
    sequence = Sequence(project_id="project", name="Main", kind=SequenceKind.MAIN)
    track = Track(
        id="video",
        sequence_id=sequence.id,
        name="Video",
        kind=TrackKind.VIDEO,
        position=0,
    )
    source = Clip(
        id="source",
        track_id=track.id,
        asset_id="asset",
        timeline_start=0,
        source_in=0,
        duration=20,
        media_kind=ClipMediaKind.VIDEO_ONLY,
    )
    return TimelineState(
        sequence=sequence,
        tracks=[track],
        clips=[source],
        web_states={source.id: WebClipState(clip_id=source.id, revision=4)},
    )


def test_remote_timeline_cache_projects_copy_split_and_non_ripple_delete() -> None:
    state = _timeline_state()
    copied = state.clips[0].model_copy(
        update={"id": "copied", "timeline_start": 20}
    )

    state = project_timeline_write(
        state,
        "copy_clip",
        copied,
        args=("source",),
    )
    assert state is not None
    assert [item.id for item in state.clips] == ["source", "copied"]
    assert state.web_states["copied"].revision == 0

    left = copied.model_copy(update={"duration": 10})
    right = copied.model_copy(
        update={"id": "right", "timeline_start": 30, "source_in": 10, "duration": 10}
    )
    state = project_timeline_write(
        state,
        "split_clip",
        (left, right),
        args=("copied", 30),
    )
    assert state is not None
    assert [item.id for item in state.clips] == ["source", "copied", "right"]
    assert state.web_states["right"].revision == 0

    state = project_timeline_write(
        state,
        "delete_clips",
        None,
        args=(["right"],),
        kwargs={"ripple": False},
    )
    assert state is not None
    assert [item.id for item in state.clips] == ["source", "copied"]
    assert "right" not in state.web_states


def test_remote_timeline_cache_refuses_to_guess_ripple_delete() -> None:
    state = _timeline_state()

    assert (
        project_timeline_write(
            state,
            "delete_clips",
            None,
            args=(["source"],),
            kwargs={"ripple": True},
        )
        is None
    )


def test_project_write_only_invalidates_the_timeline_it_can_change() -> None:
    assert not project_write_affects_timeline(
        ["/subtitles/documents/document/segments/segment"],
        "main",
    )
    assert not project_write_affects_timeline(["/sequences/short/clips/clip"], "main")
    assert project_write_affects_timeline(["/sequences/main/clips/clip"], "main")
    assert project_write_affects_timeline(["/web/clips/clip"], "main")
    assert project_write_affects_timeline(["/project"], "main")
