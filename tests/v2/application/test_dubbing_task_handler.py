from __future__ import annotations

import wave
from contextlib import nullcontext
from pathlib import Path
from typing import cast

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.dubbing_editing import DubbingEditingService
from mediaflow.application.dubbing_task_handler import DubbingTaskHandler
from mediaflow.application.ports import DubbingTaskRuntime
from mediaflow.application.task_service import CancellationToken, TaskContext
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.dubbing import (
    DiarizationResult,
    DiarizationTurn,
    DubbingReference,
    DubbingSpeaker,
)
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.project import MediaMetadata, ProjectProfile
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import (
    CommitDubbingCommand,
    PrepareDubbingCommand,
    SynthesizeDubbingCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.dubbing_runtime import PreparedDubbingAudio
from mediaflow.infrastructure.gpt_sovits_engine import GptSoVitsResult
from mediaflow.infrastructure.media_probe import ProbeResult
from mediaflow.infrastructure.project_repository import ProjectRepository


def _write_wave(path: Path, duration_seconds: float, sample_rate: int = 48_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * max(1, round(duration_seconds * sample_rate)))
    return path


def _audio(path: Path) -> PreparedDubbingAudio:
    with wave.open(str(path), "rb") as source:
        duration = source.getnframes() / source.getframerate()
        sample_rate = source.getframerate()
        channels = source.getnchannels()
    return PreparedDubbingAudio(
        path=path,
        sha256=sha256_file(path),
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
    )


def _reference(reference_id: str, sha256: str, *, primary: bool) -> DubbingReference:
    return DubbingReference(
        id=reference_id,
        speaker_id="speaker-1",
        path=f"generated/{reference_id}.wav",
        sha256=sha256,
        start_frame=0,
        end_frame=90,
        text="Exact reference transcript",
        language="en",
        duration_seconds=3.0,
        primary=primary,
    )


def test_reference_fingerprint_tracks_primary_prompt_and_auxiliary_audio() -> None:
    primary = _reference("primary", "1" * 64, primary=True)
    auxiliary = _reference("auxiliary", "2" * 64, primary=False)
    speaker = DubbingSpeaker(
        id="speaker-1",
        label="SPEAKER_00",
        display_name="Speaker 1",
        references=[primary, auxiliary],
    )
    original = DubbingTaskHandler._reference_fingerprint(speaker)

    changed_prompt = speaker.model_copy(
        update={
            "references": [
                primary.model_copy(update={"text": "Changed exact transcript"}),
                auxiliary,
            ]
        }
    )
    changed_auxiliary = speaker.model_copy(
        update={
            "references": [
                primary,
                auxiliary.model_copy(update={"sha256": "3" * 64}),
            ]
        }
    )

    assert DubbingTaskHandler._reference_fingerprint(changed_prompt) != original
    assert DubbingTaskHandler._reference_fingerprint(changed_auxiliary) != original


class _AudioProbe:
    def probe(
        self,
        _path: str | Path,
        *,
        timeline_profile: ProjectProfile | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            kind=AssetKind.AUDIO,
            metadata=MediaMetadata(
                duration_frames=300,
                has_audio=True,
            ),
            suggested_profile=None,
        )


class _SynthesisSession:
    def synthesize(
        self,
        *,
        output_path: str | Path,
        speed_factor: float,
        **_arguments,
    ) -> GptSoVitsResult:
        output = _write_wave(Path(output_path), 8.0 / speed_factor)
        return GptSoVitsResult(
            output_path=output,
            sha256=sha256_file(output),
            duration_seconds=8.0 / speed_factor,
            sample_rate=48_000,
            channels=1,
            reference_audio_sha256="0" * 64,
            device="cpu",
        )


class _DubbingRuntime:
    def __init__(self) -> None:
        self.archived: list[Path] = []

    @staticmethod
    def file_sha256(path: Path) -> str:
        return sha256_file(path)

    def archive_unrecorded_outputs(self, paths: list[Path]) -> tuple[Path, ...]:
        self.archived.extend(paths)
        return tuple(paths)

    def render_dialogue_audio(
        self,
        _state,
        _dialogue_track_id,
        output_path,
        **_arguments,
    ) -> PreparedDubbingAudio:
        return _audio(_write_wave(Path(output_path), 4.0))

    def diarize(self, _source, _settings, **_arguments) -> DiarizationResult:
        return DiarizationResult(
            engine="pyannote.audio",
            model="pyannote/speaker-diarization-community-1",
            engine_version="test",
            device="cpu",
            exclusive=True,
            turns=(
                DiarizationTurn(
                    speaker="SPEAKER_00",
                    start_seconds=0.0,
                    end_seconds=4.0,
                ),
            ),
        )

    def extract_reference(
        self,
        _source,
        output_path,
        *,
        start_seconds,
        end_seconds,
        **_arguments,
    ) -> PreparedDubbingAudio:
        return _audio(
            _write_wave(Path(output_path), end_seconds - start_seconds)
        )

    def synthesis_session(self, _settings, **_arguments):
        return "test-gpt-sovits", nullcontext(_SynthesisSession())

    def normalize_utterance(
        self,
        source,
        output_path,
        *,
        target_seconds,
        **_arguments,
    ) -> PreparedDubbingAudio:
        source_audio = _audio(Path(source))
        duration = source_audio.duration_seconds if target_seconds is None else target_seconds
        return _audio(_write_wave(Path(output_path), duration))

    def assemble_master(
        self,
        inputs,
        output_path,
        *,
        minimum_duration_seconds,
        **_arguments,
    ) -> PreparedDubbingAudio:
        duration = max(
            minimum_duration_seconds,
            *(start + _audio(Path(path)).duration_seconds for path, start in inputs),
        )
        return _audio(_write_wave(Path(output_path), duration))


def _commit_changes(repository: ProjectRepository, context: TaskContext) -> None:
    with repository.transaction(), repository.coalesced_revision():
        for change in context.project_changes():
            change()


def _context(repository: ProjectRepository, command) -> TaskContext:
    project = repository.projects.get_project()
    return TaskContext(
        task=Task(
            project_id=project.id,
            sequence_id=project.main_sequence_id,
            command=command,
        ),
        project_dir=repository.project_dir,
        cancellation=CancellationToken(),
        report=lambda _progress: None,
    )


def test_dubbing_prepare_synthesize_and_commit_preserves_overlong_speech(
    tmp_path: Path,
) -> None:
    root = tmp_path / "DubbingTask"
    with ProjectRepository.create(root, "Dubbing task") as repository:
        project = repository.projects.get_project()
        sequence_id = project.main_sequence_id
        source_audio = _write_wave(tmp_path / "source.wav", 4.0)
        source_asset = repository.assets.import_external_asset(
            source_audio,
            AssetKind.AUDIO,
        )
        source_asset = repository.assets.update_asset(
            source_asset.model_copy(
                update={
                    "metadata": MediaMetadata(
                        duration_frames=120,
                        has_audio=True,
                    )
                }
            )
        )
        editor = TimelineEditor(repository, sequence_id)
        dialogue = editor.add_track(TrackKind.AUDIO, "English dialogue")
        editor.add_clip(
            track_id=dialogue.id,
            asset_id=source_asset.id,
            timeline_start=0,
            source_in=0,
            duration=120,
        )
        editor.set_primary_dialogue_track(dialogue.id)

        subtitle_path = tmp_path / "source.srt"
        subtitle_path.write_text(
            "1\n00:00:00,000 --> 00:00:04,000\nHello there\n",
            encoding="utf-8",
        )
        subtitle_asset = repository.assets.import_external_asset(
            subtitle_path,
            AssetKind.SUBTITLE,
        )
        source_document = SubtitleDocument(
            project_id=project.id,
            asset_id=subtitle_asset.id,
            sequence_id=sequence_id,
            language="en",
            purpose="sequence_transcript",
        )
        source_segment = SubtitleSegment(
            document_id=source_document.id,
            start_frame=0,
            end_frame=120,
            text="Hello there",
        )
        repository.subtitles.create_subtitle_document(
            source_document,
            [source_segment],
        )
        target_document = SubtitleDocument(
            project_id=project.id,
            asset_id=subtitle_asset.id,
            sequence_id=sequence_id,
            source_document_id=source_document.id,
            language="zh",
            is_source=False,
        )
        repository.subtitles.create_subtitle_document(
            target_document,
            [
                SubtitleSegment(
                    document_id=target_document.id,
                    source_segment_id=source_segment.id,
                    start_frame=0,
                    end_frame=120,
                    text="你好，这是一句故意很长的配音测试。",
                )
            ],
        )

        runtime = _DubbingRuntime()
        handler = DubbingTaskHandler(
            repository,
            AssetService(repository, _AudioProbe()),
            cast(DubbingTaskRuntime, runtime),
            translations=cast(TranslationService, object()),
            settings=lambda: ServiceSettings(),
            active_llm_provider=lambda: None,
            timeline_provider=lambda current_sequence_id: TimelineEditor(
                repository,
                current_sequence_id,
            ),
        )

        prepare_context = _context(
            repository,
            PrepareDubbingCommand(
                sequence_id=sequence_id,
                source_document_id=source_document.id,
                target_document_id=target_document.id,
                target_language="zh",
            ),
        )
        prepared = handler.handle(prepare_context)
        _commit_changes(repository, prepare_context)
        assert prepared.outcome is not None
        session = repository.dubbing.get_session(prepare_context.task.id)
        assert len(session.speakers) == 1
        assert session.speakers[0].primary_reference is not None

        synthesize_context = _context(
            repository,
            SynthesizeDubbingCommand(
                sequence_id=sequence_id,
                session_id=session.id,
            ),
        )
        synthesized = handler.handle(synthesize_context)
        _commit_changes(repository, synthesize_context)
        session = repository.dubbing.get_session(session.id)
        utterance = session.utterances[0]
        assert synthesized.outcome is not None
        assert session.status == "synthesized"
        assert utterance.status == "needs_review"
        assert utterance.speed_factor == pytest.approx(
            session.settings.maximum_speed_factor
        )
        assert utterance.fitted_duration_seconds is not None
        assert utterance.fitted_duration_seconds > 4.0
        assert "仍超出可用时长" in utterance.issues[0]
        assert session.master_path is not None
        assert (root / session.master_path).is_file()

        commit_context = _context(
            repository,
            CommitDubbingCommand(
                sequence_id=sequence_id,
                session_id=session.id,
            ),
        )
        committed = handler.handle(commit_context)
        _commit_changes(repository, commit_context)
        session = repository.dubbing.get_session(session.id)
        timeline = repository.timeline.load_timeline(sequence_id)
        source_track = next(item for item in timeline.tracks if item.id == dialogue.id)
        assert committed.outcome is not None
        assert session.status == "committed"
        assert session.committed_track_id is not None
        assert session.committed_clip_id is not None
        assert source_track.muted is True
        assert any(item.id == session.committed_clip_id for item in timeline.clips)

        committed_track_id = session.committed_track_id
        committed_clip_id = session.committed_clip_id
        edited = DubbingEditingService(repository.dubbing, lambda: None).update_utterance(
            session.id,
            utterance.id,
            expected_revision=session.revision,
            target_text="你好，这是修改后重新生成的中文配音。",
            speaker_id=utterance.speaker_id,
            review_status="accepted",
        )
        assert edited.status == "review"
        assert edited.committed_track_id == committed_track_id
        assert edited.committed_clip_id == committed_clip_id

        resynthesize_context = _context(
            repository,
            SynthesizeDubbingCommand(
                sequence_id=sequence_id,
                session_id=session.id,
                utterance_ids=[utterance.id],
                regenerate=True,
            ),
        )
        handler.handle(resynthesize_context)
        _commit_changes(repository, resynthesize_context)
        resynthesized = repository.dubbing.get_session(session.id)
        assert resynthesized.status == "synthesized"

        recommit_context = _context(
            repository,
            CommitDubbingCommand(
                sequence_id=sequence_id,
                session_id=session.id,
            ),
        )
        handler.handle(recommit_context)
        _commit_changes(repository, recommit_context)
        recommitted = repository.dubbing.get_session(session.id)
        updated_timeline = repository.timeline.load_timeline(sequence_id)
        assert recommitted.committed_track_id == committed_track_id
        assert recommitted.committed_clip_id == committed_clip_id
        assert (
            recommitted.source_timeline_revision
            == updated_timeline.sequence.timeline_revision
        )
        assert sum(item.id == committed_track_id for item in updated_timeline.tracks) == 1
        assert sum(item.id == committed_clip_id for item in updated_timeline.clips) == 1
        assert runtime.archived == []
