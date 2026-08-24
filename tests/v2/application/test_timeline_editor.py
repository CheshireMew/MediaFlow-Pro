from pathlib import Path

import pytest

from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_diff import FrameInterval, RippleAdjustment, TimelineDiff
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    ColorMode,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.project import MediaMetadata, ProjectProfile
from mediaflow.domain.sequence_audio import audio_clips_for_track
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.timeline import (
    ClipAddRequest,
    ClipAudio,
    ClipTransformKeyframe,
    TimelineMarker,
)
from mediaflow.infrastructure.project_repository import ProjectRepository


@pytest.fixture
def editor_fixture(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-producer-output")
    repository = ProjectRepository.create(tmp_path / "Project", "Project")
    asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
    project = repository.projects.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    video_track = editor.add_track(TrackKind.VIDEO)
    editor.add_track(TrackKind.AUDIO)
    editor.add_track(TrackKind.SUBTITLE)
    try:
        yield repository, editor, asset, video_track
    finally:
        repository.close()


def test_duration_frames_matches_the_cached_timeline_state(editor_fixture) -> None:
    _repository, editor, asset, video_track = editor_fixture
    editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=25,
        source_in=0,
        duration=75,
    )

    assert editor.duration_frames == editor.state.duration_frames == 100


def test_split_undo_redo_round_trip_is_persisted(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=10,
        duration=100,
    )
    checkpoint = editor.history.checkpoint()
    left, right = editor.split_clip(clip.id, 40)
    changes = editor.history.change_set_since(checkpoint)
    assert (left.duration, right.timeline_start, right.source_in) == (40, 40, 50)
    assert f"/sequences/{editor.sequence_id}/clips/{clip.id}/duration" in changes.write_set
    assert f"/sequences/{editor.sequence_id}/clips/{right.id}" in changes.write_set
    assert len(repository.timeline.load_timeline(editor.sequence_id).clips) == 2

    editor.undo()
    persisted = repository.timeline.load_timeline(editor.sequence_id)
    assert [(item.timeline_start, item.duration) for item in persisted.clips] == [(0, 100)]

    editor.redo()
    persisted = repository.timeline.load_timeline(editor.sequence_id)
    assert [(item.timeline_start, item.duration) for item in persisted.clips] == [(0, 40), (40, 60)]


def test_clip_membership_edits_use_the_incremental_storage_boundary(
    editor_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, editor, asset, video_track = editor_fixture
    source = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=120,
    )
    calls: list[tuple[set[str], set[str]]] = []
    original = repository.timeline.save_clip_set_changes

    def save_delta(state, *, changed_clip_ids, removed_clip_ids, changed_web_state_ids):
        calls.append((set(changed_clip_ids), set(removed_clip_ids)))
        return original(
            state,
            changed_clip_ids=changed_clip_ids,
            removed_clip_ids=removed_clip_ids,
            changed_web_state_ids=changed_web_state_ids,
        )

    monkeypatch.setattr(repository.timeline, "save_clip_set_changes", save_delta)
    monkeypatch.setattr(
        repository.timeline,
        "save_timeline",
        lambda _state: (_ for _ in ()).throw(
            AssertionError("clip membership edit rewrote the complete timeline")
        ),
    )

    copied = editor.copy_clip(source.id, timeline_start=120)
    left, right = editor.split_clip(copied.id, 180)
    editor.delete_clips([right.id])

    assert calls[0] == ({copied.id}, set())
    assert calls[1] == ({left.id, right.id}, set())
    assert calls[2] == (set(), {right.id})


def test_batch_add_is_one_atomic_undoable_edit(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    clips = editor.add_clips(
        [
            ClipAddRequest(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=0,
                source_in=0,
                duration=20,
            ),
            ClipAddRequest(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=20,
                source_in=20,
                duration=20,
            ),
        ]
    )

    assert [clip.timeline_start for clip in clips] == [0, 20]
    assert len(repository.timeline.load_timeline(editor.sequence_id).clips) == 2
    editor.undo()
    assert repository.timeline.load_timeline(editor.sequence_id).clips == []

    with pytest.raises(KeyError):
        editor.add_clips(
            [
                ClipAddRequest(
                    track_id=video_track.id,
                    asset_id=asset.id,
                    timeline_start=0,
                    source_in=0,
                    duration=20,
                ),
                ClipAddRequest(
                    track_id="missing-track",
                    asset_id=asset.id,
                    timeline_start=20,
                    source_in=20,
                    duration=20,
                ),
            ]
        )
    assert repository.timeline.load_timeline(editor.sequence_id).clips == []


def test_timeline_history_serializes_only_entities_changed_by_the_edit(
    editor_fixture,
) -> None:
    _repository, editor, asset, video_track = editor_fixture
    clips = editor.add_clips(
        [
            ClipAddRequest(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=index * 20,
                source_in=index * 20,
                duration=20,
            )
            for index in range(100)
        ]
    )
    editor.history.clear()

    editor.move_clip(
        clips[-1].id,
        timeline_start=2_000,
    )

    command = editor.history.checkpoint().undo[-1]
    assert len(command.undo_actions) == 1
    assert len(command.redo_actions) == 1
    for action in (*command.undo_actions, *command.redo_actions):
        assert action.payload["mode"] == "patch"
        assert action.payload["source"]["tracks"] == []
        assert action.payload["destination"]["tracks"] == []
        assert len(action.payload["source"]["clips"]) == 1
        assert len(action.payload["destination"]["clips"]) == 1
    assert len(command.model_dump_json(exclude_computed_fields=True)) < 10_000


def test_known_media_source_bounds_are_enforced_at_edit_and_storage_boundaries(
    editor_fixture,
) -> None:
    repository, editor, asset, video_track = editor_fixture
    asset = repository.assets.update_asset(
        asset.model_copy(
            update={
                "metadata": asset.metadata.model_copy(
                    update={"duration_frames": 10},
                )
            }
        )
    )

    with pytest.raises(ValueError, match="source range"):
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=10,
            duration=1,
        )
    with pytest.raises(ValueError, match="source range"):
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=1,
            duration=3,
            speed_numerator=-1,
        )

    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=10,
    )
    with pytest.raises(ValueError, match="source range"):
        editor.trim_clip(
            clip.id,
            timeline_start=0,
            source_in=2,
            duration=10,
        )
    invalid = editor.state
    invalid.clips[0] = invalid.clips[0].model_copy(update={"source_in": 100, "duration": 2})
    with pytest.raises(ValueError, match="source range"):
        repository.timeline.save_timeline(invalid)


def test_replace_source_is_one_undoable_edit_and_revalidates_source_bounds(
    editor_fixture,
) -> None:
    repository, editor, asset, video_track = editor_fixture
    source = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=12,
        duration=30,
    )
    replacement_path = repository.project_dir.parent / "replacement.png"
    replacement_path.write_bytes(b"real-replacement-output")
    replacement = repository.assets.import_external_asset(
        replacement_path,
        AssetKind.IMAGE,
    )

    changed = editor.replace_clip_source(source.id, replacement.id)

    assert changed.asset_id == replacement.id
    assert changed.source_in == 0
    assert changed.duration == 30
    assert changed.media_kind == ClipMediaKind.VIDEO_ONLY
    assert repository.timeline.load_timeline(editor.sequence_id).clips[0] == changed

    editor.undo()
    restored = editor.state.clips[0]
    assert restored.asset_id == asset.id
    assert restored.source_in == 12


def test_visual_effect_stack_is_validated_ordered_persisted_and_undoable(
    editor_fixture,
) -> None:
    repository, editor, asset, video_track = editor_fixture
    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=30,
    )
    adjustment = editor.add_clip_visual_effect(
        clip.id,
        VisualEffectKind.COLOR_ADJUSTMENT,
    )
    vignette = editor.add_clip_visual_effect(
        clip.id,
        VisualEffectKind.VIGNETTE,
    )
    editor.update_clip_visual_effect(
        clip.id,
        adjustment.id,
        enabled=True,
        parameters={"brightness": 0.15, "contrast": 1.2, "saturation": 1.1},
    )
    editor.move_clip_visual_effect(clip.id, vignette.id, 0)

    stored = repository.timeline.load_timeline(editor.sequence_id).clips[0]
    assert [(item.kind, item.position) for item in stored.visual_effects] == [
        (VisualEffectKind.VIGNETTE, 0),
        (VisualEffectKind.COLOR_ADJUSTMENT, 1),
    ]
    assert stored.visual_effects[1].parameters["brightness"] == pytest.approx(0.15)
    with pytest.raises(ValueError, match="exceeds maximum"):
        editor.update_clip_visual_effect(
            clip.id,
            adjustment.id,
            enabled=True,
            parameters={"brightness": 4.0, "contrast": 1.0, "saturation": 1.0},
        )

    editor.undo()
    assert [item.id for item in editor.state.clips[0].visual_effects] == [
        adjustment.id,
        vignette.id,
    ]


def test_split_preserves_valid_incoming_and_outgoing_transitions(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    incoming = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=20,
    )
    source = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=20,
        source_in=20,
        duration=40,
    )
    outgoing = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=60,
        source_in=60,
        duration=20,
    )
    incoming_transition = editor.create_transition(
        incoming.id,
        source.id,
        TransitionKind.DISSOLVE,
        duration=6,
    )
    outgoing_transition = editor.create_transition(
        source.id,
        outgoing.id,
        TransitionKind.DISSOLVE,
        duration=6,
    )

    left, right = editor.split_clip(source.id, 40)
    transitions = {
        item.id: item for item in repository.timeline.load_timeline(editor.sequence_id).transitions
    }

    assert transitions[incoming_transition.id].right_clip_id == left.id
    assert transitions[outgoing_transition.id].left_clip_id == right.id


def test_undo_and_redo_preserve_unrelated_background_timeline_changes(
    editor_fixture,
) -> None:
    repository, editor, _asset, _video_track = editor_fixture
    editor.history.clear()
    added = editor.add_track(TrackKind.VIDEO, "用户轨道")

    background = repository.timeline.load_timeline(editor.sequence_id)
    marker = TimelineMarker(
        sequence_id=editor.sequence_id,
        frame=12,
        name="后台分析结果",
    )
    background.markers.append(marker)
    repository.timeline.save_timeline(background)
    editor.reload()

    editor.undo()
    after_undo = repository.timeline.load_timeline(editor.sequence_id)
    assert marker in after_undo.markers
    assert added.id not in {track.id for track in after_undo.tracks}

    editor.redo()
    after_redo = repository.timeline.load_timeline(editor.sequence_id)
    assert marker in after_redo.markers
    assert added.id in {track.id for track in after_redo.tracks}


def test_edit_merges_background_change_without_manual_reload(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    clip = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=20,
    )
    background = repository.timeline.load_timeline(editor.sequence_id)
    marker = TimelineMarker(
        sequence_id=editor.sequence_id,
        frame=12,
        name="后台分析结果",
    )
    background.markers.append(marker)
    repository.timeline.save_timeline(background)

    editor.move_clip(clip.id, timeline_start=30)

    persisted = repository.timeline.load_timeline(editor.sequence_id)
    assert persisted.markers == [marker]
    assert persisted.clips[0].timeline_start == 30
    assert editor.state.sequence.timeline_revision == persisted.sequence.timeline_revision


def test_in_memory_timeline_matches_durable_order_after_fast_persistence(
    editor_fixture,
) -> None:
    repository, editor, asset, video_track = editor_fixture
    overlay_track = editor.add_track(TrackKind.VIDEO, "Overlay")
    later = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=30,
        source_in=0,
        duration=20,
    )
    editor.add_clip(
        track_id=overlay_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=20,
    )
    editor.set_clip_audio(later.id, ClipAudio(gain_db=-3.0))

    assert editor.state == repository.timeline.load_timeline(editor.sequence_id)


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
    state = repository.timeline.load_timeline(editor.sequence_id)
    assert (state.sequence.in_out.in_frame, state.sequence.in_out.out_frame) == (12, 88)
    assert state.clips[0] == clip

    editor.undo()
    assert repository.timeline.load_timeline(editor.sequence_id).sequence.in_out is None
    assert repository.timeline.load_timeline(editor.sequence_id).clips[0] == clip
    editor.redo()
    assert repository.timeline.load_timeline(editor.sequence_id).sequence.in_out.out_frame == 88

    editor.trim_clip(clip.id, timeline_start=0, source_in=10, duration=60)
    assert repository.timeline.load_timeline(editor.sequence_id).sequence.in_out.out_frame == 60
    editor.clear_sequence_in_out()
    assert repository.timeline.load_timeline(editor.sequence_id).sequence.in_out is None


def test_primary_dialogue_track_is_unique_persisted_and_undoable(editor_fixture) -> None:
    repository, editor, _asset, _video_track = editor_fixture
    first_audio = next(track for track in editor.state.tracks if track.kind == TrackKind.AUDIO)
    second_audio = editor.add_track(TrackKind.AUDIO)

    assert not any(track.primary_dialogue for track in editor.state.tracks)
    voice_source = repository.project_dir.parent / "voice.wav"
    voice_source.write_bytes(b"real-audio-producer-output")
    voice_asset = repository.assets.import_external_asset(voice_source, AssetKind.AUDIO)
    editor.add_clip(
        track_id=second_audio.id,
        asset_id=voice_asset.id,
        timeline_start=0,
        source_in=0,
        duration=100,
    )
    persisted = repository.timeline.load_timeline(editor.sequence_id)
    assert [track.id for track in persisted.tracks if track.primary_dialogue] == [second_audio.id]

    editor.set_primary_dialogue_track(first_audio.id)
    persisted = repository.timeline.load_timeline(editor.sequence_id)
    assert [track.id for track in persisted.tracks if track.primary_dialogue] == [first_audio.id]

    editor.set_primary_dialogue_track(second_audio.id)
    persisted = repository.timeline.load_timeline(editor.sequence_id)
    assert [track.id for track in persisted.tracks if track.primary_dialogue] == [second_audio.id]

    editor.undo()
    assert [
        track.id
        for track in repository.timeline.load_timeline(editor.sequence_id).tracks
        if track.primary_dialogue
    ] == [first_audio.id]


def test_empty_audio_track_does_not_claim_dialogue_before_linked_audio_appears(
    editor_fixture,
) -> None:
    repository, editor, asset, video_track = editor_fixture
    asset = repository.assets.update_asset(
        asset.model_copy(
            update={"metadata": asset.metadata.model_copy(update={"has_video": True, "has_audio": True})}
        )
    )

    assert not any(track.primary_dialogue for track in editor.state.tracks)
    editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=100,
    )
    persisted = repository.timeline.load_timeline(editor.sequence_id)
    persisted_video = next(track for track in persisted.tracks if track.id == video_track.id)
    assert persisted_video.linked_audio_track_id is not None
    assert [track.id for track in persisted.tracks if track.primary_dialogue] == [
        persisted_video.linked_audio_track_id
    ]


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
        project_id=repository.projects.get_project().id,
        asset_id=asset.id,
        language="zh-CN",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=30,
        end_frame=50,
        text="跟随片段",
    )
    repository.subtitles.create_subtitle_document(document, [segment])
    repository.subtitles.place_subtitle_document(document.id, subtitle_track.id, follow_clips=True)
    placements = repository.subtitles.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame, item.clip_id) for item in placements] == [(20, 40, clip.id)]

    editor.trim_clip(clip.id, timeline_start=20, source_in=30, duration=80)
    editor.move_clip(clip.id, timeline_start=40)
    editor.set_clip_speed(
        clip.id,
        speed_numerator=2,
        speed_denominator=1,
        pitch_compensation=True,
    )
    placements = repository.subtitles.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame) for item in placements] == [(40, 50)]

    left, right = editor.split_clip(clip.id, 45)
    placements = repository.subtitles.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame, item.clip_id) for item in placements] == [
        (40, 45, left.id),
        (45, 50, right.id),
    ]

    editor.undo()
    placements = repository.subtitles.list_subtitle_placements(subtitle_track.id)
    assert [(item.start_frame, item.end_frame, item.clip_id) for item in placements] == [(40, 50, clip.id)]
    editor.redo()
    assert len(repository.subtitles.list_subtitle_placements(subtitle_track.id)) == 2


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


def test_dragging_clips_together_does_not_create_a_transition(editor_fixture) -> None:
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

    moved = editor.move_clip(second.id, timeline_start=40)
    state = repository.timeline.load_timeline(editor.sequence_id)
    assert moved.timeline_start == first.timeline_end
    assert state.transitions == []

    editor.undo()
    restored = repository.timeline.load_timeline(editor.sequence_id)
    assert next(item for item in restored.clips if item.id == second.id).timeline_start == 60
    assert restored.transitions == []


def test_compound_clip_is_persisted_moved_as_one_unit_and_undoable(editor_fixture) -> None:
    repository, editor, asset, video_track = editor_fixture
    first = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=20,
        source_in=0,
        duration=30,
    )
    second = editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=50,
        source_in=30,
        duration=40,
    )

    compound = editor.create_compound_clip([second.id, first.id])
    persisted = repository.timeline.load_timeline(editor.sequence_id)
    assert persisted.compounds == [compound]
    assert compound.clip_ids == [first.id, second.id]

    editor.move_clips(
        compound.clip_ids,
        primary_clip_id=first.id,
        timeline_start=40,
        track_id=video_track.id,
    )
    moved = repository.timeline.load_timeline(editor.sequence_id)
    assert [(clip.id, clip.timeline_start) for clip in moved.clips] == [
        (first.id, 40),
        (second.id, 70),
    ]
    assert moved.compounds == [compound]

    editor.undo()
    restored = repository.timeline.load_timeline(editor.sequence_id)
    assert [(clip.id, clip.timeline_start) for clip in restored.clips] == [
        (first.id, 20),
        (second.id, 50),
    ]
    editor.dissolve_compound_clip(compound.id)
    assert repository.timeline.load_timeline(editor.sequence_id).compounds == []


def test_compound_clip_requires_adjacent_clips_on_one_track(editor_fixture) -> None:
    _, editor, asset, video_track = editor_fixture
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
        timeline_start=30,
        source_in=20,
        duration=20,
    )

    with pytest.raises(ValueError, match="首尾相接"):
        editor.create_compound_clip([first.id, second.id])


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
    assert repository.timeline.load_timeline(editor.sequence_id).markers[0] == marker
    assert repository.timeline.load_timeline(editor.sequence_id).ranges[0] == selection

    updated = editor.update_transition(
        transition.id,
        kind=TransitionKind.FADE,
        duration=6,
    )
    assert (updated.kind, updated.duration) == (TransitionKind.FADE, 6)
    editor.remove_transition(updated.id)
    assert repository.timeline.load_timeline(editor.sequence_id).transitions == []
    editor.undo()
    assert repository.timeline.load_timeline(editor.sequence_id).transitions[0].id == transition.id


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
        project_id=repository.projects.get_project().id,
        asset_id=asset.id,
        language="zh-CN",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=15,
        end_frame=25,
        text="区间字幕",
    )
    repository.subtitles.create_subtitle_document(document, [segment])
    repository.subtitles.place_subtitle_document(document.id, subtitle_track.id)

    short = SequenceService(repository).create_short_from_range(
        editor.sequence_id,
        selected.id,
    )
    short_state = repository.timeline.load_timeline(short.id)
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
    placements = repository.subtitles.list_subtitle_placements(destination_subtitle_track.id)
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
    before_ids = [item.id for item in repository.sequences.list_sequences()]

    def fail_after_sequence_was_staged(_state) -> None:
        raise RuntimeError("late timeline failure")

    monkeypatch.setattr(
        repository.timeline,
        "save_timeline",
        fail_after_sequence_was_staged,
    )
    with pytest.raises(RuntimeError, match="late timeline failure"):
        SequenceService(repository).create_short_from_range(
            editor.sequence_id,
            selection.id,
        )

    assert [item.id for item in repository.sequences.list_sequences()] == before_ids


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
    checkpoint = editor.history.checkpoint()
    editor.delete_clip(first.id, ripple=True)
    changes = editor.history.change_set_since(checkpoint)
    assert next(clip for clip in editor.state.clips if clip.id == later.id).timeline_start == 10
    assert next(clip for clip in editor.state.clips if clip.id == locked.id).timeline_start == 40
    assert next(item for item in editor.state.markers if item.id == marker.id).frame == 15
    shifted = next(item for item in editor.state.ranges if item.id == selection.id)
    assert (shifted.start_frame, shifted.end_frame) == (5, 25)
    assert f"/sequences/{editor.sequence_id}/clips/{first.id}" in changes.write_set
    assert f"/sequences/{editor.sequence_id}/clips/{later.id}/timeline_start" in changes.write_set
    assert f"/sequences/{editor.sequence_id}/markers/{marker.id}/frame" in changes.write_set
    assert f"/sequences/{editor.sequence_id}/ranges/{selection.id}/start_frame" in changes.write_set


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
    moved = repository.timeline.load_timeline(editor.sequence_id)
    assert [(clip.id, clip.timeline_start) for clip in moved.clips] == [
        (first.id, 10),
        (second.id, 30),
    ]
    assert moved.transitions[0].id == transition.id
    editor.undo()
    assert [clip.timeline_start for clip in editor.state.clips] == [0, 20]

    editor.delete_clips([first.id, second.id])
    assert repository.timeline.load_timeline(editor.sequence_id).clips == []
    editor.undo()
    restored = repository.timeline.load_timeline(editor.sequence_id)
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
    assert [track.position for track in repository.timeline.load_timeline(editor.sequence_id).tracks] == [
        0,
        1,
        2,
        3,
    ]

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
        project_id=repository.projects.get_project().id,
        asset_id=asset.id,
        language="zh-CN",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=30,
        end_frame=60,
        text="字幕",
    )
    repository.subtitles.create_subtitle_document(document, [segment])
    repository.subtitles.place_subtitle_document(document.id, subtitle_track.id)

    editor.set_sequence_profile(ProjectProfile(width=1080, height=1920, fps_numerator=60, fps_denominator=1))
    changed = editor.state.clips[0]
    assert (changed.timeline_start, changed.source_in, changed.duration) == (60, 30, 120)
    assert (changed.audio.fade_in_frames, changed.audio.fade_out_frames) == (12, 18)
    placement = repository.subtitles.list_subtitle_placements(subtitle_track.id)[0]
    assert (placement.start_frame, placement.end_frame) == (90, 150)

    editor.undo()
    restored = editor.state.clips[0]
    assert (restored.timeline_start, restored.source_in, restored.duration) == (30, 15, 60)
    placement = repository.subtitles.list_subtitle_placements(subtitle_track.id)[0]
    assert (placement.start_frame, placement.end_frame) == (45, 75)


def test_profile_frame_clock_change_reframes_known_asset_bounds_atomically(
    tmp_path: Path,
) -> None:
    profile_25 = ProjectProfile(fps_numerator=25, fps_denominator=1)
    source = tmp_path / "clock-source.mp4"
    source.write_bytes(b"clock-source")
    with ProjectRepository.create(
        tmp_path / "Clock Project",
        "Clock Project",
        profile_25,
    ) as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(update={"duration_frames": 25, "has_video": True})
                }
            )
        )
        editor = TimelineEditor(
            repository,
            repository.projects.get_project().main_sequence_id,
        )
        video_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )

        editor.set_sequence_profile(profile_25.model_copy(update={"fps_numerator": 30}))
        assert editor.state.clips[0].duration == 30
        assert repository.assets.get_asset(asset.id).metadata.duration_frames == 30

        editor.undo()
        assert editor.state.clips[0].duration == 25
        assert repository.assets.get_asset(asset.id).metadata.duration_frames == 25


def test_main_profile_cannot_bypass_the_frame_clock_transaction(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(
        tmp_path / "Main Clock Boundary",
        "Main Clock Boundary",
    ) as repository:
        sequence_id = repository.projects.get_project().main_sequence_id
        before = repository.timeline.load_timeline(sequence_id)
        invalid = before.model_copy(deep=True)
        invalid.sequence = invalid.sequence.model_copy(
            update={"profile": invalid.sequence.profile.model_copy(update={"fps_numerator": 24})}
        )

        with pytest.raises(
            RuntimeError,
            match="frame-clock transaction",
        ):
            repository.timeline.save_timeline(invalid)

        assert repository.timeline.load_timeline(sequence_id) == before


def test_main_clock_change_resyncs_short_subtitles_without_moving_overrides(
    tmp_path: Path,
) -> None:
    main_profile = ProjectProfile(fps_numerator=120, fps_denominator=1)
    short_profile = main_profile.model_copy(
        update={
            "width": 1080,
            "height": 1920,
            "fps_numerator": 60,
        }
    )
    subtitle_source = tmp_path / "shared-clock.srt"
    subtitle_source.write_text("", encoding="utf-8")
    with ProjectRepository.create(
        tmp_path / "Shared Subtitle Clock",
        "Shared Subtitle Clock",
        main_profile,
    ) as repository:
        project = repository.projects.get_project()
        short = repository.sequences.create_short_sequence(
            "Short",
            short_profile,
        )
        asset = repository.assets.import_external_asset(
            subtitle_source,
            AssetKind.SUBTITLE,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="en",
        )
        first = SubtitleSegment(
            document_id=document.id,
            start_frame=3,
            end_frame=7,
            text="derived",
        )
        second = SubtitleSegment(
            document_id=document.id,
            start_frame=10,
            end_frame=14,
            text="overridden",
        )
        repository.subtitles.create_subtitle_document(
            document,
            [first, second],
        )
        short_editor = TimelineEditor(repository, short.id)
        subtitle_track = short_editor.add_track(TrackKind.SUBTITLE)
        placements = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )
        by_segment = {item.segment_id: item for item in placements}
        assert (
            by_segment[first.id].start_frame,
            by_segment[first.id].end_frame,
        ) == (1, 4)
        repository.subtitles.update_subtitle_placement_range(
            by_segment[second.id].id,
            20,
            30,
        )

        main_editor = TimelineEditor(
            repository,
            project.main_sequence_id,
        )
        main_editor.set_sequence_profile(main_profile.model_copy(update={"fps_numerator": 24}))
        changed = {
            item.segment_id: item for item in repository.subtitles.list_subtitle_placements(subtitle_track.id)
        }
        assert (
            changed[first.id].start_frame,
            changed[first.id].end_frame,
        ) == (0, 5)
        assert (
            changed[second.id].start_frame,
            changed[second.id].end_frame,
            changed[second.id].timing_overridden,
        ) == (20, 30, True)

        main_editor.undo()
        restored = {
            item.segment_id: item for item in repository.subtitles.list_subtitle_placements(subtitle_track.id)
        }
        assert (
            restored[first.id].start_frame,
            restored[first.id].end_frame,
        ) == (1, 4)
        assert (
            restored[second.id].start_frame,
            restored[second.id].end_frame,
            restored[second.id].timing_overridden,
        ) == (20, 30, True)

        main_editor.redo()
        redone = {
            item.segment_id: item for item in repository.subtitles.list_subtitle_placements(subtitle_track.id)
        }
        assert (
            redone[first.id].start_frame,
            redone[first.id].end_frame,
        ) == (0, 5)


def test_short_sequence_source_bounds_use_the_short_sequence_frame_clock(
    tmp_path: Path,
) -> None:
    main_profile = ProjectProfile(fps_numerator=25, fps_denominator=1)
    source = tmp_path / "short-clock-source.mp4"
    source.write_bytes(b"short-clock-source")
    with ProjectRepository.create(
        tmp_path / "Short Clock Project",
        "Short Clock Project",
        main_profile,
    ) as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(update={"duration_frames": 25, "has_video": True})
                }
            )
        )
        short = repository.sequences.create_short_sequence(
            "30 fps short",
            main_profile.model_copy(update={"fps_numerator": 30}),
        )
        editor = TimelineEditor(repository, short.id)
        video_track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        assert clip.duration == 30
        with pytest.raises(ValueError, match="source range"):
            editor.trim_clip(
                clip.id,
                timeline_start=0,
                source_in=1,
                duration=30,
            )


def test_linked_video_moves_between_video_tracks_and_detaches_to_independent_audio(
    tmp_path: Path,
) -> None:
    source = tmp_path / "linked.mp4"
    source.write_bytes(b"av-source")
    with ProjectRepository.create(tmp_path / "Linked Project", "Linked Project") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": MediaMetadata(
                        duration_frames=240,
                        has_video=True,
                        has_audio=True,
                    )
                }
            )
        )
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        first_video = editor.add_track(TrackKind.VIDEO)
        first = editor.add_clip(
            track_id=first_video.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=60,
        )
        state = editor.state
        first_video = next(track for track in state.tracks if track.id == first_video.id)
        first_audio_id = first_video.linked_audio_track_id
        assert first.media_kind == ClipMediaKind.LINKED_AV
        assert first_audio_id is not None
        assert [clip.id for clip in audio_clips_for_track(state, first_audio_id)] == [first.id]

        second_video = editor.add_track(TrackKind.VIDEO)
        second = editor.add_clip(
            track_id=second_video.id,
            asset_id=asset.id,
            timeline_start=60,
            source_in=60,
            duration=60,
        )
        second_audio_id = next(
            track.linked_audio_track_id for track in editor.state.tracks if track.id == second_video.id
        )
        assert second_audio_id is not None and second_audio_id != first_audio_id

        moved = editor.move_clip(second.id, timeline_start=60, track_id=first_video.id)
        assert moved.track_id == first_video.id
        assert [clip.id for clip in audio_clips_for_track(editor.state, first_audio_id)] == [
            first.id,
            second.id,
        ]

        second_audio_track = next(track for track in editor.state.tracks if track.id == second_audio_id)
        editor.set_track_state(
            second_audio_id,
            enabled=second_audio_track.enabled,
            locked=True,
            muted=second_audio_track.muted,
            solo=second_audio_track.solo,
            audio_bus_id=second_audio_track.audio_bus_id,
        )
        with pytest.raises(PermissionError, match="Track is locked"):
            editor.move_clip(
                second.id,
                timeline_start=60,
                track_id=second_video.id,
            )
        assert next(clip for clip in editor.state.clips if clip.id == second.id).track_id == first_video.id
        editor.set_track_state(
            second_audio_id,
            enabled=second_audio_track.enabled,
            locked=False,
            muted=second_audio_track.muted,
            solo=second_audio_track.solo,
            audio_bus_id=second_audio_track.audio_bus_id,
        )

        detached_video, detached_audio = editor.detach_clip_audio(first.id)
        assert detached_video.media_kind == ClipMediaKind.VIDEO_ONLY
        assert detached_audio.media_kind == ClipMediaKind.AUDIO_ONLY
        editor.move_clip(
            detached_audio.id,
            timeline_start=130,
            track_id=second_audio_id,
        )
        assert editor.state.clips_for_track(first_video.id)[0].timeline_start == 0
        assert editor.state.clips_for_track(second_audio_id)[0].timeline_start == 130

        editor.undo()
        editor.undo()
        restored = next(clip for clip in editor.state.clips if clip.id == first.id)
        assert restored.media_kind == ClipMediaKind.LINKED_AV
        assert not any(clip.media_kind == ClipMediaKind.AUDIO_ONLY for clip in editor.state.clips)


def test_main_frame_clock_snapshot_round_trip_is_exact_across_reopen(
    tmp_path: Path,
) -> None:
    profile_120 = ProjectProfile(fps_numerator=120, fps_denominator=1)
    source = tmp_path / "snapshot-source.mp4"
    proxy = tmp_path / "snapshot-proxy.mp4"
    sdr_proxy = tmp_path / "snapshot-sdr.mp4"
    source.write_bytes(b"source")
    proxy.write_bytes(b"proxy")
    sdr_proxy.write_bytes(b"sdr")
    project_dir = tmp_path / "Exact Frame Clock"
    with ProjectRepository.create(
        project_dir,
        "Exact Frame Clock",
        profile_120,
    ) as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "proxy_path": str(proxy),
                    "sdr_preview_proxy_path": str(sdr_proxy),
                    "metadata": MediaMetadata(
                        duration_frames=120,
                        width=1920,
                        height=1080,
                        fps_numerator=120,
                        fps_denominator=1,
                        has_video=True,
                    ),
                }
            )
        )
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        first = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=60,
        )
        second = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=60,
            source_in=60,
            duration=60,
        )
        editor.set_clip_transform_keyframes(
            first.id,
            [
                ClipTransformKeyframe(
                    source_frame=3,
                    transform=first.transform,
                ),
                ClipTransformKeyframe(
                    source_frame=4,
                    transform=first.transform.model_copy(update={"x": 8.0}),
                ),
            ],
        )
        editor.set_clip_audio(
            first.id,
            ClipAudio(fade_in_frames=3, fade_out_frames=7),
        )
        editor.create_transition(
            first.id,
            second.id,
            TransitionKind.DISSOLVE,
            duration=7,
        )
        editor.add_marker(3, "三帧")
        editor.add_range(3, 7, "窄范围")
        editor.set_sequence_in_out(3, 119)

        document = SubtitleDocument(
            project_id=repository.projects.get_project().id,
            asset_id=asset.id,
            language="zh-CN",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=3,
            end_frame=7,
            text="精确字幕",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        repository.subtitles.save_subtitle_words(
            document.id,
            [
                SubtitleWord(
                    segment_id=segment.id,
                    position=0,
                    start_frame=3,
                    end_frame=4,
                    text="精",
                ),
                SubtitleWord(
                    segment_id=segment.id,
                    position=1,
                    start_frame=4,
                    end_frame=7,
                    text="确",
                ),
            ],
        )
        placement = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            offset_frames=3,
            source_start_frame=3,
            source_end_frame=7,
            follow_clips=False,
        )[0]
        repository.subtitles.update_subtitle_placement_range(
            placement.id,
            3,
            7,
            timing_overridden=True,
        )
        repository.highlights.save_highlights(
            [
                HighlightCandidate(
                    project_id=repository.projects.get_project().id,
                    asset_id=asset.id,
                    document_id=document.id,
                    sequence_id=sequence_id,
                    start_frame=3,
                    end_frame=7,
                    title="精确高光",
                )
            ]
        )
        before = repository.frame_clock.capture_main_frame_clock(sequence_id)

        editor.set_sequence_profile(profile_120.model_copy(update={"fps_numerator": 24}))
        after = repository.frame_clock.capture_main_frame_clock(sequence_id)
        assert after != before
        assert after.assets[0].proxy_path is None
        assert after.assets[0].sdr_preview_proxy_path is None
        assert len(after.timeline.clips[0].transform_keyframes) == 1
        assert (
            after.subtitle_links[0].source_start_frame,
            after.subtitle_links[0].source_end_frame,
        ) == (0, 2)
        assert (
            after.timeline.sequence.in_out.in_frame,
            after.timeline.sequence.in_out.out_frame,
        ) == (0, 24)
        assert (
            after.timeline.ranges[0].start_frame,
            after.timeline.ranges[0].end_frame,
        ) == (0, 2)
        with ProjectRepository.open(project_dir, writable=False) as observer:
            assert observer.frame_clock.capture_main_frame_clock(sequence_id) == after

        editor.undo()
        assert repository.frame_clock.capture_main_frame_clock(sequence_id) == before
        with ProjectRepository.open(project_dir, writable=False) as observer:
            assert observer.frame_clock.capture_main_frame_clock(sequence_id) == before

        editor.redo()
        assert repository.frame_clock.capture_main_frame_clock(sequence_id) == after
        with ProjectRepository.open(project_dir, writable=False) as observer:
            assert observer.frame_clock.capture_main_frame_clock(sequence_id) == after

        current_asset = repository.assets.get_asset(asset.id)
        repository.assets.update_asset(
            current_asset.model_copy(
                update={
                    "proxy_path": str(proxy),
                    "sdr_preview_proxy_path": str(sdr_proxy),
                }
            )
        )
        resolution_before = repository.frame_clock.capture_main_frame_clock(sequence_id)
        editor.reload()
        editor.set_sequence_profile(
            editor.state.sequence.profile.model_copy(
                update={
                    "width": 1080,
                    "height": 1920,
                    "color_mode": ColorMode.HDR10_BT2020_PQ,
                    "bit_depth": 10,
                }
            )
        )
        resolution_after = repository.frame_clock.capture_main_frame_clock(sequence_id)
        assert resolution_after.assets[0].proxy_path is None
        assert resolution_after.assets[0].sdr_preview_proxy_path is None
        editor.undo()
        assert repository.frame_clock.capture_main_frame_clock(sequence_id) == resolution_before
        editor.redo()
        assert repository.frame_clock.capture_main_frame_clock(sequence_id) == resolution_after
