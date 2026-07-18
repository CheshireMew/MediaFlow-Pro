from pathlib import Path

import pytest

from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_diff import FrameInterval, RippleAdjustment, TimelineDiff
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AssetKind, ColorMode, TrackKind, TransitionKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.timeline import ClipAudio
from mediaflow.infrastructure.project_repository import ProjectRepository


@pytest.fixture
def editor_fixture(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-producer-output")
    repository = ProjectRepository.create(tmp_path / "Project", "Project")
    asset = repository.import_external_asset(source, AssetKind.VIDEO)
    project = repository.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
    try:
        yield repository, editor, asset, video_track
    finally:
        repository.close()


def test_split_undo_redo_round_trip_is_persisted(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=10,
        duration=100,
    )
    left, right = editor.split_clip(clip.id, 40)
    assert (left.duration, right.timeline_start, right.source_in) == (40, 40, 50)
    assert len(repository.load_timeline(editor.sequence_id).clips) == 2

    editor.undo()
    persisted = repository.load_timeline(editor.sequence_id)
    assert [(item.timeline_start, item.duration) for item in persisted.clips] == [(0, 100)]

    editor.redo()
    persisted = repository.load_timeline(editor.sequence_id)
    assert [(item.timeline_start, item.duration) for item in persisted.clips] == [(0, 40), (40, 60)]


def test_sequence_in_out_is_persisted_undoable_and_does_not_trim_clip(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=10,
        duration=100,
    )
    editor.set_sequence_in_out(12, 88)
    state = repository.load_timeline(editor.sequence_id)
    assert (state.sequence.in_out.in_frame, state.sequence.in_out.out_frame) == (12, 88)
    assert state.clips[0] == clip

    editor.undo()
    assert repository.load_timeline(editor.sequence_id).sequence.in_out is None
    assert repository.load_timeline(editor.sequence_id).clips[0] == clip
    editor.redo()
    assert repository.load_timeline(editor.sequence_id).sequence.in_out.out_frame == 88

    editor.trim_clip(clip.id, timeline_start=0, source_in=10, duration=60)
    assert repository.load_timeline(editor.sequence_id).sequence.in_out.out_frame == 60
    editor.clear_sequence_in_out()
    assert repository.load_timeline(editor.sequence_id).sequence.in_out is None


def test_linked_subtitles_follow_move_trim_speed_split_and_undo(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=10,
        source_in=20,
        duration=100,
    )
    subtitle_track = next(track for track in editor.state.tracks if track.kind == TrackKind.SUBTITLE)
    document = SubtitleDocument(
        project_id=repository.get_project().id,
        asset_id=asset.id,
        language="zh-CN",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=30,
        end_frame=50,
        text="跟随片段",
    )
    repository.create_subtitle_document(document, [segment])
    repository.place_subtitle_document(document.id, subtitle_track.id, follow_clips=True)
    placements = repository.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame, item.clip_id) for item in placements] == [(20, 40, clip.id)]

    editor.trim_clip(clip.id, timeline_start=20, source_in=30, duration=80)
    editor.move_clip(clip.id, timeline_start=40)
    editor.set_clip_speed(
        clip.id,
        speed_numerator=2,
        speed_denominator=1,
        pitch_compensation=True,
    )
    placements = repository.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame) for item in placements] == [(40, 50)]

    left, right = editor.split_clip(clip.id, 45)
    placements = repository.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame, item.clip_id) for item in placements] == [
        (40, 45, left.id),
        (45, 50, right.id),
    ]

    editor.undo()
    placements = repository.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame, item.clip_id) for item in placements] == [(40, 50, clip.id)]
    editor.redo()
    assert len(repository.list_subtitle_placements(subtitle_track.id)) == 2


def test_move_rejects_same_track_overlap(editor_fixture) -> None:
    _, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=50,
    )
    second = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=50,
        source_in=50,
        duration=50,
    )
    with pytest.raises(ValueError, match="overlap"):
        editor.move_clip(second.id, timeline_start=25)
    assert editor.state.clips[0].id == first.id


def test_drag_overlap_becomes_one_undoable_transition_command(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=40,
    )
    second = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=60,
        source_in=40,
        duration=40,
    )

    moved = editor.move_clip(
        second.id,
        timeline_start=30,
        transition_from_overlap=True,
    )
    state = repository.load_timeline(editor.sequence_id)
    assert moved.timeline_start == first.timeline_end
    assert len(state.transitions) == 1
    assert state.transitions[0].left_clip_id == first.id
    assert state.transitions[0].right_clip_id == second.id
    assert state.transitions[0].duration == 10

    editor.undo()
    restored = repository.load_timeline(editor.sequence_id)
    assert next(item for item in restored.clips if item.id == second.id).timeline_start == 60
    assert restored.transitions == []


def test_speed_sign_switch_preserves_the_current_source_span(editor_fixture) -> None:
    _, editor, asset, video_track = editor_fixture
    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=10,
        duration=30,
    )

    reversed_clip = editor.set_clip_speed(
        clip.id,
        speed_numerator=-1,
        speed_denominator=1,
        pitch_compensation=True,
    )
    assert reversed_clip.source_in == 39

    forward_clip = editor.set_clip_speed(
        clip.id,
        speed_numerator=1,
        speed_denominator=1,
        pitch_compensation=True,
    )
    assert forward_clip.source_in == 10


def test_transition_requires_adjacent_clips(editor_fixture) -> None:
    _, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=50,
    )
    second = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=50,
        source_in=50,
        duration=50,
    )
    transition = editor.create_transition(first.id, second.id, TransitionKind.DISSOLVE, duration=10)
    assert transition in editor.state.transitions


def test_hdr_transition_registry_hides_unverified_effects(editor_fixture) -> None:
    _, editor, asset, video_track = editor_fixture
    editor.set_sequence_profile(
        ProjectProfile(
            width=1920,
            height=1080,
            fps_numerator=30,
            fps_denominator=1,
            color_mode=ColorMode.HDR10_BT2020_PQ,
            bit_depth=10,
        )
    )
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=30,
    )
    second = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=30,
        source_in=30,
        duration=30,
    )
    with pytest.raises(ValueError, match="HDR10"):
        editor.create_transition(first.id, second.id, TransitionKind.WIPE_LEFT, duration=8)
    transition = editor.create_transition(
        first.id,
        second.id,
        TransitionKind.DISSOLVE,
        duration=8,
    )
    assert transition.kind == TransitionKind.DISSOLVE


def test_copy_transition_annotations_and_undo_are_persisted(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=40,
    )
    copied = editor.copy_clip(first.id, timeline_start=40)
    transition = editor.create_transition(first.id, copied.id, TransitionKind.DISSOLVE, duration=8)
    marker = editor.add_marker(40, "切点")
    selection = editor.add_range(20, 60, "选区")

    assert copied.id != first.id
    assert repository.load_timeline(editor.sequence_id).markers[0] == marker
    assert repository.load_timeline(editor.sequence_id).ranges[0] == selection

    updated = editor.update_transition(
        transition.id,
        kind=TransitionKind.FADE,
        duration=6,
    )
    assert (updated.kind, updated.duration) == (TransitionKind.FADE, 6)
    editor.remove_transition(updated.id)
    assert repository.load_timeline(editor.sequence_id).transitions == []
    editor.undo()
    assert repository.load_timeline(editor.sequence_id).transitions[0].id == transition.id


def test_timeline_range_creates_complete_editable_short_sequence(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=40,
    )
    second = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=40,
        source_in=40,
        duration=40,
    )
    editor.create_transition(first.id, second.id, TransitionKind.DISSOLVE, duration=8)
    editor.add_marker(20, "重点")
    selected = editor.add_range(10, 70, "短视频选区")
    subtitle_track = next(track for track in editor.state.tracks if track.kind == TrackKind.SUBTITLE)
    document = SubtitleDocument(
        project_id=repository.get_project().id,
        asset_id=asset.id,
        language="zh-CN",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=15,
        end_frame=25,
        text="区间字幕",
    )
    repository.create_subtitle_document(document, [segment])
    repository.place_subtitle_document(document.id, subtitle_track.id)

    short = SequenceService(repository).create_short_from_range(
        editor.sequence_id,
        selected.id,
    )
    short_state = repository.load_timeline(short.id)
    assert (short.profile.width, short.profile.height) == (1080, 1920)
    assert [(clip.timeline_start, clip.source_in, clip.duration) for clip in short_state.clips] == [
        (0, 10, 30),
        (30, 40, 30),
    ]
    assert len(short_state.transitions) == 1
    assert [(item.frame, item.name) for item in short_state.markers] == [(10, "重点")]
    destination_subtitle_track = next(
        track for track in short_state.tracks if track.kind == TrackKind.SUBTITLE
    )
    placements = repository.list_subtitle_placements(destination_subtitle_track.id)
    assert [(item.start_frame, item.end_frame) for item in placements] == [(5, 15)]


def test_short_creation_rolls_back_the_whole_use_case_on_late_failure(
    editor_fixture,
    monkeypatch,
) -> None:
    repository, editor, asset, video_track = editor_fixture
    editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=40,
    )
    selection = editor.add_range(5, 30, "Atomic short")
    before_ids = [item.id for item in repository.list_sequences()]

    def fail_after_sequence_was_staged(_state) -> None:
        raise RuntimeError("late timeline failure")

    monkeypatch.setattr(repository, "save_timeline", fail_after_sequence_was_staged)
    with pytest.raises(RuntimeError, match="late timeline failure"):
        SequenceService(repository).create_short_from_range(
            editor.sequence_id,
            selection.id,
        )

    assert [item.id for item in repository.list_sequences()] == before_ids


def test_ripple_delete_moves_all_unlocked_tracks(editor_fixture) -> None:
    _, editor, asset, video_track = editor_fixture
    overlay_track = editor.add_track(TrackKind.VIDEO)
    locked_track = editor.add_track(TrackKind.VIDEO)
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=30,
    )
    later = editor.add_clip(
        track_id=overlay_track.id,
        asset_id=asset.id,
        timeline_start=40,
        source_in=0,
        duration=20,
    )
    locked = editor.add_clip(
        track_id=locked_track.id,
        asset_id=asset.id,
        timeline_start=40,
        source_in=0,
        duration=20,
    )
    editor.set_track_state(
        locked_track.id,
        enabled=True,
        locked=True,
        muted=False,
        solo=False,
        audio_bus_id=locked_track.audio_bus_id,
    )
    marker = editor.add_marker(45, "后续")
    selection = editor.add_range(35, 55, "后续选区")
    before = editor.state
    after = before.model_copy(update={"clips": [clip for clip in before.clips if clip.id != first.id]})
    assert TimelineDiff.ripple_adjustments(
        before,
        after,
        source_track_ids={video_track.id},
    ) == [RippleAdjustment(interval=FrameInterval(0, 30), delta_frames=-30)]
    editor.delete_clip(first.id, ripple=True)
    assert next(clip for clip in editor.state.clips if clip.id == later.id).timeline_start == 10
    assert next(clip for clip in editor.state.clips if clip.id == locked.id).timeline_start == 40
    assert next(item for item in editor.state.markers if item.id == marker.id).frame == 15
    shifted = next(item for item in editor.state.ranges if item.id == selection.id)
    assert (shifted.start_frame, shifted.end_frame) == (5, 25)


def test_snapping_uses_nearest_frame_with_stable_tie_break() -> None:
    assert TimelineEditor.snap_frame(100, [104, 96], 4) == 96
    assert TimelineEditor.snap_frame(100, [105], 4) == 100


def test_multi_clip_move_and_delete_are_each_one_persisted_command(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=20,
    )
    second = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=20,
        source_in=20,
        duration=20,
    )
    transition = editor.create_transition(
        first.id,
        second.id,
        TransitionKind.DISSOLVE,
        duration=6,
    )

    editor.move_clips(
        [first.id, second.id],
        primary_clip_id=first.id,
        timeline_start=10,
        track_id=video_track.id,
    )
    moved = repository.load_timeline(editor.sequence_id)
    assert [(clip.id, clip.timeline_start) for clip in moved.clips] == [
        (first.id, 10),
        (second.id, 30),
    ]
    assert moved.transitions[0].id == transition.id
    editor.undo()
    assert [clip.timeline_start for clip in editor.state.clips] == [0, 20]

    editor.delete_clips([first.id, second.id])
    assert repository.load_timeline(editor.sequence_id).clips == []
    editor.undo()
    restored = repository.load_timeline(editor.sequence_id)
    assert [clip.id for clip in restored.clips] == [first.id, second.id]
    assert restored.transitions[0].id == transition.id


def test_multi_track_ripple_diff_does_not_hide_one_tracks_gap(editor_fixture) -> None:
    _, editor, asset, video_track = editor_fixture
    overlay_track = editor.add_track(TrackKind.VIDEO)
    first_gap = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=10,
    )
    first_later = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=20,
        source_in=20,
        duration=5,
    )
    blocker = editor.add_clip(
        track_id=overlay_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=10,
    )
    second_gap = editor.add_clip(
        track_id=overlay_track.id,
        asset_id=asset.id,
        timeline_start=30,
        source_in=30,
        duration=10,
    )
    second_later = editor.add_clip(
        track_id=overlay_track.id,
        asset_id=asset.id,
        timeline_start=50,
        source_in=50,
        duration=5,
    )
    before = editor.state
    deleted_ids = {first_gap.id, second_gap.id}
    after = before.model_copy(update={"clips": [clip for clip in before.clips if clip.id not in deleted_ids]})
    assert TimelineDiff.ripple_adjustments(
        before,
        after,
        source_track_ids={video_track.id, overlay_track.id},
    ) == [
        RippleAdjustment(interval=FrameInterval(0, 10), delta_frames=-10),
        RippleAdjustment(interval=FrameInterval(30, 40), delta_frames=-10),
    ]

    editor.delete_clips(deleted_ids, ripple=True)
    positions = {clip.id: clip.timeline_start for clip in editor.state.clips}
    assert positions[first_later.id] == 10
    assert positions[blocker.id] == 0
    assert positions[second_later.id] == 30


def test_track_controls_and_reordering_share_the_command_stack(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    overlay = editor.add_track(TrackKind.VIDEO)
    updated = editor.set_track_state(
        video_track.id,
        enabled=False,
        locked=True,
        muted=True,
        solo=True,
        audio_bus_id=video_track.audio_bus_id,
    )
    assert (updated.enabled, updated.locked, updated.muted, updated.solo) == (
        False,
        True,
        True,
        True,
    )
    with pytest.raises(PermissionError, match="locked"):
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )

    editor.move_track(overlay.id, 0)
    assert [track.id for track in editor.state.tracks][:2] == [overlay.id, video_track.id]
    assert [track.position for track in repository.load_timeline(editor.sequence_id).tracks] == [0, 1, 2, 3]

    editor.undo()
    assert editor.state.tracks[-1].id == overlay.id
    editor.undo()
    restored = next(track for track in editor.state.tracks if track.id == video_track.id)
    assert (restored.enabled, restored.locked, restored.muted, restored.solo) == (
        True,
        False,
        False,
        False,
    )


def test_profile_frame_clock_change_retimes_timeline_subtitles_and_undo(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=30,
        source_in=15,
        duration=60,
    )
    editor.set_clip_audio(
        first.id,
        ClipAudio(fade_in_frames=6, fade_out_frames=9),
    )
    subtitle_track = next(track for track in editor.state.tracks if track.kind == TrackKind.SUBTITLE)
    document = SubtitleDocument(
        project_id=repository.get_project().id,
        asset_id=asset.id,
        language="zh-CN",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=30,
        end_frame=60,
        text="字幕",
    )
    repository.create_subtitle_document(document, [segment])
    repository.place_subtitle_document(document.id, subtitle_track.id)

    editor.set_sequence_profile(ProjectProfile(width=1080, height=1920, fps_numerator=60, fps_denominator=1))
    changed = editor.state.clips[0]
    assert (changed.timeline_start, changed.source_in, changed.duration) == (60, 30, 120)
    assert (changed.audio.fade_in_frames, changed.audio.fade_out_frames) == (12, 18)
    placement = repository.list_subtitle_placements(subtitle_track.id)[0]
    assert (placement.start_frame, placement.end_frame) == (90, 150)

    editor.undo()
    restored = editor.state.clips[0]
    assert (restored.timeline_start, restored.source_in, restored.duration) == (30, 15, 60)
    placement = repository.list_subtitle_placements(subtitle_track.id)[0]
    assert (placement.start_frame, placement.end_frame) == (45, 75)
