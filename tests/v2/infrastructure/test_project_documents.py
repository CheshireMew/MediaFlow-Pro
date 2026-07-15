from pathlib import Path

import pytest

from mediaflow.domain.enums import AssetKind, AudioEffectKind, TrackKind
from mediaflow.domain.models import (
    AudioEffect,
    HighlightCandidate,
    SubtitleDocument,
    SubtitleSegment,
)
from mediaflow.infrastructure.project_repository import ProjectRepository


def test_source_translation_and_sequence_placement_keep_stable_links(tmp_path: Path) -> None:
    source_file = tmp_path / "source.mp4"
    source_file.write_bytes(b"media")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.import_external_asset(source_file, AssetKind.VIDEO)
        project = repository.get_project()
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
        repository.create_subtitle_document(source_document, source_segments)

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
        repository.create_subtitle_document(translation, translated_segments)

        subtitle_track = next(
            track
            for track in repository.load_timeline(project.main_sequence_id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        placements = repository.place_subtitle_document(translation.id, subtitle_track.id)

        assert [item.source_segment_id for item in repository.list_subtitle_segments(translation.id)] == [
            source_segments[0].id,
            source_segments[1].id,
        ]
        assert [item.start_frame for item in placements] == [0, 31]
        assert len(repository.list_subtitle_placements(subtitle_track.id)) == 2

        overridden = repository.update_subtitle_placement_text(placements[0].id, "您好")
        assert overridden.text_override == "您好"
        assert repository.list_subtitle_segments(translation.id)[0].text == "你好"

        updated_segment = repository.apply_subtitle_placement_to_document(
            placements[0].id,
            "你好呀",
        )
        assert updated_segment.text == "你好呀"
        assert repository.list_subtitle_placements(subtitle_track.id)[0].text_override is None


def test_subtitle_placement_can_be_clipped_and_offset_for_a_short_sequence(
    tmp_path: Path,
) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"media")
    with ProjectRepository.create(tmp_path / "ShortProject", "ShortProject") as repository:
        asset = repository.import_external_asset(media, AssetKind.VIDEO)
        project = repository.get_project()
        document = SubtitleDocument(project_id=project.id, asset_id=asset.id, language="en")
        segments = [
            SubtitleSegment(document_id=document.id, start_frame=0, end_frame=30, text="before"),
            SubtitleSegment(document_id=document.id, start_frame=40, end_frame=70, text="inside"),
            SubtitleSegment(document_id=document.id, start_frame=80, end_frame=120, text="after"),
        ]
        repository.create_subtitle_document(document, segments)
        short = repository.create_short_sequence("Short")
        subtitle_track = next(
            track for track in repository.load_timeline(short.id).tracks if track.kind == TrackKind.SUBTITLE
        )

        placements = repository.place_subtitle_document(
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
        sequence_id = repository.get_project().main_sequence_id
        buses = repository.list_audio_buses(sequence_id)
        master, dialogue = buses[0], buses[1]
        with pytest.raises(ValueError, match="cycle"):
            repository.save_audio_bus(master.model_copy(update={"parent_bus_id": dialogue.id}))

        repository.save_audio_effect(
            AudioEffect(
                bus_id=dialogue.id,
                kind=AudioEffectKind.COMPRESSOR,
                position=2,
                parameters={"threshold_db": -18},
            )
        )
        repository.save_audio_effect(
            AudioEffect(
                bus_id=dialogue.id,
                kind=AudioEffectKind.PARAMETRIC_EQ,
                position=1,
                parameters={"low_db": 2},
            )
        )
        assert [effect.kind for effect in repository.list_audio_effects(dialogue.id)] == [
            AudioEffectKind.PARAMETRIC_EQ,
            AudioEffectKind.COMPRESSOR,
        ]
        effects = list(reversed(repository.list_audio_effects(dialogue.id)))
        reordered = [effect.model_copy(update={"position": index}) for index, effect in enumerate(effects)]
        repository.save_audio_effect_chain(dialogue.id, reordered)
        assert [effect.kind for effect in repository.list_audio_effects(dialogue.id)] == [
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
        asset = repository.import_external_asset(source_file, AssetKind.VIDEO)
        project = repository.get_project()
        candidate = HighlightCandidate(
            project_id=project.id,
            asset_id=asset.id,
            start_frame=120,
            end_frame=420,
            title="核心观点",
            reason="信息密度高且可以独立成段",
            score=0.93,
        )
        repository.save_highlights([candidate])
        persisted = repository.list_highlights(asset.id)
        assert persisted == [candidate]
