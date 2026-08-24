from pathlib import Path

import pytest

from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.translation_comparison import TranslationComparisonService
from mediaflow.domain.audio import AudioEffect
from mediaflow.domain.enums import AssetKind, AudioEffectKind, ClipMediaKind, TrackKind
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.settings import GlossaryTermSettings, LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.timeline import Clip
from mediaflow.infrastructure.project_repository import ProjectRepository


def test_source_translation_and_sequence_placement_keep_stable_links(tmp_path: Path) -> None:
    source_file = tmp_path / "source.mp4"
    source_file.write_bytes(b"media")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.assets.import_external_asset(source_file, AssetKind.VIDEO)
        project = repository.projects.get_project()
        source_document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="en",
        )
        source_segments = [
            SubtitleSegment(
                document_id=source_document.id,
                start_frame=0,
                end_frame=30,
                text="Hello",
            ),
            SubtitleSegment(
                document_id=source_document.id,
                start_frame=31,
                end_frame=60,
                text="World",
            ),
        ]
        repository.subtitles.create_subtitle_document(source_document, source_segments)

        translation = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="zh_CN",
            source_document_id=source_document.id,
            is_source=False,
        )
        translated_segments = [
            SubtitleSegment(
                document_id=translation.id,
                source_segment_id=segment.id,
                start_frame=segment.start_frame,
                end_frame=segment.end_frame,
                text=text,
            )
            for segment, text in zip(source_segments, ["你好", "世界"], strict=True)
        ]
        repository.subtitles.create_subtitle_document(translation, translated_segments)

        subtitle_track = TimelineEditor(repository, project.main_sequence_id).add_track(
            TrackKind.SUBTITLE
        )
        placements = repository.subtitles.place_subtitle_document(translation.id, subtitle_track.id)

        assert [
            item.source_segment_id
            for item in repository.subtitles.list_subtitle_segments(translation.id)
        ] == [
            source_segments[0].id,
            source_segments[1].id,
        ]
        assert [item.start_frame for item in placements] == [0, 31]
        assert len(repository.subtitles.list_subtitle_placements(subtitle_track.id)) == 2

        overridden = repository.subtitles.update_subtitle_placement_text(placements[0].id, "您好")
        assert overridden.text_override == "您好"
        assert repository.subtitles.list_subtitle_segments(translation.id)[0].text == "你好"

        updated_segment = repository.subtitles.apply_subtitle_placement_to_document(
            placements[0].id,
            "你好呀",
        )
        assert updated_segment.text == "你好呀"
        assert repository.subtitles.list_subtitle_placements(subtitle_track.id)[0].text_override is None


def test_translation_comparison_is_one_typed_project_query(tmp_path: Path) -> None:
    source_file = tmp_path / "translation-source.mp4"
    source_file.write_bytes(b"media")
    with ProjectRepository.create(tmp_path / "Translation", "Translation") as repository:
        asset = repository.assets.import_external_asset(source_file, AssetKind.VIDEO)
        project = repository.projects.get_project()
        source = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="en",
        )
        source_segments = [
            SubtitleSegment(
                id="source-one",
                document_id=source.id,
                start_frame=0,
                end_frame=20,
                text="MediaFlow editor",
            ),
            SubtitleSegment(
                id="source-two",
                document_id=source.id,
                start_frame=20,
                end_frame=40,
                text="Second line",
            ),
        ]
        repository.subtitles.create_subtitle_document(source, source_segments)
        target = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="zh_CN",
            source_document_id=source.id,
            is_source=False,
        )
        repository.subtitles.create_subtitle_document(
            target,
            [
                SubtitleSegment(
                    document_id=target.id,
                    source_segment_id="source-one",
                    start_frame=0,
                    end_frame=20,
                    text="媒体流编辑器",
                )
            ],
        )

        comparison = TranslationComparisonService(repository).compare(
            source.id,
            "zh_CN",
            [GlossaryTermSettings(source="MediaFlow", target="媒体流")],
        )

        assert comparison.source_document_id == source.id
        assert comparison.target_document_id == target.id
        assert comparison.glossary_hit_count == 1
        assert [row.status for row in comparison.rows] == ["translated", "missing"]
        assert comparison.rows[0].source_segment_ids == ["source-one"]
        assert comparison.rows[1].source_segment_ids == ["source-two"]


def test_subtitle_placement_can_be_clipped_and_offset_for_a_short_sequence(
    tmp_path: Path,
) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"media")
    with ProjectRepository.create(tmp_path / "ShortProject", "ShortProject") as repository:
        asset = repository.assets.import_external_asset(media, AssetKind.VIDEO)
        project = repository.projects.get_project()
        document = SubtitleDocument(project_id=project.id, asset_id=asset.id, language="en")
        segments = [
            SubtitleSegment(document_id=document.id, start_frame=0, end_frame=30, text="before"),
            SubtitleSegment(document_id=document.id, start_frame=40, end_frame=70, text="inside"),
            SubtitleSegment(document_id=document.id, start_frame=80, end_frame=120, text="after"),
        ]
        repository.subtitles.create_subtitle_document(document, segments)
        short = repository.sequences.create_short_sequence("Short")
        subtitle_track = TimelineEditor(repository, short.id).add_track(TrackKind.SUBTITLE)

        placements = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            offset_frames=-30,
            source_start_frame=30,
            source_end_frame=90,
        )

        assert [(item.start_frame, item.end_frame) for item in placements] == [
            (10, 40),
            (50, 60),
        ]


def test_audio_bus_rejects_cycle_and_effect_chain_is_ordered(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        sequence_id = repository.projects.get_project().main_sequence_id
        buses = repository.audio.list_audio_buses(sequence_id)
        master, dialogue = buses[0], buses[1]
        with pytest.raises(ValueError, match="cycle"):
            repository.audio.save_audio_bus(master.model_copy(update={"parent_bus_id": dialogue.id}))

        repository.audio.save_audio_effect(
            AudioEffect(
                bus_id=dialogue.id,
                kind=AudioEffectKind.COMPRESSOR,
                position=2,
                parameters={"threshold_db": -18},
            )
        )
        repository.audio.save_audio_effect(
            AudioEffect(
                bus_id=dialogue.id,
                kind=AudioEffectKind.PARAMETRIC_EQ,
                position=1,
                parameters={"low_db": 2},
            )
        )
        assert [effect.kind for effect in repository.audio.list_audio_effects(dialogue.id)] == [
            AudioEffectKind.PARAMETRIC_EQ,
            AudioEffectKind.COMPRESSOR,
        ]
        effects = list(reversed(repository.audio.list_audio_effects(dialogue.id)))
        reordered = [effect.model_copy(update={"position": index}) for index, effect in enumerate(effects)]
        repository.audio.save_audio_effect_chain(dialogue.id, reordered)
        assert [effect.kind for effect in repository.audio.list_audio_effects(dialogue.id)] == [
            AudioEffectKind.COMPRESSOR,
            AudioEffectKind.PARAMETRIC_EQ,
        ]

        with pytest.raises(ValueError):
            AudioEffect(
                bus_id=dialogue.id,
                kind=AudioEffectKind.LIMITER,
                position=3,
                parameters={"ceiling_db": 3.0},
            )


def test_highlight_candidates_are_project_data_not_task_only_output(tmp_path: Path) -> None:
    source_file = tmp_path / "source.mp4"
    source_file.write_bytes(b"media")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.assets.import_external_asset(source_file, AssetKind.VIDEO)
        project = repository.projects.get_project()
        candidate = HighlightCandidate(
            project_id=project.id,
            asset_id=asset.id,
            start_frame=120,
            end_frame=420,
            title="核心观点",
            reason="信息密度高且可以独立成段",
            score=0.93,
        )
        repository.highlights.save_highlights([candidate])
        persisted = repository.highlights.list_highlights(asset.id)
        assert persisted == [candidate]


def test_manual_highlight_selection_edit_and_short_draft_share_one_persisted_candidate(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "source.mp4"
    source_file.write_bytes(b"media")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.assets.import_external_asset(source_file, AssetKind.VIDEO)
        project = repository.projects.get_project()
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="zh_CN",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=100,
            end_frame=500,
            text="候选字幕",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        service = HighlightService(repository)
        candidate = service.add_manual_candidate(
            asset.id,
            start_frame=120,
            end_frame=420,
            title="手动候选",
            document_id=document.id,
        )
        assert service.set_selected(candidate.id, False).selected is False
        assert service.set_selected(candidate.id, True).selected is True
        updated = service.update_candidate(
            candidate.id,
            start_frame=150,
            end_frame=390,
            title="修改后的候选",
        )
        first = service.create_short_sequence(updated.id)
        second = service.create_short_sequence(updated.id)
        persisted = repository.highlights.list_highlights(asset.id)[0]
        timeline = repository.timeline.load_timeline(first.id)
        subtitle_track = next(track for track in timeline.tracks if track.kind == TrackKind.SUBTITLE)

        assert first.id == second.id == persisted.sequence_id
        assert len(repository.sequences.list_sequences()) == 2
        assert [(clip.source_in, clip.duration) for clip in timeline.clips] == [(150, 240)]
        assert repository.subtitles.list_subtitle_placements(subtitle_track.id)

        service.update_candidate(
            candidate.id,
            start_frame=180,
            end_frame=360,
            title="再次修改",
        )
        synced = repository.timeline.load_timeline(first.id)
        assert [(clip.source_in, clip.duration) for clip in synced.clips] == [(180, 180)]


def test_sequence_transcript_highlight_copies_and_resyncs_the_multitrack_timeline(
    tmp_path: Path,
) -> None:
    first_video = tmp_path / "first.mp4"
    second_video = tmp_path / "second.mp4"
    mixed_audio = tmp_path / "timeline-mix.wav"
    for source in (first_video, second_video, mixed_audio):
        source.write_bytes(b"media")

    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        first_asset = repository.assets.import_external_asset(first_video, AssetKind.VIDEO)
        second_asset = repository.assets.import_external_asset(second_video, AssetKind.VIDEO)
        audio_asset = repository.assets.import_external_asset(mixed_audio, AssetKind.AUDIO)
        project = repository.projects.get_project()
        sequence_id = project.main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        audio_track = editor.add_track(TrackKind.AUDIO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        state = editor.state
        state.clips = [
            Clip(
                track_id=video_track.id,
                asset_id=first_asset.id,
                timeline_start=0,
                source_in=10,
                duration=60,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            ),
            Clip(
                track_id=video_track.id,
                asset_id=second_asset.id,
                timeline_start=60,
                source_in=5,
                duration=40,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            ),
            Clip(
                track_id=audio_track.id,
                asset_id=audio_asset.id,
                timeline_start=0,
                source_in=0,
                duration=100,
                media_kind=ClipMediaKind.AUDIO_ONLY,
            ),
        ]
        repository.timeline.save_timeline(state)
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=audio_asset.id,
            sequence_id=sequence_id,
            language="zh_CN",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=20,
            end_frame=80,
            text="来自整个时间轴的字幕",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )

        class TimelineHighlightClient:
            def __init__(self, _settings) -> None:
                pass

            def complete_json(self, **_kwargs):
                return {
                    "candidates": [
                        {
                            "start_id": segment.id,
                            "end_id": segment.id,
                            "title": "时间轴高光",
                            "reason": "覆盖多轨内容",
                            "score": 0.9,
                        }
                    ]
                }

        service = HighlightService(repository, TimelineHighlightClient)
        candidate = service.analyze_document(
            document.id,
            provider=LlmProviderSettings(
                name="Test",
                base_url="http://127.0.0.1",
                api_key="test",
                model="test",
                enabled=True,
            ),
        )
        assert [item.asset_id for item in candidate] == [first_asset.id]
        short = service.create_short_sequence(candidate[0].id)
        short_state = repository.timeline.load_timeline(short.id)
        short_subtitle_track = next(
            track for track in short_state.tracks if track.kind == TrackKind.SUBTITLE
        )

        assert len(repository.sequences.list_sequences()) == 2
        assert {
            clip.asset_id: (clip.timeline_start, clip.source_in, clip.duration)
            for clip in short_state.clips
        } == {
            first_asset.id: (0, 30, 40),
            second_asset.id: (40, 5, 20),
            audio_asset.id: (0, 20, 60),
        }
        assert [
            (placement.start_frame, placement.end_frame)
            for placement in repository.subtitles.list_subtitle_placements(short_subtitle_track.id)
        ] == [(0, 60)]

        service.update_candidate(
            candidate[0].id,
            start_frame=30,
            end_frame=70,
            title="更新后的时间轴高光",
        )
        synced = repository.timeline.load_timeline(short.id)
        assert {
            clip.asset_id: (clip.timeline_start, clip.source_in, clip.duration)
            for clip in synced.clips
        } == {
            first_asset.id: (0, 40, 30),
            second_asset.id: (30, 5, 10),
            audio_asset.id: (0, 30, 40),
        }
