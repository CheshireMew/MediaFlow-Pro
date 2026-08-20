from __future__ import annotations

import hashlib
from pathlib import Path

from mediaflow.application.asset_service import AssetService, PreparedAssetRegistration
from mediaflow.application.dubbing_service import (
    DubbingPreparationPlan,
    DubbingPreparationService,
)
from mediaflow.application.ports import (
    DubbingSynthesisOutput,
    DubbingSynthesisSession,
    DubbingTaskRuntime,
    PreparedDubbingAudio,
    ProjectTaskDocuments,
)
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.translation_service import (
    PreparedDocumentTranslation,
    TranslationService,
)
from mediaflow.domain.dubbing import (
    DiarizationSpeechInterval,
    DubbingReference,
    DubbingSession,
    DubbingSpeaker,
    DubbingUtterance,
)
from mediaflow.domain.enums import AssetKind, AssetOrigin, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import LlmProviderSettings, ServiceSettings
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.domain.task_commands import (
    CommitDubbingCommand,
    PrepareDubbingCommand,
    SynthesizeDubbingCommand,
)
from mediaflow.domain.tasks import ArtifactReference, DubbingTaskOutcome
from mediaflow.domain.timebase import frames_to_seconds
from mediaflow.domain.timeline import TimelineState


class DubbingTaskHandler(ProjectTaskHandler):
    def __init__(
        self,
        documents: ProjectTaskDocuments,
        assets: AssetService,
        runtime: DubbingTaskRuntime,
        translations: TranslationService,
        settings,
        active_llm_provider,
        timeline_provider,
    ) -> None:
        super().__init__(documents.project_dir)
        self.documents = documents
        self.assets = assets
        self.runtime = runtime
        self.translations = translations
        self.settings = settings
        self.active_llm_provider = active_llm_provider
        self.timeline_provider = timeline_provider
        self.preparation = DubbingPreparationService()

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = context.task.command
        if isinstance(command, PrepareDubbingCommand):
            return self._prepare(context, command)
        if isinstance(command, SynthesizeDubbingCommand):
            return self._synthesize(context, command)
        if isinstance(command, CommitDubbingCommand):
            return self._commit(context, command)
        raise TypeError(f"Unexpected dubbing command: {type(command).__name__}")

    def _prepare(
        self,
        context: TaskContext,
        command: PrepareDubbingCommand,
    ) -> TaskCompletion:
        state = self.documents.timeline.load_timeline(command.sequence_id)
        dialogue_track = next(
            (track for track in state.tracks if track.kind == TrackKind.AUDIO and track.primary_dialogue),
            None,
        )
        if dialogue_track is None:
            raise ValueError("请先把只包含主要对白的音频轨设为主要对白轨")
        source_document = self.documents.subtitles.get_subtitle_document(command.source_document_id)
        if source_document.sequence_id not in {None, command.sequence_id}:
            raise ValueError("源字幕文档不属于当前序列")
        source_segments = self.documents.subtitles.list_subtitle_segments(source_document.id)
        source_words = self.documents.subtitles.list_subtitle_words(
            source_document.id,
            include_excluded=False,
        )
        if not source_segments:
            raise ValueError("源字幕文档为空")
        prepared_translation: PreparedDocumentTranslation | None = None
        target_document = None
        target_segments = None
        if command.target_document_id:
            target_document = self.documents.subtitles.get_subtitle_document(command.target_document_id)
            if target_document.source_document_id != source_document.id:
                raise ValueError("目标字幕不是当前源字幕的译文")
            target_segments = self.documents.subtitles.list_subtitle_segments(target_document.id)
            if not target_segments:
                raise ValueError("目标语言字幕文档为空")
        project = self.documents.projects.get_project()
        main_profile = self.documents.sequences.get_sequence(project.main_sequence_id).profile
        session_root = self.project_dir / "cache" / "dubbing" / context.task.id
        source_audio = session_root / "dialogue-source.wav"
        context.report(OperationProgress.indeterminate("dubbing_rendering_dialogue"))
        rendered = self.runtime.render_dialogue_audio(
            state,
            dialogue_track.id,
            source_audio,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        context.report(OperationProgress.indeterminate("dubbing_identifying_speakers"))
        ordered_source_segments = sorted(
            source_segments,
            key=lambda item: (item.start_frame, item.end_frame, item.id),
        )
        if any(
            left.end_frame > right.start_frame
            for left, right in zip(
                ordered_source_segments,
                ordered_source_segments[1:],
                strict=False,
            )
        ):
            raise ValueError(
                "源转写片段存在重叠；普通音色聚类只支持轮流说话，请修正转写时间或在设置中改用 Community-1"
            )
        speech_intervals = tuple(
            DiarizationSpeechInterval(
                start_seconds=float(
                    frames_to_seconds(
                        item.start_frame,
                        state.sequence.profile.fps_numerator,
                        state.sequence.profile.fps_denominator,
                    )
                ),
                end_seconds=float(
                    frames_to_seconds(
                        item.end_frame,
                        state.sequence.profile.fps_numerator,
                        state.sequence.profile.fps_denominator,
                    )
                ),
            )
            for item in ordered_source_segments
        )
        diarization = self.runtime.diarize(
            rendered.path,
            self.settings().speaker_diarization,
            minimum_speakers=command.settings.minimum_speakers,
            maximum_speakers=command.settings.maximum_speakers,
            speech_intervals=speech_intervals,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        if target_document is None:
            service_settings: ServiceSettings = self.settings()
            provider: LlmProviderSettings = self.active_llm_provider()
            prepared_translation = self.translations.prepare_document_translation(
                source_document.id,
                target_language=command.target_language,
                provider=provider,
                mode="standard",
                glossary=service_settings.translation.glossary_terms,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
                operation_id=context.task.id,
            )
            target_document = prepared_translation.document
            target_segments = list(prepared_translation.segments)
        assert target_segments is not None
        plan = self.preparation.prepare(
            source_segments=source_segments,
            target_segments=target_segments,
            diarization=diarization,
            main_profile=main_profile,
            sequence_profile=state.sequence.profile,
            settings=command.settings,
            source_words=source_words,
        )
        references_root = self.project_dir / "generated" / "dubbing" / context.task.id / "references"
        published_outputs: list[Path] = []
        try:
            speakers = self._extract_references(
                context,
                plan,
                rendered.path,
                rendered.sha256,
                references_root,
                state.sequence.profile,
                source_document.language,
                published_outputs,
            )
            session = DubbingSession(
                id=context.task.id,
                project_id=project.id,
                sequence_id=command.sequence_id,
                source_document_id=source_document.id,
                target_document_id=target_document.id,
                source_language=source_document.language,
                target_language=target_document.language,
                dialogue_track_id=dialogue_track.id,
                source_timeline_revision=state.sequence.timeline_revision,
                status="review",
                settings=command.settings,
                speakers=speakers,
                turns=list(plan.turns),
                utterances=list(plan.utterances),
                diarization_engine=diarization.engine,
                diarization_version=diarization.engine_version,
                diarization_model=diarization.model,
            )
        except BaseException as error:
            self._archive_unrecorded(published_outputs, error)
            raise

        def commit_preparation() -> None:
            self.documents.enlist_transaction_publication(
                on_commit=lambda: None,
                on_rollback=lambda error: self._archive_unrecorded(
                    published_outputs,
                    error,
                ),
            )
            latest = self.documents.timeline.load_timeline(command.sequence_id)
            if latest.sequence.timeline_revision != session.source_timeline_revision:
                raise RuntimeError("时间轴在说话人识别期间已发生变化，请重新创建配音方案")
            if prepared_translation is not None:
                self.translations.commit_document_translation(prepared_translation)
            self.documents.dubbing.create_session(session)

        context.defer_project_change(commit_preparation)
        return TaskCompletion(
            outcome=DubbingTaskOutcome(
                session_id=session.id,
                phase="prepared",
            )
        )

    def _extract_references(
        self,
        context: TaskContext,
        plan: DubbingPreparationPlan,
        source_audio: Path,
        source_sha256: str,
        references_root: Path,
        profile: ProjectProfile,
        source_language: str,
        published_outputs: list[Path],
    ) -> list[DubbingSpeaker]:
        speakers: list[DubbingSpeaker] = []
        candidate_total = sum(len(value) for value in plan.reference_candidates.values())
        completed = 0
        for speaker in plan.speakers:
            references: list[DubbingReference] = []
            for index, candidate in enumerate(
                plan.reference_candidates[speaker.id],
                start=1,
            ):
                context.cancellation.raise_if_requested()
                path = content_addressed_child_path(
                    references_root,
                    (f"{speaker.id}:{candidate.start_frame}:{candidate.end_frame}:{source_sha256}"),
                    namespace=f"{speaker.id}-ref-{index}",
                    suffix=".wav",
                )
                existed = self.documents.assets.is_regular_file(path)
                audio = self.runtime.extract_reference(
                    source_audio,
                    path,
                    start_seconds=self._frame_seconds(candidate.start_frame, profile),
                    end_seconds=self._frame_seconds(candidate.end_frame, profile),
                    sample_rate=profile.audio_sample_rate,
                    check_cancelled=context.cancellation.raise_if_requested,
                )
                if not existed:
                    published_outputs.append(audio.path)
                references.append(
                    DubbingReference(
                        speaker_id=speaker.id,
                        path=audio.path.relative_to(self.project_dir).as_posix(),
                        sha256=audio.sha256,
                        start_frame=candidate.start_frame,
                        end_frame=candidate.end_frame,
                        text=candidate.text,
                        language=source_language,
                        duration_seconds=audio.duration_seconds,
                        primary=index == 1,
                    )
                )
                completed += 1
                context.report(
                    OperationProgress.determinate(
                        "dubbing_extracting_references",
                        completed=completed,
                        total=max(1, candidate_total),
                        unit="items",
                    )
                )
            if not references:
                raise RuntimeError(f"{speaker.display_name} 没有可用的克隆参考音频")
            speakers.append(speaker.model_copy(update={"references": references}))
        return speakers

    def _synthesize(
        self,
        context: TaskContext,
        command: SynthesizeDubbingCommand,
    ) -> TaskCompletion:
        published_outputs: list[Path] = []
        try:
            return self._synthesize_with_outputs(
                context,
                command,
                published_outputs,
            )
        except BaseException as error:
            self._archive_unrecorded(published_outputs, error)
            raise

    def _synthesize_with_outputs(
        self,
        context: TaskContext,
        command: SynthesizeDubbingCommand,
        published_outputs: list[Path],
    ) -> TaskCompletion:
        session, state, targets, speakers = self._synthesis_targets(command)
        version, synthesis_context = self.runtime.synthesis_session(
            self.settings(),
            check_cancelled=context.cancellation.raise_if_requested,
        )
        output_root = self.project_dir / "generated" / "dubbing" / session.id / "utterances"
        cache_root = self.project_dir / "cache" / "dubbing" / session.id / "synthesis"
        with synthesis_context as synthesis:
            updated_by_id = self._synthesize_utterances(
                context,
                command,
                session,
                state,
                targets,
                speakers,
                synthesis,
                version,
                output_root,
                cache_root,
                published_outputs,
            )
        utterances = [updated_by_id.get(item.id, item) for item in session.utterances]
        master = self._assemble_master(
            context,
            session,
            state,
            utterances,
            published_outputs,
        )
        return self._complete_synthesis(
            context,
            session,
            utterances,
            version,
            master,
            published_outputs,
        )

    def _complete_synthesis(
        self,
        context: TaskContext,
        session: DubbingSession,
        utterances: list[DubbingUtterance],
        version: str,
        master: PreparedDubbingAudio,
        published_outputs: list[Path],
    ) -> TaskCompletion:
        updated = session.model_copy(
            update={
                "status": "synthesized",
                "utterances": utterances,
                "synthesis_version": version,
                "master_path": master.path.relative_to(self.project_dir).as_posix(),
                "master_sha256": master.sha256,
                "master_duration_seconds": master.duration_seconds,
                "master_asset_id": None,
            }
        )

        def commit_synthesis() -> None:
            self.documents.enlist_transaction_publication(
                on_commit=lambda: None,
                on_rollback=lambda error: self._archive_unrecorded(
                    published_outputs,
                    error,
                ),
            )
            self.documents.dubbing.save_session(
                updated,
                expected_revision=session.revision,
            )

        context.defer_project_change(commit_synthesis)
        artifact = ArtifactReference.project(
            self.project_dir,
            master.path,
        )
        return TaskCompletion.with_artifacts(
            artifact,
            outcome=DubbingTaskOutcome(
                session_id=session.id,
                phase="synthesized",
                master=artifact,
            ),
        )

    def _synthesis_targets(
        self,
        command: SynthesizeDubbingCommand,
    ) -> tuple[
        DubbingSession,
        TimelineState,
        list[DubbingUtterance],
        dict[str, DubbingSpeaker],
    ]:
        session = self.documents.dubbing.get_session(command.session_id)
        if session.sequence_id != command.sequence_id:
            raise ValueError("配音方案不属于当前序列")
        state = self.documents.timeline.load_timeline(session.sequence_id)
        if state.sequence.timeline_revision != session.source_timeline_revision:
            raise RuntimeError("时间轴在配音方案创建后已变化，请重新创建配音方案")
        requested = set(command.utterance_ids)
        known = {item.id for item in session.utterances}
        if requested - known:
            raise KeyError("包含不属于当前配音方案的句子")
        targets = [item for item in session.utterances if not requested or item.id in requested]
        target_ids = {item.id for item in targets}
        untouched = [item for item in session.utterances if item.id not in target_ids]
        if any(item.status not in {"generated", "needs_review"} for item in untouched):
            raise ValueError("首次合成必须处理全部句子，之后才能只重做选中句子")
        speakers = {item.id: item for item in session.speakers}
        for speaker in speakers.values():
            if speaker.primary_reference is None:
                raise ValueError(f"{speaker.display_name} 尚未选择主参考音频")
        return session, state, targets, speakers

    def _synthesize_utterances(
        self,
        context: TaskContext,
        command: SynthesizeDubbingCommand,
        session: DubbingSession,
        state: TimelineState,
        targets: list[DubbingUtterance],
        speakers: dict[str, DubbingSpeaker],
        synthesis: DubbingSynthesisSession,
        version: str,
        output_root: Path,
        cache_root: Path,
        published_outputs: list[Path],
    ) -> dict[str, DubbingUtterance]:
        updated: dict[str, DubbingUtterance] = {}
        for index, utterance in enumerate(targets, start=1):
            context.cancellation.raise_if_requested()
            existing_output = utterance.output_path and self.documents.assets.is_regular_file(
                self.project_dir / utterance.output_path
            )
            if (
                not command.regenerate
                and utterance.status in {"generated", "needs_review"}
                and existing_output
            ):
                updated[utterance.id] = utterance
                continue
            updated[utterance.id] = self._synthesize_utterance(
                context,
                session,
                state,
                utterance,
                speakers[utterance.speaker_id],
                synthesis,
                version,
                output_root,
                cache_root,
                published_outputs,
            )
            context.report(
                OperationProgress.determinate(
                    "dubbing_synthesizing_utterances",
                    completed=index,
                    total=len(targets),
                    unit="items",
                )
            )
        return updated

    def _synthesize_utterance(
        self,
        context: TaskContext,
        session: DubbingSession,
        state: TimelineState,
        utterance: DubbingUtterance,
        speaker: DubbingSpeaker,
        synthesis: DubbingSynthesisSession,
        version: str,
        output_root: Path,
        cache_root: Path,
        published_outputs: list[Path],
    ) -> DubbingUtterance:
        primary = speaker.primary_reference
        assert primary is not None
        reference_paths: dict[str, Path] = {}
        for reference in speaker.references:
            path = self.project_dir / reference.path
            if self._sha256(path) != reference.sha256:
                raise RuntimeError(f"{speaker.display_name} 的参考音频已被修改")
            reference_paths[reference.id] = path
        reference_fingerprint = self._reference_fingerprint(speaker)
        auxiliary_paths: list[str | Path] = []
        for reference in speaker.references:
            if not reference.primary:
                auxiliary_paths.append(reference_paths[reference.id])
            if len(auxiliary_paths) == 5:
                break
        seed = self._utterance_seed(session, utterance)
        raw = content_addressed_child_path(
            cache_root,
            (f"{context.task.id}:{utterance.id}:{utterance.target_text}:{reference_fingerprint}:{seed}"),
            namespace="raw",
            suffix=".wav",
        )
        first = self._invoke_synthesis(
            synthesis,
            session,
            utterance,
            primary,
            reference_paths[primary.id],
            auxiliary_paths,
            raw,
            seed,
            speed_factor=1.0,
        )
        available_seconds = self._available_seconds(
            utterance,
            session.utterances,
            session.settings.borrow_gap_frames,
            state.sequence.profile,
        )
        speed_factor = 1.0
        selected = first
        if first.duration_seconds > available_seconds:
            speed_factor = min(
                session.settings.maximum_speed_factor,
                first.duration_seconds / available_seconds,
            )
            selected = self._invoke_synthesis(
                synthesis,
                session,
                utterance,
                primary,
                reference_paths[primary.id],
                auxiliary_paths,
                raw,
                seed,
                speed_factor=speed_factor,
            )
        too_long = selected.duration_seconds > available_seconds + 0.02
        final_path = content_addressed_child_path(
            output_root,
            (
                f"{context.task.id}:{utterance.id}:"
                f"{utterance.target_text}:{reference_fingerprint}:"
                f"{speed_factor:.6f}:{seed}:{version}:"
                f"{selected.sha256}"
            ),
            namespace="line",
            suffix=".wav",
        )
        final_existed = self.documents.assets.is_regular_file(final_path)
        normalized = self.runtime.normalize_utterance(
            selected.output_path,
            final_path,
            target_seconds=(None if too_long else available_seconds),
            sample_rate=state.sequence.profile.audio_sample_rate,
            check_cancelled=(context.cancellation.raise_if_requested),
        )
        if not final_existed:
            published_outputs.append(normalized.path)
        issue = "译文在最大语速下仍超出可用时长，请缩短译文"
        issues = [value for value in utterance.issues if value != issue]
        if too_long:
            issues.append(issue)
        return utterance.model_copy(
            update={
                "status": ("needs_review" if too_long else "generated"),
                "review_status": ("needs_review" if too_long else utterance.review_status),
                "output_path": normalized.path.relative_to(self.project_dir).as_posix(),
                "output_sha256": normalized.sha256,
                "natural_duration_seconds": first.duration_seconds,
                "fitted_duration_seconds": normalized.duration_seconds,
                "speed_factor": speed_factor,
                "seed": seed,
                "reference_sha256": reference_fingerprint,
                "issues": issues,
            }
        )

    def _invoke_synthesis(
        self,
        synthesis: DubbingSynthesisSession,
        session: DubbingSession,
        utterance: DubbingUtterance,
        primary: DubbingReference,
        reference_audio: Path,
        auxiliary_reference_audio: list[str | Path],
        output_path: Path,
        seed: int,
        *,
        speed_factor: float,
    ) -> DubbingSynthesisOutput:
        return synthesis.synthesize(
            text=utterance.target_text,
            text_language=self._gpt_language(session.target_language),
            reference_audio=reference_audio,
            reference_text=primary.text,
            reference_language=self._gpt_language(primary.language),
            output_path=output_path,
            auxiliary_reference_audio=auxiliary_reference_audio,
            speed_factor=speed_factor,
            seed=seed,
            overwrite=True,
        )

    def _assemble_master(
        self,
        context: TaskContext,
        session: DubbingSession,
        state: TimelineState,
        utterances: list[DubbingUtterance],
        published_outputs: list[Path],
    ) -> PreparedDubbingAudio:
        if any(item.output_path is None for item in utterances):
            raise RuntimeError("仍有句子尚未生成，无法合成配音母版")
        inputs: list[tuple[str | Path, float]] = []
        for item in utterances:
            assert item.output_path is not None
            path = self.project_dir / item.output_path
            if self._sha256(path) != item.output_sha256:
                raise RuntimeError("已生成的配音句子文件发生变化，请重新合成")
            inputs.append(
                (
                    path,
                    self._frame_seconds(
                        item.start_frame,
                        state.sequence.profile,
                    ),
                )
            )
        master_identity = ":".join(
            [context.task.id] + [f"{item.id}:{item.output_sha256}" for item in utterances]
        )
        master_path = content_addressed_child_path(
            self.project_dir / "generated" / "dubbing" / session.id,
            master_identity,
            namespace="master",
            suffix=".wav",
        )
        master_existed = self.documents.assets.is_regular_file(master_path)
        master = self.runtime.assemble_master(
            inputs,
            master_path,
            minimum_duration_seconds=self._frame_seconds(
                state.duration_frames,
                state.sequence.profile,
            ),
            sample_rate=state.sequence.profile.audio_sample_rate,
            check_cancelled=(context.cancellation.raise_if_requested),
        )
        if not master_existed:
            published_outputs.append(master.path)
        return master

    def _commit(
        self,
        context: TaskContext,
        command: CommitDubbingCommand,
    ) -> TaskCompletion:
        session = self.documents.dubbing.get_session(command.session_id)
        if session.sequence_id != command.sequence_id:
            raise ValueError("配音方案不属于当前序列")
        if session.status != "synthesized" or not session.master_path:
            raise ValueError("请先完成全部配音合成")
        master_path = self.project_dir / session.master_path
        if self._sha256(master_path) != session.master_sha256:
            raise RuntimeError("配音母版文件已被修改，请重新合成")
        prepared_asset: PreparedAssetRegistration = self.assets.prepare_output(
            master_path,
            AssetOrigin.GENERATED,
        )
        if prepared_asset.asset.kind != AssetKind.AUDIO:
            raise RuntimeError("配音母版没有被识别为音频素材")

        def commit_to_timeline() -> None:
            current = self.documents.dubbing.get_session(session.id)
            if current.revision != session.revision:
                raise RuntimeError("配音方案在提交期间已被修改，请重新提交")
            state = self.documents.timeline.load_timeline(session.sequence_id)
            if state.sequence.timeline_revision != session.source_timeline_revision:
                raise RuntimeError("时间轴在配音方案创建后已变化，请重新创建配音方案")
            asset = self.assets.commit_prepared(prepared_asset)
            editor: TimelineEditor = self.timeline_provider(session.sequence_id)
            master_duration = session.master_duration_seconds
            if master_duration is None:
                raise RuntimeError("配音母版缺少时长信息")
            duration = max(
                1,
                round(
                    master_duration
                    * state.sequence.profile.fps_numerator
                    / state.sequence.profile.fps_denominator
                ),
            )
            existing_track = next(
                (item for item in editor.state.tracks if item.id == session.committed_track_id),
                None,
            )
            existing_clip = next(
                (item for item in editor.state.clips if item.id == session.committed_clip_id),
                None,
            )
            if existing_track is not None and existing_clip is not None:
                track = existing_track
                clip = editor.replace_clip_source(existing_clip.id, asset.id)
                clip = editor.trim_clip(
                    clip.id,
                    timeline_start=0,
                    source_in=0,
                    duration=duration,
                )
            else:
                track = editor.add_track(TrackKind.AUDIO, command.track_name)
                clip = editor.add_clip(
                    track_id=track.id,
                    asset_id=asset.id,
                    timeline_start=0,
                    source_in=0,
                    duration=duration,
                )
            if command.mute_source_dialogue:
                source_track = next(
                    item for item in editor.state.tracks if item.id == session.dialogue_track_id
                )
                editor.set_track_state(
                    source_track.id,
                    enabled=source_track.enabled,
                    locked=source_track.locked,
                    muted=True,
                    solo=False,
                    audio_bus_id=source_track.audio_bus_id,
                )
            committed = session.model_copy(
                update={
                    "status": "committed",
                    "master_asset_id": asset.id,
                    "committed_track_id": track.id,
                    "committed_clip_id": clip.id,
                    "source_timeline_revision": editor.state.sequence.timeline_revision,
                }
            )
            self.documents.dubbing.save_session(
                committed,
                expected_revision=session.revision,
            )

        context.defer_project_change(commit_to_timeline)
        artifact = ArtifactReference.project(self.project_dir, master_path)
        return TaskCompletion.with_artifacts(
            artifact,
            outcome=DubbingTaskOutcome(
                session_id=session.id,
                phase="committed",
                master=artifact,
            ),
        )

    @staticmethod
    def _frame_seconds(frame: int, profile) -> float:
        return frame * profile.fps_denominator / profile.fps_numerator

    @classmethod
    def _available_seconds(
        cls,
        utterance: DubbingUtterance,
        utterances: list[DubbingUtterance],
        borrow_gap_frames: int,
        profile,
    ) -> float:
        index = next(i for i, item in enumerate(utterances) if item.id == utterance.id)
        next_start = (
            utterances[index + 1].start_frame
            if index + 1 < len(utterances)
            else utterance.end_frame + borrow_gap_frames
        )
        borrowed = min(
            borrow_gap_frames,
            max(0, next_start - utterance.end_frame),
        )
        return cls._frame_seconds(
            utterance.end_frame - utterance.start_frame + borrowed,
            profile,
        )

    @staticmethod
    def _utterance_seed(
        session: DubbingSession,
        utterance: DubbingUtterance,
    ) -> int:
        basis = str(session.settings.seed) if session.settings.seed >= 0 else session.id
        digest = hashlib.sha256(f"{basis}:{utterance.id}:{utterance.target_text}".encode()).digest()
        return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF

    @staticmethod
    def _reference_fingerprint(speaker: DubbingSpeaker) -> str:
        identity = "\n".join(
            "|".join(
                (
                    reference.id,
                    reference.sha256,
                    "primary" if reference.primary else "auxiliary",
                    reference.text if reference.primary else "",
                    reference.language if reference.primary else "",
                )
            )
            for reference in speaker.references
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _gpt_language(value: str) -> str:
        normalized = value.casefold().replace("_", "-")
        if normalized.startswith("zh"):
            return "zh"
        if normalized.startswith("en"):
            return "en"
        if normalized.startswith("ja"):
            return "ja"
        if normalized.startswith("ko"):
            return "ko"
        return "auto"

    def _sha256(self, path: Path) -> str:
        return self.runtime.file_sha256(path)

    def _archive_unrecorded(
        self,
        paths: list[Path],
        error: BaseException,
    ) -> None:
        if not paths:
            return
        try:
            self.runtime.archive_unrecorded_outputs(paths)
        except BaseException as archive_error:
            error.add_note(f"未登记的配音文件无法移入失败归档：{archive_error}")
