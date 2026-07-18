from __future__ import annotations

from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.subtitle_file import SubtitleFile
from mediaflow.domain.timeline import Clip
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt.compiler import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


def _build_subtitle_components(
    repository: ProjectRepository,
    history: ProjectEditHistory | None = None,
) -> tuple[SubtitleAcquisitionService, SubtitleEditingService, SubtitlePublicationService]:
    publication = SubtitlePublicationService(repository)
    return (
        SubtitleAcquisitionService(repository, publication),
        SubtitleEditingService(repository, publication, history),
        publication,
    )


def test_srt_import_edit_place_compile_and_export_use_one_document_boundary(tmp_path: Path) -> None:
    source = tmp_path / "interview.en.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello world\n\n"
        "2\n00:00:01,200 --> 00:00:03,000\nThis is a subtitle editing test.\n",
        encoding="utf-8-sig",
    )

    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, editing, publication = _build_subtitle_components(repository)
        document = acquisition.import_subtitle_file(
            source,
            AssetService(repository, MediaProbe()),
        )
        asset = repository.get_asset(document.asset_id)
        assert asset.kind == AssetKind.SUBTITLE
        assert document.language == "en"
        assert [item.text for item in repository.list_subtitle_segments(document.id)] == [
            "Hello world",
            "This is a subtitle editing test.",
        ]

        first, second = repository.list_subtitle_segments(document.id)
        editing.update_segment(
            document.id,
            first.id,
            start_frame=0,
            end_frame=36,
            text="Hello MediaFlow",
        )
        split_first, split_second = editing.split_segment(document.id, second.id)
        merged = editing.merge_segments(document.id, [split_first.id, split_second.id])
        assert merged.text == "This is a subtitle editing test."
        assert editing.replace_all(document.id, "MediaFlow", "MediaFlow Pro") == 1

        project = repository.get_project()
        subtitle_track = next(
            track
            for track in repository.load_timeline(project.main_sequence_id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        placements = repository.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )
        assert len(placements) == 2

        compiled = TimelineCompiler(repository).compile(
            repository.load_timeline(project.main_sequence_id),
            subtitle_track_id=subtitle_track.id,
        )
        assert "Hello MediaFlow Pro" in compiled.xml
        assert "This is a subtitle editing test." in compiled.xml

        exported = publication.write_document_srt(document.id, tmp_path / "exported.srt")
        cues = SubtitleFile.read(exported, fps_numerator=30, fps_denominator=1)
        assert [cue.text for cue in cues] == [
            "Hello MediaFlow Pro",
            "This is a subtitle editing test.",
        ]


def test_timeline_and_subtitle_edits_share_one_chronological_undo_history(tmp_path: Path) -> None:
    source = tmp_path / "history.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nBefore\n",
        encoding="utf-8-sig",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        history = ProjectEditHistory()
        acquisition, editing, _publication = _build_subtitle_components(repository, history)
        document = acquisition.import_subtitle_file(source, AssetService(repository, MediaProbe()))
        project = repository.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id, history)
        editor.add_marker(10, "Timeline edit")
        segment = repository.list_subtitle_segments(document.id)[0]
        editing.update_segment(
            document.id,
            segment.id,
            start_frame=segment.start_frame,
            end_frame=segment.end_frame,
            text="After",
        )

        editor.undo()
        assert repository.list_subtitle_segments(document.id)[0].text == "Before"
        assert len(repository.load_timeline(project.main_sequence_id).markers) == 1
        editor.undo()
        assert repository.load_timeline(project.main_sequence_id).markers == []

        editor.redo()
        editor.redo()
        assert len(repository.load_timeline(project.main_sequence_id).markers) == 1
        assert repository.list_subtitle_segments(document.id)[0].text == "After"


def test_imported_subtitle_auto_imports_adjacent_media_and_follows_its_clip(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "interview.mp4"
    subtitle_path = tmp_path / "interview_ZH-CN.srt"
    generate_real_media(video_path, RuntimePaths.discover(), width=320, height=180)
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n同名媒体关联\n",
        encoding="utf-8",
    )

    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        publication = SubtitlePublicationService(repository)
        document = SubtitleAcquisitionService(repository, publication).import_subtitle_file(
            subtitle_path,
            AssetService(repository, MediaProbe()),
        )
        assert repository.get_asset(document.asset_id).kind == AssetKind.SUBTITLE
        assert document.media_asset_id is not None
        media = repository.get_asset(document.media_asset_id)
        assert media.kind == AssetKind.VIDEO
        assert repository.resolve_asset_path(media) == video_path.resolve()

        project = repository.get_project()
        state = repository.load_timeline(project.main_sequence_id)
        video_track = next(item for item in state.tracks if item.kind == TrackKind.VIDEO)
        subtitle_track = next(item for item in state.tracks if item.kind == TrackKind.SUBTITLE)
        clip = Clip(
            track_id=video_track.id,
            asset_id=media.id,
            timeline_start=45,
            source_in=0,
            duration=60,
        )
        state.clips.append(clip)
        repository.save_timeline(state)
        placements = repository.place_subtitle_document(document.id, subtitle_track.id)
        assert [(item.clip_id, item.start_frame, item.end_frame) for item in placements] == [
            (clip.id, 45, 75)
        ]


def test_smart_split_and_delete_preserve_existing_placement_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.zh.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n这是一个足够长的字幕句子，应该能够在标点处自动拆分。\n",
        encoding="utf-8",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, editing, _publication = _build_subtitle_components(repository)
        document = acquisition.import_subtitle_file(
            source,
            AssetService(repository, MediaProbe()),
        )
        project = repository.get_project()
        track = next(
            item
            for item in repository.load_timeline(project.main_sequence_id).tracks
            if item.kind == TrackKind.SUBTITLE
        )
        original = repository.place_subtitle_document(
            document.id,
            track.id,
            follow_clips=False,
        )[0]
        assert editing.smart_split_document(document.id, text_limit=12) == 1
        split_segments = repository.list_subtitle_segments(document.id)
        split_placements = repository.list_subtitle_placements(track.id)
        assert len(split_segments) == len(split_placements) == 2
        first_placement = next(item for item in split_placements if item.segment_id == split_segments[0].id)
        assert first_placement.id == original.id

        assert editing.delete_segments(document.id, [split_segments[1].id]) == 1
        assert [item.segment_id for item in repository.list_subtitle_placements(track.id)] == [
            split_segments[0].id
        ]


def test_overlap_fix_and_clipboard_replacement_persist_through_srt_boundary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "overlap.en.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n2\n00:00:00,800 --> 00:00:01,800\nSecond\n",
        encoding="utf-8",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, editing, publication = _build_subtitle_components(repository)
        document = acquisition.import_subtitle_file(
            source,
            AssetService(repository, MediaProbe()),
        )
        before = repository.list_subtitle_segments(document.id)
        assert editing.fix_overlaps(document.id) == 1
        fixed = repository.list_subtitle_segments(document.id)
        assert (fixed[1].start_frame, fixed[1].end_frame) == (32, 62)
        assert fixed[1].end_frame - fixed[1].start_frame == (before[1].end_frame - before[1].start_frame)

        copied = editing.selected_segments_srt(
            document.id,
            [fixed[0].id, fixed[1].id],
        )
        assert "00:00:01,067 --> 00:00:02,067" in copied
        assert (
            editing.replace_selected_texts(
                document.id,
                [fixed[0].id, fixed[1].id],
                "1\n00:00:00,000 --> 00:00:00,500\nReplaced one\n\n"
                "2\n00:00:00,500 --> 00:00:01,000\nReplaced two\n",
            )
            == 2
        )
        assert [segment.text for segment in repository.list_subtitle_segments(document.id)] == [
            "Replaced one",
            "Replaced two",
        ]
        matches = editing.find_matches(document.id, "replaced")
        assert len(matches) == 2
        editing.replace_match(
            document.id,
            str(matches[0]["segmentId"]),
            int(matches[0]["start"]),
            int(matches[0]["end"]),
            "replaced",
            "Changed",
        )
        assert [segment.text for segment in repository.list_subtitle_segments(document.id)] == [
            "Changed one",
            "Replaced two",
        ]
        with pytest.raises(ValueError, match="失效"):
            editing.replace_match(
                document.id,
                str(matches[0]["segmentId"]),
                int(matches[0]["start"]),
                int(matches[0]["end"]),
                "replaced",
                "Again",
            )
        generated = publication.write_document_srt(document.id)
        assert generated.is_relative_to(repository.project_dir / "generated" / "subtitles")
        assert "Replaced two" in generated.read_text(encoding="utf-8-sig")


def test_webvtt_ass_and_ssa_import_share_the_same_subtitle_document_boundary(
    tmp_path: Path,
) -> None:
    vtt = tmp_path / "captions.en.vtt"
    vtt.write_text(
        "WEBVTT\n\nintro\n00:00.100 --> 00:01.000 align:start\n<b>WebVTT line</b>\n",
        encoding="utf-8",
    )
    ass = tmp_path / "captions.zh.ass"
    ass.write_text(
        "[Script Info]\nTitle: Test\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.20,0:00:02.50,Default,,0,0,0,,{\\i1}ASS 第一行\\N第二行\n",
        encoding="utf-8-sig",
    )
    ssa = tmp_path / "captions.ja.ssa"
    ssa.write_text(
        "[Events]\nFormat: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: Marked=0,0:00:00.50,0:00:01.50,Default,,0,0,0,,SSA text\n",
        encoding="utf-8",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, _editing, publication = _build_subtitle_components(repository)
        assets = AssetService(repository, MediaProbe())
        documents = [acquisition.import_subtitle_file(path, assets) for path in (vtt, ass, ssa)]
        assert [document.language for document in documents] == ["en", "zh_CN", "ja"]
        assert [repository.list_subtitle_segments(document.id)[0].text for document in documents] == [
            "WebVTT line",
            "ASS 第一行\n第二行",
            "SSA text",
        ]
        outputs = [publication.write_document_srt(document.id) for document in documents]
        assert all(output.suffix == ".srt" and output.is_file() for output in outputs)
        assert all(
            output.is_relative_to(repository.project_dir / "generated" / "subtitles")
            for output in outputs
        )
