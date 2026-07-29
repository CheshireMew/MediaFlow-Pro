from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.transcript_editing import TranscriptEditingService
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.asr import AsrResult, AsrSegment, AsrWord
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.sequence_audio import project_dialogue_transcript
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.domain.storage_names import utf16_units
from mediaflow.domain.subtitle_file import SubtitleFile
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.transcript_edits import (
    TranscriptEditRequest,
    TranscriptEditSelection,
)
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt.compiler import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


class _MemoryTranslationCache:
    def __init__(self) -> None:
        self._values: dict[str, list[str]] = {}

    def get(self, request: dict) -> list[str] | None:
        return self._values.get(repr(request))

    def put(self, request: dict, texts: list[str]) -> None:
        self._values[repr(request)] = list(texts)


class _TranslationClient:
    def complete_json(self, *, system: str, payload: dict) -> dict:
        del system
        return {
            "segments": [
                {
                    "id": item["id"],
                    "text": f"译文：{item['source_text']}",
                }
                for item in payload["segments"]
            ]
        }


def _translation_client(_provider: LlmProviderSettings) -> _TranslationClient:
    return _TranslationClient()


class _FailingTranslationClient:
    def complete_json(self, *, system: str, payload: dict) -> dict:
        del system, payload
        raise ConnectionError("provider unavailable")


def _failing_translation_client(
    _provider: LlmProviderSettings,
) -> _FailingTranslationClient:
    return _FailingTranslationClient()


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


def _fail_outermost_transaction_commit(
    repository: ProjectRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_transaction = repository.transaction
    depth = 0

    @contextmanager
    def failing_transaction() -> Iterator[object]:
        nonlocal depth
        outermost = depth == 0
        depth += 1
        try:
            with original_transaction() as connection:
                yield connection
                if outermost:
                    raise RuntimeError("injected database commit failure")
        finally:
            depth -= 1

    monkeypatch.setattr(repository, "transaction", failing_transaction)


def test_translation_operation_retry_reuses_one_persisted_document(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        publication = SubtitlePublicationService(repository)
        project = repository.catalog.get_project()
        source_path = tmp_path / "translation-source.srt"
        source_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
            encoding="utf-8-sig",
        )
        asset = repository.catalog.import_external_asset(source_path, AssetKind.SUBTITLE)
        source = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
        )
        source_segment = SubtitleSegment(
            document_id=source.id,
            start_frame=0,
            end_frame=30,
            text="Hello",
        )
        repository.subtitles.create_subtitle_document(source, [source_segment])
        service = TranslationService(
            repository,
            _translation_client,
            _MemoryTranslationCache(),
            publication,
        )
        provider = LlmProviderSettings(
            name="test",
            base_url="https://example.invalid",
            model="test-model",
        )

        first = service.translate_document(
            source.id,
            target_language="zh_CN",
            provider=provider,
            operation_id="task-translation-retry",
        )
        second = service.translate_document(
            source.id,
            target_language="zh_CN",
            provider=provider,
            operation_id="task-translation-retry",
        )

        assert first.id == second.id == "task-translation-retry"
        targets = [
            item
            for item in repository.subtitles.list_subtitle_documents(sequence_id=project.main_sequence_id)
            if not item.is_source
        ]
        assert [item.id for item in targets] == ["task-translation-retry"]
        assert [item.text for item in repository.subtitles.list_subtitle_segments(second.id)] == [
            "译文：Hello"
        ]
        outputs = list(
            (
                repository.project_dir
                / "generated"
                / "subtitles"
            ).glob("sub-*.srt")
        )
        assert len(outputs) == 1
        assert not (
            repository.project_dir
            / "generated"
            / "subtitles"
            / asset.id
        ).exists()
        assert "译文：Hello" in outputs[0].read_text(encoding="utf-8-sig")


def test_translation_provider_failure_does_not_persist_source_text_as_success(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(tmp_path / "Failed Translation", "Failed Translation") as repository:
        publication = SubtitlePublicationService(repository)
        project = repository.catalog.get_project()
        source_path = tmp_path / "failed-translation-source.srt"
        source_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello world\n",
            encoding="utf-8-sig",
        )
        asset = repository.catalog.import_external_asset(source_path, AssetKind.SUBTITLE)
        source = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
        )
        source_segment = SubtitleSegment(
            document_id=source.id,
            start_frame=0,
            end_frame=30,
            text="Hello world",
        )
        repository.subtitles.create_subtitle_document(source, [source_segment])
        service = TranslationService(
            repository,
            _failing_translation_client,
            _MemoryTranslationCache(),
            publication,
        )
        provider = LlmProviderSettings(
            name="offline",
            base_url="https://example.invalid",
            model="test-model",
        )

        with pytest.raises(RuntimeError, match="Translation failed"):
            service.translate_document(
                source.id,
                target_language="zh_CN",
                provider=provider,
                operation_id="failed-translation-task",
            )

        assert repository.subtitles.list_subtitle_documents(
            sequence_id=project.main_sequence_id
        ) == [source]
        assert not list((repository.project_dir / "generated" / "subtitles").rglob("*.srt"))


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
        asset = repository.catalog.get_asset(document.asset_id)
        assert asset.kind == AssetKind.SUBTITLE
        assert document.language == "en"
        assert [item.text for item in repository.subtitles.list_subtitle_segments(document.id)] == [
            "Hello world",
            "This is a subtitle editing test.",
        ]

        first, second = repository.subtitles.list_subtitle_segments(document.id)
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

        project = repository.catalog.get_project()
        subtitle_track = TimelineEditor(repository, project.main_sequence_id).add_track(
            TrackKind.SUBTITLE
        )
        placements = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )
        assert len(placements) == 2

        compiled = TimelineCompiler(repository).compile(
            repository.timeline.load_timeline(project.main_sequence_id),
            subtitle_track_id=subtitle_track.id,
        )
        assert "Hello MediaFlow Pro" in compiled.xml
        assert "This is a subtitle editing test." in compiled.xml
        preview_graph = TimelineCompiler(repository).compile(
            repository.timeline.load_timeline(project.main_sequence_id)
        )
        assert 'mlt_service">dynamictext' not in preview_graph.xml

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
        project = repository.catalog.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id, history)
        editor.add_marker(10, "Timeline edit")
        segment = repository.subtitles.list_subtitle_segments(document.id)[0]
        editing.update_segment(
            document.id,
            segment.id,
            start_frame=segment.start_frame,
            end_frame=segment.end_frame,
            text="After",
        )

        editor.undo()
        assert repository.subtitles.list_subtitle_segments(document.id)[0].text == "Before"
        assert len(repository.timeline.load_timeline(project.main_sequence_id).markers) == 1
        editor.undo()
        assert repository.timeline.load_timeline(project.main_sequence_id).markers == []

        editor.redo()
        editor.redo()
        assert len(repository.timeline.load_timeline(project.main_sequence_id).markers) == 1
        assert repository.subtitles.list_subtitle_segments(document.id)[0].text == "After"


def test_word_delete_updates_timeline_transcript_srt_and_shared_undo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "interview.mp4"
    source.write_bytes(b"timeline-source")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        history = ProjectEditHistory()
        project = repository.catalog.get_project()
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        editor = TimelineEditor(repository, project.main_sequence_id, history)
        video_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
            purpose="sequence_transcript",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=0,
            end_frame=90,
            text="one two three",
        )
        words = [
            SubtitleWord(
                segment_id=segment.id,
                position=position,
                start_frame=position * 30,
                end_frame=(position + 1) * 30,
                text=text,
                timing_source="estimated" if position == 0 else "recognized",
            )
            for position, text in enumerate(("one", "two", "three"))
        ]
        repository.subtitles.create_subtitle_document(document, [segment], words)
        publication = SubtitlePublicationService(repository)
        service = TranscriptEditingService(repository, publication, history)

        with pytest.raises(ValueError, match="估算词时间不能用于词级剪辑"):
            service.preview_plan(
                TranscriptEditRequest(
                    sequence_id=project.main_sequence_id,
                    document_id=document.id,
                    expected_content_revision=repository.content_revision(),
                    selections=[
                        TranscriptEditSelection(
                            kind="words",
                            ids=[words[0].id],
                            reason="Unsafe estimated boundary",
                        )
                    ],
                ),
                editor,
            )
        plan = service.preview_plan(
            TranscriptEditRequest(
                sequence_id=project.main_sequence_id,
                document_id=document.id,
                expected_content_revision=repository.content_revision(),
                selections=[
                    TranscriptEditSelection(
                        kind="words",
                        ids=[words[1].id],
                        reason="Remove repeated filler",
                    )
                ],
            ),
            editor,
        )
        assert plan.impact.removed_duration_frames == 30
        assert plan.impact.after_duration_frames == 60
        revision_before_failure = repository.content_revision()
        history_before_failure = history.checkpoint()
        versions_before_failure = repository.records.list_project_versions()
        original_push = history.push

        def fail_history_push(_command) -> None:
            raise OSError("injected history publication failure")

        monkeypatch.setattr(history, "push", fail_history_push)
        with pytest.raises(
            OSError,
            match="history publication failure",
        ):
            service.apply_plan(plan, editor)

        assert repository.content_revision() == revision_before_failure
        assert repository.timeline.load_timeline(
            project.main_sequence_id
        ).clips[0].duration == 90
        assert repository.subtitles.list_subtitle_segments(
            document.id
        ) == [segment]
        assert repository.subtitles.list_subtitle_words(
            document.id
        ) == words
        assert history.checkpoint() == history_before_failure
        assert (
            repository.records.list_project_versions()
            == versions_before_failure
        )
        assert not list(
            (
                repository.project_dir
                / "generated"
                / "subtitles"
            ).rglob("*.srt")
        )

        monkeypatch.setattr(history, "push", original_push)
        result = service.apply_plan(plan, editor)
        assert result.removed_word_count == 1
        assert result.removed_segment_count == 0
        recovery = repository.project_dir / result.recovery_version.snapshot_path
        assert recovery.is_file()
        persisted = repository.timeline.load_timeline(project.main_sequence_id)
        assert [
            (clip.timeline_start, clip.source_in, clip.duration)
            for clip in persisted.clips
        ] == [(0, 0, 30), (30, 60, 30)]
        edited_segment = repository.subtitles.list_subtitle_segments(document.id)[0]
        assert (edited_segment.start_frame, edited_segment.end_frame, edited_segment.text) == (
            0,
            60,
            "one three",
        )
        edited_words = repository.subtitles.list_subtitle_words(document.id)
        assert [(word.text, word.start_frame, word.excluded) for word in edited_words] == [
            ("one", 0, False),
            ("two", 30, True),
            ("three", 30, False),
        ]
        generated = list((repository.project_dir / "generated" / "subtitles").rglob("*.srt"))
        assert generated and "one three" in generated[0].read_text(encoding="utf-8-sig")

        editor.undo()
        restored = repository.timeline.load_timeline(project.main_sequence_id)
        assert [(clip.timeline_start, clip.source_in, clip.duration) for clip in restored.clips] == [
            (0, 0, 90)
        ]
        assert repository.subtitles.list_subtitle_segments(document.id)[0].text == "one two three"
        assert not any(word.excluded for word in repository.subtitles.list_subtitle_words(document.id))

        editor.redo()
        assert repository.subtitles.list_subtitle_segments(document.id)[0].text == "one three"
        assert repository.subtitles.list_subtitle_words(document.id)[1].excluded is True


def test_short_sequence_transcript_persists_in_main_clock_and_publishes_real_time(
    tmp_path: Path,
) -> None:
    main_profile = ProjectProfile(fps_numerator=24, fps_denominator=1)
    short_profile = main_profile.model_copy(
        update={
            "width": 1080,
            "height": 1920,
            "fps_numerator": 60,
        }
    )
    with ProjectRepository.create(
        tmp_path / "Short Transcript Clock",
        "Short Transcript Clock",
        main_profile,
    ) as repository:
        project = repository.catalog.get_project()
        short = repository.catalog.create_short_sequence(
            "60 fps short",
            short_profile,
        )
        media_source = tmp_path / "short-transcript.mp4"
        media_source.write_bytes(b"timeline-source")
        media_asset = repository.catalog.import_external_asset(
            media_source,
            AssetKind.VIDEO,
        )
        editor = TimelineEditor(repository, short.id)
        video_track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=media_asset.id,
            timeline_start=0,
            source_in=0,
            duration=180,
        )
        projected = project_dialogue_transcript(
            editor.state,
            (clip,),
            {
                media_asset.id: AsrResult(
                    language="en",
                    duration_seconds=2,
                    segments=(
                        AsrSegment(
                            start_seconds=61 / 60,
                            end_seconds=119 / 60,
                            text="clock invariant",
                            confidence=0.9,
                            words=(
                                AsrWord(
                                    start_seconds=61 / 60,
                                    end_seconds=90 / 60,
                                    text="clock",
                                    confidence=0.9,
                                ),
                                AsrWord(
                                    start_seconds=95 / 60,
                                    end_seconds=119 / 60,
                                    text="invariant",
                                    confidence=0.8,
                                ),
                            ),
                        ),
                    ),
                )
            },
            start_frame=0,
            end_frame=180,
        )
        assert [
            (item.start_frame, item.end_frame)
            for item in projected
        ] == [(61, 119)]
        subtitle_source = tmp_path / "short-transcript.srt"
        subtitle_source.write_text("", encoding="utf-8")
        subtitle_asset = repository.catalog.import_external_asset(
            subtitle_source,
            AssetKind.SUBTITLE,
        )
        publication = SubtitlePublicationService(repository)
        acquisition = SubtitleAcquisitionService(repository, publication)
        document = acquisition.save_sequence_transcript(
            short.id,
            subtitle_asset.id,
            projected,
            document_id=acquisition.sequence_transcript_document_id(short.id),
            language="en",
        )

        segment = repository.subtitles.list_subtitle_segments(document.id)[0]
        words = repository.subtitles.list_subtitle_words(document.id)
        assert (segment.start_frame, segment.end_frame) == (24, 48)
        assert [
            (word.start_frame, word.end_frame)
            for word in words
        ] == [(24, 36), (38, 48)]

        subtitle_track = editor.add_track(
            TrackKind.SUBTITLE
        )
        placement = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )[0]
        assert (placement.start_frame, placement.end_frame) == (60, 120)
        published = publication.write_document_srt(
            document.id,
            tmp_path / "short-published.srt",
        )
        cues = SubtitleFile.read(
            published,
            fps_numerator=60,
            fps_denominator=1,
        )
        assert [
            (cue.start_frame, cue.end_frame, cue.text)
            for cue in cues
        ] == [(60, 120, "clock invariant")]

        with ProjectRepository.open(
            repository.project_dir,
            writable=False,
        ) as observer:
            assert observer.catalog.get_sequence(
                project.main_sequence_id
            ).profile == main_profile
            reopened_segment = observer.subtitles.list_subtitle_segments(
                document.id
            )[0]
            reopened_placement = observer.subtitles.list_subtitle_placements(
                subtitle_track.id
            )[0]
            assert (
                reopened_segment.start_frame,
                reopened_segment.end_frame,
            ) == (24, 48)
            assert (
                reopened_placement.start_frame,
                reopened_placement.end_frame,
            ) == (60, 120)


def test_short_sequence_transcript_edit_uses_separate_subtitle_and_timeline_clocks(
    tmp_path: Path,
) -> None:
    main_profile = ProjectProfile(fps_numerator=24, fps_denominator=1)
    short_profile = main_profile.model_copy(
        update={
            "width": 1080,
            "height": 1920,
            "fps_numerator": 60,
        }
    )
    source = tmp_path / "short-edit.mp4"
    source.write_bytes(b"timeline-source")
    with ProjectRepository.create(
        tmp_path / "Short Transcript Edit",
        "Short Transcript Edit",
        main_profile,
    ) as repository:
        history = ProjectEditHistory()
        project = repository.catalog.get_project()
        short = repository.catalog.create_short_sequence(
            "60 fps short",
            short_profile,
        )
        asset = repository.catalog.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        editor = TimelineEditor(repository, short.id, history)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=300,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=short.id,
            language="en",
            purpose="sequence_transcript",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=0,
            end_frame=96,
            text="one two three four",
        )
        words = [
            SubtitleWord(
                segment_id=segment.id,
                position=position,
                start_frame=position * 24,
                end_frame=(position + 1) * 24,
                text=text,
            )
            for position, text in enumerate(
                ("one", "two", "three", "four")
            )
        ]
        repository.subtitles.create_subtitle_document(
            document,
            [segment],
            words,
        )
        repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )
        publication = SubtitlePublicationService(repository)
        service = TranscriptEditingService(
            repository,
            publication,
            history,
        )
        plan = service.preview_plan(
            TranscriptEditRequest(
                sequence_id=short.id,
                document_id=document.id,
                expected_content_revision=repository.content_revision(),
                selections=[
                    TranscriptEditSelection(
                        kind="words",
                        ids=[words[1].id],
                        reason="Remove duplicate",
                    )
                ],
            ),
            editor,
        )

        assert plan.version == 2
        assert plan.main_profile == main_profile
        assert plan.sequence_profile == short_profile
        assert [
            (item.start_frame, item.end_frame)
            for item in plan.subtitle_intervals
        ] == [(24, 48)]
        assert [
            (item.start_frame, item.end_frame)
            for item in plan.timeline_intervals
        ] == [(60, 120)]
        assert plan.impact.removed_duration_frames == 60
        assert plan.impact.after_duration_frames == 240

        result = service.apply_plan(plan, editor)
        assert result.after_duration_frames == 240
        assert [
            (clip.timeline_start, clip.source_in, clip.duration)
            for clip in repository.timeline.load_timeline(short.id).clips
        ] == [(0, 0, 60), (60, 120, 180)]
        edited_segment = repository.subtitles.list_subtitle_segments(
            document.id
        )[0]
        assert (
            edited_segment.start_frame,
            edited_segment.end_frame,
            edited_segment.text,
        ) == (0, 72, "one three four")
        placement = repository.subtitles.list_subtitle_placements(
            subtitle_track.id
        )[0]
        assert (placement.start_frame, placement.end_frame) == (0, 180)

        editor.undo()
        assert repository.timeline.load_timeline(short.id).clips[0].duration == 300
        assert repository.subtitles.list_subtitle_segments(
            document.id
        )[0] == segment
        editor.redo()
        assert repository.timeline.load_timeline(short.id).duration_frames == 240
        assert repository.subtitles.list_subtitle_segments(
            document.id
        )[0].end_frame == 72

        current_words = repository.subtitles.list_subtitle_words(
            document.id,
            include_excluded=False,
        )
        profile_bound_plan = service.preview_plan(
            TranscriptEditRequest(
                sequence_id=short.id,
                document_id=document.id,
                expected_content_revision=repository.content_revision(),
                selections=[
                    TranscriptEditSelection(
                        kind="words",
                        ids=[current_words[1].id],
                        reason="Verify profile binding",
                    )
                ],
            ),
            editor,
        )
        editor.set_sequence_profile(
            short_profile.model_copy(update={"fps_numerator": 30})
        )
        with pytest.raises(RuntimeError, match="frame rate changed"):
            service.apply_plan(profile_bound_plan, editor)


def test_estimated_words_can_only_be_removed_as_a_complete_segment(
    tmp_path: Path,
) -> None:
    source = tmp_path / "estimated.mp4"
    source.write_bytes(b"timeline-source")
    with ProjectRepository.create(tmp_path / "Estimated Project", "Estimated") as repository:
        history = ProjectEditHistory()
        project = repository.catalog.get_project()
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        editor = TimelineEditor(repository, project.main_sequence_id, history)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=60,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
            purpose="sequence_transcript",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=0,
            end_frame=60,
            text="hello world",
        )
        words = [
            SubtitleWord(
                segment_id=segment.id,
                position=position,
                start_frame=position * 30,
                end_frame=(position + 1) * 30,
                text=text,
                timing_source="estimated",
            )
            for position, text in enumerate(("hello", "world"))
        ]
        repository.subtitles.create_subtitle_document(document, [segment], words)
        service = TranscriptEditingService(
            repository,
            SubtitlePublicationService(repository),
            history,
        )

        plan = service.preview_plan(
            TranscriptEditRequest(
                sequence_id=project.main_sequence_id,
                document_id=document.id,
                expected_content_revision=repository.content_revision(),
                selections=[
                    TranscriptEditSelection(
                        kind="segments",
                        ids=[segment.id],
                        reason="Remove unusable sentence",
                    )
                ],
            ),
            editor,
        )
        assert plan.resolved_selections[0].timing == "subtitle_segments"
        assert plan.impact.after_duration_frames == 0
        result = service.apply_plan(plan, editor)
        assert result.removed_word_count == 2
        assert result.removed_segment_count == 1
        assert repository.timeline.load_timeline(project.main_sequence_id).clips == []
        assert repository.subtitles.list_subtitle_segments(document.id) == []
        assert repository.subtitles.list_subtitle_words(document.id) == []


def test_sequence_subtitle_timing_edit_persists_through_document_sync_and_undo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "timing.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nAdjust me\n",
        encoding="utf-8-sig",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        history = ProjectEditHistory()
        acquisition, editing, _publication = _build_subtitle_components(repository, history)
        document = acquisition.import_subtitle_file(source, AssetService(repository, MediaProbe()))
        project = repository.catalog.get_project()
        subtitle_track = TimelineEditor(repository, project.main_sequence_id).add_track(
            TrackKind.SUBTITLE
        )
        placement = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )[0]

        adjusted = editing.update_placement_range(
            placement.id,
            start_frame=12,
            end_frame=48,
        )
        assert (adjusted.start_frame, adjusted.end_frame, adjusted.timing_overridden) == (
            12,
            48,
            True,
        )

        segment = repository.subtitles.list_subtitle_segments(document.id)[0]
        editing.update_segment(
            document.id,
            segment.id,
            start_frame=3,
            end_frame=60,
            text="Document changed",
        )
        persisted = repository.subtitles.get_subtitle_placement(placement.id)
        assert (persisted.start_frame, persisted.end_frame) == (12, 48)

        history.undo()
        history.undo()
        restored = repository.subtitles.get_subtitle_placement(placement.id)
        assert (restored.start_frame, restored.end_frame, restored.timing_overridden) == (
            placement.start_frame,
            placement.end_frame,
            False,
        )

        history.redo()
        moved_again = repository.subtitles.get_subtitle_placement(placement.id)
        assert (moved_again.start_frame, moved_again.end_frame, moved_again.timing_overridden) == (
            12,
            48,
            True,
        )

        reset = editing.reset_placement_range(placement.id)
        assert (reset.start_frame, reset.end_frame, reset.timing_overridden) == (
            placement.start_frame,
            placement.end_frame,
            False,
        )
        history.undo()
        restored_override = repository.subtitles.get_subtitle_placement(placement.id)
        assert (
            restored_override.start_frame,
            restored_override.end_frame,
            restored_override.timing_overridden,
        ) == (12, 48, True)


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
        assert repository.catalog.get_asset(document.asset_id).kind == AssetKind.SUBTITLE
        assert document.media_asset_id is not None
        media = repository.catalog.get_asset(document.media_asset_id)
        assert media.kind == AssetKind.VIDEO
        assert repository.catalog.resolve_asset_path(media) == video_path.resolve()

        project = repository.catalog.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=media.id,
            timeline_start=45,
            source_in=0,
            duration=media.metadata.duration_frames,
        )
        placements = repository.subtitles.place_subtitle_document(document.id, subtitle_track.id)
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
        project = repository.catalog.get_project()
        track = TimelineEditor(repository, project.main_sequence_id).add_track(
            TrackKind.SUBTITLE
        )
        original = repository.subtitles.place_subtitle_document(
            document.id,
            track.id,
            follow_clips=False,
        )[0]
        assert editing.smart_split_document(document.id, text_limit=12) == 1
        split_segments = repository.subtitles.list_subtitle_segments(document.id)
        split_placements = repository.subtitles.list_subtitle_placements(track.id)
        assert len(split_segments) == len(split_placements) == 2
        first_placement = next(item for item in split_placements if item.segment_id == split_segments[0].id)
        assert first_placement.id == original.id

        assert editing.delete_segments(document.id, [split_segments[1].id]) == 1
        assert [item.segment_id for item in repository.subtitles.list_subtitle_placements(track.id)] == [
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
        before = repository.subtitles.list_subtitle_segments(document.id)
        assert editing.fix_overlaps(document.id) == 1
        fixed = repository.subtitles.list_subtitle_segments(document.id)
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
        assert [segment.text for segment in repository.subtitles.list_subtitle_segments(document.id)] == [
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
        assert [segment.text for segment in repository.subtitles.list_subtitle_segments(document.id)] == [
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
        assert [
            repository.subtitles.list_subtitle_segments(document.id)[0].text
            for document in documents
        ] == [
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


def test_default_subtitle_artifact_ignores_language_as_a_filename_at_max_root(
    max_project_path: Path,
) -> None:
    with ProjectRepository.create(
        max_project_path,
        "Subtitle Path Budget",
    ) as repository:
        project = repository.catalog.get_project()
        asset_path = max_project_path.parent / "subtitle-source.srt"
        asset_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nPath safe\n",
            encoding="utf-8-sig",
        )
        asset = repository.catalog.import_external_asset(
            asset_path,
            AssetKind.SUBTITLE,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language=("../CON\\" + "超长语言" * 100),
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=0,
            end_frame=25,
            text="Path safe",
        )
        repository.subtitles.create_subtitle_document(document, [segment])

        publication = SubtitlePublicationService(repository)
        output = publication.write_document_srt(document.id)

        assert output.is_file()
        assert output.parent == (
            repository.project_dir / "generated" / "subtitles"
        )
        assert output.name.startswith("sub-")
        assert output.suffix == ".srt"
        assert utf16_units(str(output)) <= 240
        assert "CON" not in output.name
        assert "Path safe" in output.read_text(encoding="utf-8-sig")

        first_missing = max_project_path.parent / "no-srt-directory"
        explicit_parent = first_missing
        while utf16_units(str(explicit_parent / "subtitle.srt")) <= 240:
            explicit_parent /= "deep-subtitle-output"
        with pytest.raises(ValueError, match="路径过深"):
            publication.write_document_srt(
                document.id,
                explicit_parent / "subtitle.srt",
            )
        assert not first_missing.exists()


def test_subtitle_edit_database_commit_failure_restores_database_and_visible_srt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nOriginal\n",
        encoding="utf-8-sig",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, editing, publication = _build_subtitle_components(repository)
        document = acquisition.import_subtitle_file(
            source,
            AssetService(repository, MediaProbe()),
        )
        segment = repository.subtitles.list_subtitle_segments(document.id)[0]
        output = publication.document_srt_path(document.id)
        original_bytes = output.read_bytes()
        original_revision = repository.content_revision()
        _fail_outermost_transaction_commit(repository, monkeypatch)

        with pytest.raises(RuntimeError, match="injected database commit failure"):
            editing.update_segment(
                document.id,
                segment.id,
                start_frame=segment.start_frame,
                end_frame=segment.end_frame,
                text="Uncommitted text",
            )

        assert (
            repository.subtitles.list_subtitle_segments(document.id)[0].text
            == "Original"
        )
        assert repository.content_revision() == original_revision
        assert output.read_bytes() == original_bytes
        archived = list(
            (repository.project_dir / "archive" / "subtitle-publications").rglob(
                "*.srt"
            )
        )
        assert len(archived) == 1
        assert "Uncommitted text" in archived[0].read_text(
            encoding="utf-8-sig"
        )


def test_new_translation_commit_failure_leaves_no_document_or_visible_srt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        encoding="utf-8-sig",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, _editing, publication = _build_subtitle_components(
            repository
        )
        source_document = acquisition.import_subtitle_file(
            source,
            AssetService(repository, MediaProbe()),
        )
        operation_id = "translation-commit-failure"
        output = publication.document_srt_path(operation_id)
        service = TranslationService(
            repository,
            _translation_client,
            _MemoryTranslationCache(),
            publication,
        )
        provider = LlmProviderSettings(
            name="test",
            base_url="https://example.invalid",
            model="test-model",
        )
        _fail_outermost_transaction_commit(repository, monkeypatch)

        with pytest.raises(RuntimeError, match="injected database commit failure"):
            service.translate_document(
                source_document.id,
                target_language="zh_CN",
                provider=provider,
                operation_id=operation_id,
            )

        with pytest.raises(KeyError):
            repository.subtitles.get_subtitle_document(operation_id)
        assert not output.exists()
        archived = list(
            (repository.project_dir / "archive" / "subtitle-publications").rglob(
                "*.srt"
            )
        )
        assert len(archived) == 1
        assert "译文：Hello" in archived[0].read_text(encoding="utf-8-sig")


def test_subtitle_import_cancellation_after_related_media_probe_has_no_side_effects(
    tmp_path: Path,
) -> None:
    video = tmp_path / "interview.mp4"
    subtitle = tmp_path / "interview.srt"
    generate_real_media(
        video,
        RuntimePaths.discover(),
        width=320,
        height=180,
    )
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nInterview\n",
        encoding="utf-8-sig",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, _editing, _publication = _build_subtitle_components(
            repository
        )

        def cancel_after_preparation() -> None:
            raise RuntimeError("injected cancellation")

        with pytest.raises(RuntimeError, match="injected cancellation"):
            acquisition.import_subtitle_file(
                subtitle,
                AssetService(repository, MediaProbe()),
                check_cancelled=cancel_after_preparation,
            )

        assert repository.catalog.list_assets() == []
        assert repository.subtitles.list_subtitle_documents() == []
        assert not list(
            (repository.project_dir / "generated" / "subtitles").rglob(
                "*.srt"
            )
        )


def test_subtitle_import_commit_failure_with_related_media_rolls_back_everything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "interview.mp4"
    subtitle = tmp_path / "interview.srt"
    generate_real_media(
        video,
        RuntimePaths.discover(),
        width=320,
        height=180,
    )
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nInterview\n",
        encoding="utf-8-sig",
    )
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        acquisition, _editing, _publication = _build_subtitle_components(
            repository
        )
        original_revision = repository.content_revision()
        _fail_outermost_transaction_commit(repository, monkeypatch)

        with pytest.raises(RuntimeError, match="injected database commit failure"):
            acquisition.import_subtitle_file(
                subtitle,
                AssetService(repository, MediaProbe()),
            )

        assert repository.content_revision() == original_revision
        assert repository.catalog.list_assets() == []
        assert repository.subtitles.list_subtitle_documents() == []
        assert not list(
            (repository.project_dir / "generated" / "subtitles").rglob(
                "*.srt"
            )
        )
        archived = list(
            (repository.project_dir / "archive" / "subtitle-publications").rglob(
                "*.srt"
            )
        )
        assert len(archived) == 1
        assert "Interview" in archived[0].read_text(encoding="utf-8-sig")


def test_subtitle_reconciliation_repairs_only_writable_project_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Project"
    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nReconcile\n",
        encoding="utf-8-sig",
    )
    with ProjectRepository.create(root, "Project") as repository:
        project = repository.catalog.get_project()
        asset = repository.catalog.import_external_asset(
            source,
            AssetKind.SUBTITLE,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
        )
        repository.subtitles.create_subtitle_document(
            document,
            [
                SubtitleSegment(
                    document_id=document.id,
                    start_frame=0,
                    end_frame=25,
                    text="Reconcile",
                )
            ],
        )
        expected = SubtitlePublicationService(repository).document_srt_path(
            document.id
        )
        assert not expected.exists()

    external = tmp_path / "read-only-export.srt"
    with ProjectRepository.open(root, writable=False) as repository:
        publication = SubtitlePublicationService(repository)
        assert publication.reconcile_document_srts() == ()
        assert not expected.exists()
        with pytest.raises(PermissionError, match="只读项目"):
            publication.write_document_srt(document.id)
        publication.write_document_srt(document.id, external)
        assert "Reconcile" in external.read_text(encoding="utf-8-sig")

    with ProjectRepository.open(root) as repository:
        outputs = SubtitlePublicationService(
            repository
        ).reconcile_document_srts()
        assert outputs == (expected,)
        assert "Reconcile" in expected.read_text(encoding="utf-8-sig")
