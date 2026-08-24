import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl

from mediaflow.application.asset_service import AssetService
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.desktop.controllers.controller_hub import EditorControllers
from mediaflow.domain.audio import AudioEffect
from mediaflow.domain.enums import (
    AssetKind,
    AudioEffectKind,
    ColorMode,
    ExportFormat,
    TaskStatus,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.exports import ExportPreset, SubtitleStyle, WatermarkOverlay
from mediaflow.domain.project import ProjectProfile, SequenceInOut
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.storage_names import (
    DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY,
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    export_quality_directory,
    safe_child_path,
    utf16_units,
)
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import ExportHighlightsCommand, ExportSequenceCommand
from mediaflow.domain.tasks import ExportTaskOutcome
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.encoder_policy import (
    ResolvedVideoEncoder,
    VideoEncoderPolicyResolver,
)
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import (
    MltExportService,
    SequenceBoundaryAnalysisService,
    TimelineCompiler,
)
from mediaflow.infrastructure.mlt.export_service import ExportAttemptError
from mediaflow.infrastructure.mlt.export_types import MltExportRequest
from mediaflow.infrastructure.mlt.graph import MltGraph
from mediaflow.infrastructure.output_reservation import (
    output_set_transaction,
    reserve_output,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from tests.v2.real_media import generate_real_media


@pytest.mark.parametrize("returncode", [-1073741819, 0])
def test_mlt_incomplete_process_attempt_gets_one_bounded_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    service = object.__new__(MltExportService)
    archived = tmp_path / "first-crash.mp4"
    attempts: list[str] = []

    def render_attempt(*_args, attempt_label: str, **_kwargs) -> dict:
        attempts.append(attempt_label)
        if len(attempts) == 1:
            raise ExportAttemptError(
                f"melt exited with code {returncode}",
                archived_output=archived,
                returncode=returncode,
            )
        return {"format": {"duration": "1.0"}}

    monkeypatch.setattr(service, "_render_attempt", render_attempt)
    probe, recovered = service._render_with_process_recovery(
        None,
        SimpleNamespace(video_codec="libx264"),
        tmp_path / "timeline.mlt",
        tmp_path / "output.mp4",
        None,
        start_frame=0,
        end_frame=1,
        attempt_label="requested",
    )

    assert attempts == ["requested", "requested-retry"]
    assert recovered == (archived,)
    assert probe["format"]["duration"] == "1.0"
    assert not ExportAttemptError(
        "ordinary encoder failure",
        archived_output=None,
        returncode=1,
    ).is_retryable_process_failure


def test_sequence_export_refuses_to_overwrite_without_explicit_permission(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    with ProjectRepository.create(tmp_path / "No Overwrite", "No Overwrite") as repository:
        output = tmp_path / "existing.mp4"
        output.write_bytes(b"user-output")
        state = repository.timeline.load_timeline(repository.projects.get_project().main_sequence_id)
        preset = ExportPreset(
            name="No overwrite",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
        )

        service = MltExportService(TimelineCompiler(repository, RuntimeContext.discover().paths), paths)
        with pytest.raises(FileExistsError, match="already exists"):
            service.export(
                state,
                preset,
                output,
            )

        assert output.read_bytes() == b"user-output"
        reserved_output = tmp_path / "reserved.mp4"
        with reserve_output(reserved_output, runtime_dir=paths.runtime_dir):
            with pytest.raises(RuntimeError, match="already writing"):
                MltExportService(TimelineCompiler(repository, RuntimeContext.discover().paths), paths).export(
                    state,
                    preset,
                    reserved_output,
                )
        assert not reserved_output.exists()


def test_sequence_export_resolves_software_policy_without_codec_mismatch(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    with ProjectRepository.create(
        tmp_path / "Codec Mismatch",
        "Codec Mismatch",
    ) as repository:
        state = repository.timeline.load_timeline(repository.projects.get_project().main_sequence_id)
        output = tmp_path / "not-created" / "mismatch.mp4"
        graph_root = repository.project_dir / "cache" / "mlt"

        preset = ExportPreset(
            name="Portable software policy",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
        )
        resolved = VideoEncoderPolicyResolver(paths).resolve(
            preset.format,
            preset.encoder_policy,
        )
        assert resolved.codec == "libx264"
        MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths),
            paths,
        ).preflight(state, preset, output)

        assert not output.parent.exists()
        assert not graph_root.exists()


def test_sequence_export_rejects_a_mislabelled_container_before_side_effects(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    with ProjectRepository.create(
        tmp_path / "Container Mismatch",
        "Container Mismatch",
    ) as repository:
        state = repository.timeline.load_timeline(repository.projects.get_project().main_sequence_id)
        output = tmp_path / "not-created" / "mislabelled.mkv"
        graph_root = repository.project_dir / "cache" / "mlt"

        with pytest.raises(
            ValueError,
            match="扩展名与封装格式不一致",
        ):
            MltExportService(
                TimelineCompiler(repository, RuntimeContext.discover().paths),
                paths,
            ).preflight(
                state,
                ExportPreset(
                    name="Mislabelled MP4",
                    format=ExportFormat.H264,
                    container="mp4",
                    encoder_policy={"mode": "software"},
                    audio_codec="aac",
                    pixel_format="yuv420p",
                ),
                output,
            )

        assert not output.parent.exists()
        assert not graph_root.exists()


def test_atomic_export_batch_archives_staged_output_when_a_later_render_fails(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "atomic-batch-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    with ProjectRepository.create(
        tmp_path / "Atomic Batch",
        "Atomic Batch",
    ) as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
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
        valid_preset = ExportPreset(
            name="First valid export",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
            preset="ultrafast",
        )
        invalid_preset = ExportPreset.model_validate(
            {
                **valid_preset.model_dump(mode="python"),
                "name": "Second invalid export",
                "encoder_policy": {
                    "mode": "prefer_hardware",
                    "vendor": "apple",
                },
            },
        )
        first_output = repository.project_dir / "exports" / "first.mp4"
        second_output = repository.project_dir / "exports" / "second.mp4"

        class DeterministicResolver:
            @staticmethod
            def resolve(export_format, policy):
                if policy.vendor == "apple":
                    return ResolvedVideoEncoder(
                        "mediaflow_missing_encoder",
                        "amf",
                        False,
                    )
                return ResolvedVideoEncoder("libx264", "software", False)

        with pytest.raises(RuntimeError):
            MltExportService(
                TimelineCompiler(repository, RuntimeContext.discover().paths),
                paths,
                encoder_resolver=DeterministicResolver(),
            ).export_many(
                (
                    MltExportRequest(
                        state=editor.state,
                        preset=valid_preset,
                        output_path=first_output,
                    ),
                    MltExportRequest(
                        state=editor.state,
                        preset=invalid_preset,
                        output_path=second_output,
                    ),
                )
            )

        assert not first_output.exists()
        assert not second_output.exists()
        failed_directory = first_output.parent / "MediaFlow Pro Failed Exports"
        archived = list(failed_directory.glob("*.mp4"))
        assert archived
        consumable = []
        for path in archived:
            try:
                probe = MediaProbe(paths).probe(
                    path,
                    timeline_profile=editor.state.sequence.profile,
                )
            except Exception:
                continue
            if probe.metadata.has_video:
                consumable.append(path)
        assert consumable
        assert not any(path.name.startswith(".") for path in first_output.parent.glob("*.mp4"))


def test_sequence_export_rejects_an_unusable_temporary_sibling_before_compiling(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    with ProjectRepository.create(
        tmp_path / "Export Path Preflight",
        "Export Path Preflight",
    ) as repository:
        state = repository.timeline.load_timeline(repository.projects.get_project().main_sequence_id)
        output_parent = tmp_path
        output = output_parent / "video.mp4"
        while utf16_units(str(output_parent.resolve())) + 1 + 64 <= WINDOWS_INTEROP_PATH_UTF16_LIMIT:
            output_parent /= "deep-video-export-directory"
            output = output_parent / "video.mp4"
        graph_root = repository.project_dir / "cache" / "mlt"

        assert utf16_units(str(output.resolve())) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        assert not output_parent.exists()
        assert not graph_root.exists()
        with pytest.raises(ValueError, match="目录过深"):
            MltExportService(
                TimelineCompiler(repository, RuntimeContext.discover().paths),
                paths,
            ).export(
                state,
                ExportPreset(
                    name="Path preflight",
                    format=ExportFormat.H264,
                    container="mp4",
                    encoder_policy={"mode": "software"},
                    audio_codec="aac",
                    pixel_format="yuv420p",
                ),
                output,
            )
        assert not output_parent.exists()
        assert not graph_root.exists()


@pytest.mark.parametrize(
    "suffix_length",
    (24, 60),
    ids=("failure-archive", "staging-sibling"),
)
def test_export_task_rejects_long_suffix_before_any_output_or_render_side_effect(
    max_project_path: Path,
    suffix_length: int,
) -> None:
    paths = RuntimeContext.discover().paths
    repository = ProjectRepository.create(
        max_project_path,
        "Output Workspace Preflight",
    )
    project = EditorProject(
        repository,
        settings=ServiceSettings(),
        paths=paths,
    )
    try:
        output_dir = repository.project_dir / DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY
        output = output_dir / f"delivery.{('x' * suffix_length)}"
        assert (
            utf16_units(str(output_dir.resolve())) + 1 + OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS
            == WINDOWS_INTEROP_PATH_UTF16_LIMIT
        )
        assert utf16_units(str(output.resolve())) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        assert not output_dir.exists()
        assert not (repository.project_dir / "cache" / "mlt").exists()
        assert not (repository.project_dir / "cache" / "web").exists()

        sequence_id = repository.projects.get_project().main_sequence_id
        task = project.start_task(
            ExportSequenceCommand(
                sequence_id=sequence_id,
                output_path=str(output),
                format=ExportFormat.H264,
                preset=ExportPreset(
                    name="Long custom muxer path",
                    format=ExportFormat.H264,
                    container="x" * suffix_length,
                    encoder_policy={"mode": "software"},
                    audio_codec="aac",
                    pixel_format="yuv420p",
                ),
            ),
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=30)

        assert completed.status == TaskStatus.FAILED
        assert "文件路径过深" in (completed.error or "")
        assert not completed.artifacts
        assert not output_dir.exists()
        assert not (repository.project_dir / "cache" / "mlt").exists()
        assert not (repository.project_dir / "cache" / "web").exists()
    finally:
        project.close()


@pytest.mark.parametrize(
    "failure_phase",
    (
        "backup_first",
        "backup_second",
        "publish_first",
        "publish_second",
    ),
)
def test_output_set_fault_matrix_restores_every_precommit_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    first = (tmp_path / "matrix.mp4").resolve()
    second = (tmp_path / "matrix.English.srt").resolve()
    first.write_bytes(b"old-video")
    second.write_bytes(b"old-subtitle")
    original_replace = Path.replace
    injected = False

    with pytest.raises(OSError, match=failure_phase):
        with output_set_transaction(
            (first, second),
            overwrite=True,
            runtime_dir=tmp_path / "runtime",
        ) as transaction:
            first_stage = transaction.temporary_path(
                first,
                "video",
            )
            second_stage = transaction.temporary_path(
                second,
                "subtitle",
            )
            first_stage.write_bytes(b"new-video")
            second_stage.write_bytes(b"new-subtitle")

            def fail_selected_transition(
                source: Path,
                destination: str | Path,
            ) -> Path:
                nonlocal injected
                target = Path(destination).resolve()
                selected = {
                    "backup_first": (source == first and target.name.startswith(".mf-previous-")),
                    "backup_second": (source == second and target.name.startswith(".mf-previous-")),
                    "publish_first": (source == first_stage and target == first),
                    "publish_second": (source == second_stage and target == second),
                }[failure_phase]
                if selected and not injected:
                    injected = True
                    raise OSError(failure_phase)
                return original_replace(source, destination)

            monkeypatch.setattr(
                Path,
                "replace",
                fail_selected_transition,
            )
            transaction.commit()

    assert injected is True
    assert first.read_bytes() == b"old-video"
    assert second.read_bytes() == b"old-subtitle"
    archived_contents = {item.read_bytes() for item in (tmp_path / "MediaFlow Pro Failed Exports").iterdir()}
    assert archived_contents == {b"new-video", b"new-subtitle"}
    assert not [item for item in tmp_path.iterdir() if item.is_file() and item.name.startswith(".mf-")]


def test_output_set_retries_transient_failures_during_rollback_and_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = (tmp_path / "rollback.mp4").resolve()
    second = (tmp_path / "rollback.English.srt").resolve()
    first.write_bytes(b"old-video")
    second.write_bytes(b"old-subtitle")
    original_replace = Path.replace
    failed_publish = False
    failed_remove = False
    failed_restore = False
    failed_archive = False

    with pytest.raises(OSError, match="publish interruption"):
        with output_set_transaction(
            (first, second),
            overwrite=True,
            runtime_dir=tmp_path / "runtime",
        ) as transaction:
            first_stage = transaction.temporary_path(
                first,
                "video",
            )
            second_stage = transaction.temporary_path(
                second,
                "subtitle",
            )
            first_stage.write_bytes(b"new-video")
            second_stage.write_bytes(b"new-subtitle")

            def interrupt_each_recovery_phase_once(
                source: Path,
                destination: str | Path,
            ) -> Path:
                nonlocal failed_publish
                nonlocal failed_remove
                nonlocal failed_restore
                nonlocal failed_archive
                target = Path(destination).resolve()
                if not failed_publish and source == second_stage and target == second:
                    failed_publish = True
                    raise OSError("publish interruption")
                if not failed_remove and source == first and target.name.startswith(".mf-rollback-"):
                    failed_remove = True
                    raise OSError("rollback remove interruption")
                if not failed_restore and source.name.startswith(".mf-previous-") and target == second:
                    failed_restore = True
                    raise OSError("rollback restore interruption")
                if not failed_archive and target.parent.name == "MediaFlow Pro Failed Exports":
                    failed_archive = True
                    raise OSError("archive interruption")
                return original_replace(source, destination)

            monkeypatch.setattr(
                Path,
                "replace",
                interrupt_each_recovery_phase_once,
            )
            transaction.commit()

    assert all(
        (
            failed_publish,
            failed_remove,
            failed_restore,
            failed_archive,
        )
    )
    assert first.read_bytes() == b"old-video"
    assert second.read_bytes() == b"old-subtitle"
    archived_contents = {item.read_bytes() for item in (tmp_path / "MediaFlow Pro Failed Exports").iterdir()}
    assert archived_contents == {b"new-video", b"new-subtitle"}
    assert not [item for item in tmp_path.iterdir() if item.is_file() and item.name.startswith(".mf-")]


def test_long_output_set_uses_short_siblings_for_commit_and_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path
    first_name = f"{'feature-export-' * 6}video.mp4"
    second_name = f"{'feature-export-' * 6}subtitle.srt"
    while len(str(parent / first_name)) < 215:
        parent /= "deep-user-export-folder"
    parent.mkdir(parents=True)
    first = (parent / first_name).resolve()
    second = (parent / second_name).resolve()
    assert len(str(first).encode("utf-16-le")) // 2 <= 240
    assert len(str(second).encode("utf-16-le")) // 2 <= 240
    first.write_bytes(b"old-video")
    second.write_bytes(b"old-subtitle")

    with output_set_transaction(
        (first, second),
        overwrite=True,
        runtime_dir=tmp_path / "runtime",
    ) as transaction:
        first_stage = transaction.temporary_path(first, "video")
        second_stage = transaction.temporary_path(second, "subtitle")
        assert first.stem not in first_stage.name
        assert second.stem not in second_stage.name
        assert len(str(first_stage)) < len(str(first))
        assert len(str(second_stage)) < len(str(second))
        first_stage.write_bytes(b"committed-video")
        second_stage.write_bytes(b"committed-subtitle")
        transaction.commit()

    assert first.read_bytes() == b"committed-video"
    assert second.read_bytes() == b"committed-subtitle"

    original_replace = Path.replace
    interrupted = False
    with pytest.raises(OSError, match="long publish interruption"):
        with output_set_transaction(
            (first, second),
            overwrite=True,
            runtime_dir=tmp_path / "runtime",
        ) as transaction:
            first_stage = transaction.temporary_path(first, "video")
            second_stage = transaction.temporary_path(second, "subtitle")
            first_stage.write_bytes(b"failed-video")
            second_stage.write_bytes(b"failed-subtitle")

            def interrupt_second_publish(
                source: Path,
                destination: str | Path,
            ) -> Path:
                nonlocal interrupted
                if not interrupted and source == second_stage and Path(destination).resolve() == second:
                    interrupted = True
                    raise OSError("long publish interruption")
                return original_replace(source, destination)

            monkeypatch.setattr(Path, "replace", interrupt_second_publish)
            transaction.commit()

    assert interrupted is True
    assert first.read_bytes() == b"committed-video"
    assert second.read_bytes() == b"committed-subtitle"
    assert not [item for item in parent.iterdir() if item.is_file() and item.name.startswith(".mf-")]


def test_output_set_rejects_a_native_tool_path_beyond_the_shared_budget(
    tmp_path: Path,
) -> None:
    parent = tmp_path
    destination_name = f"{'long-native-output-' * 6}.mp4"
    while len(str(parent / destination_name).encode("utf-16-le")) // 2 <= 240:
        parent /= "deep-output-folder"
    parent.mkdir(parents=True)
    destination = parent / destination_name

    with pytest.raises(ValueError, match="路径过深"):
        with output_set_transaction(
            (destination,),
            overwrite=False,
            runtime_dir=tmp_path / "runtime",
        ):
            raise AssertionError("invalid output path entered the transaction")

    assert not destination.exists()
    assert not list(parent.glob(".mf-*.tmp.mp4"))


def test_video_solo_is_kind_local_and_disabled_offline_tracks_are_not_compiled(
    tmp_path: Path,
) -> None:
    first_source = tmp_path / "first.mp4"
    second_source = tmp_path / "second.mp4"
    first_source.write_bytes(b"first-source")
    second_source.write_bytes(b"second-source")
    with ProjectRepository.create(tmp_path / "Track Selection", "Track Selection") as repository:
        first_asset = repository.assets.import_external_asset(first_source, AssetKind.VIDEO)
        second_asset = repository.assets.import_external_asset(second_source, AssetKind.VIDEO)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        first_track = editor.add_track(TrackKind.VIDEO, "Visible Solo")
        second_track = editor.add_track(TrackKind.VIDEO, "Disabled Offline")
        first_clip = editor.add_clip(
            track_id=first_track.id,
            asset_id=first_asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        editor.add_clip(
            track_id=second_track.id,
            asset_id=second_asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        repository.assets.update_asset(
            second_asset.model_copy(update={"path": str(tmp_path / "never-created.mp4")})
        )
        editor.set_track_state(
            first_track.id,
            enabled=True,
            locked=False,
            muted=False,
            solo=True,
        )
        editor.set_track_state(
            second_track.id,
            enabled=False,
            locked=False,
            muted=False,
            solo=False,
        )

        document = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(editor.state)
        root = ET.fromstring(document.xml)
        tractor = root.find("./tractor[@id='tractor0']")
        assert tractor is not None
        producers = [node.get("producer") for node in tractor.findall("track")]
        assert MltGraph.playlist_id(first_track.id) in producers
        assert MltGraph.playlist_id(second_track.id) not in producers
        assert MltGraph.producer_id(first_clip.id) in document.xml
        assert str(tmp_path / "never-created.mp4") not in document.xml


def test_visual_effect_stack_compiles_in_persisted_order_for_preview_and_export(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-source-boundary")
    with ProjectRepository.create(tmp_path / "Visual Effects", "Visual Effects") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        adjustment = editor.add_clip_visual_effect(
            clip.id,
            VisualEffectKind.COLOR_ADJUSTMENT,
        )
        blur = editor.add_clip_visual_effect(
            clip.id,
            VisualEffectKind.GAUSSIAN_BLUR,
        )
        editor.update_clip_visual_effect(
            clip.id,
            adjustment.id,
            enabled=True,
            parameters={"brightness": 0.2, "contrast": 1.25, "saturation": 0.8},
        )

        document = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(editor.state)

        assert document.xml.index(f"visual_effect_{adjustment.id}") < document.xml.index(
            f"visual_effect_{blur.id}"
        )
        assert "avfilter.eq" in document.xml
        assert "av.brightness" in document.xml
        assert "0.2" in document.xml
        assert "avfilter.gblur" in document.xml


def test_timeline_compiler_resolves_one_shared_asset_source_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "shared-source.mp4"
    source.write_bytes(b"shared-source")
    with ProjectRepository.create(tmp_path / "Shared Source", "Shared Source") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=10,
        )
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=10,
            source_in=0,
            duration=10,
        )
        original = repository.assets.resolve_asset_path
        calls: list[str] = []

        def resolve_asset_path(current) -> Path:
            calls.append(current.id)
            return original(current)

        monkeypatch.setattr(repository.assets, "resolve_asset_path", resolve_asset_path)
        document = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(
            editor.state,
            native_preview=True,
        )

        assert calls == [asset.id]
        assert document.source_paths == (source.resolve(),)


def test_visual_effect_stack_changes_real_exported_pixels(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    assert paths.melt is not None
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=160, height=90)
    with ProjectRepository.create(tmp_path / "Visual Effect Render", "Visual Effect Render") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        asset = AssetService(repository, MediaProbe(paths)).adopt_main_profile_from_video(asset.id)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        preset = ExportPreset(
            name="Visual effect pixels",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
            quality_value=18,
            preset="ultrafast",
            gop_frames=25,
        )
        service = MltExportService(TimelineCompiler(repository, RuntimeContext.discover().paths), paths)
        baseline = service.export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "baseline.mp4",
        )
        effect = editor.add_clip_visual_effect(
            clip.id,
            VisualEffectKind.COLOR_ADJUSTMENT,
        )
        editor.update_clip_visual_effect(
            clip.id,
            effect.id,
            enabled=True,
            parameters={"brightness": 0.0, "contrast": 1.0, "saturation": 0.0},
        )
        filtered = service.export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "grayscale.mp4",
        )

        baseline_rgb = _frame_rgb_means(baseline.output_path, paths, [10])[0]
        filtered_rgb = _frame_rgb_means(filtered.output_path, paths, [10])[0]
        baseline_spread = max(baseline_rgb) - min(baseline_rgb)
        filtered_spread = max(filtered_rgb) - min(filtered_rgb)
        assert baseline_spread > 5
        assert filtered_spread < baseline_spread * 0.35


def test_lut_visual_effect_changes_real_exported_pixels(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    assert paths.melt is not None
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=160, height=90)
    cube = tmp_path / "invert.cube"
    cube.write_text(
        """TITLE "Invert"
LUT_3D_SIZE 2
1 1 1
0 1 1
1 0 1
0 0 1
1 1 0
0 1 0
1 0 0
0 0 0
""",
        encoding="utf-8",
    )
    with ProjectRepository.create(tmp_path / "LUT Render", "LUT Render") as repository:
        assets = AssetService(
            repository,
            MediaProbe(paths),
            fingerprint_file,
        )
        asset = assets.import_external(source)
        asset = assets.adopt_main_profile_from_video(asset.id)
        lut = assets.import_lut(cube)
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        preset = ExportPreset(
            name="LUT pixels",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
            quality_value=18,
            preset="ultrafast",
            gop_frames=25,
        )
        service = MltExportService(TimelineCompiler(repository, paths), paths)
        baseline = service.export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "baseline.mp4",
        )
        editor.add_clip_visual_effect(
            clip.id,
            VisualEffectKind.LUT_3D,
            resource_asset_id=lut.id,
        )
        filtered = service.export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "inverted.mp4",
        )

        baseline_rgb = _frame_rgb_means(baseline.output_path, paths, [10])[0]
        filtered_rgb = _frame_rgb_means(filtered.output_path, paths, [10])[0]
        assert sum(
            abs(left - right)
            for left, right in zip(baseline_rgb, filtered_rgb, strict=True)
        ) > 80


def test_timeline_compiler_rejects_active_native_source_beyond_shared_budget(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    with ProjectRepository.create(
        tmp_path / "Native Source Budget",
        "Native Source Budget",
    ) as repository:
        asset = repository.assets.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        overlong_parent = tmp_path
        while utf16_units(str(overlong_parent / "source.mp4")) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT:
            overlong_parent /= "deep-native-source"
        repository.assets.update_asset(
            asset.model_copy(update={"path": str(overlong_parent / "source.mp4")})
        )

        with pytest.raises(ValueError, match="路径过深"):
            TimelineCompiler(repository, RuntimeContext.discover().paths).compile(editor.state)


def test_linked_video_dialogue_drives_ducking_and_video_solo_does_not_mute_audio(
    tmp_path: Path,
) -> None:
    dialogue_source = tmp_path / "dialogue.mp4"
    music_source = tmp_path / "music.wav"
    dialogue_source.write_bytes(b"dialogue-source")
    music_source.write_bytes(b"music-source")
    with ProjectRepository.create(tmp_path / "Linked Ducking", "Linked Ducking") as repository:
        dialogue_asset = repository.assets.import_external_asset(
            dialogue_source,
            AssetKind.VIDEO,
        )
        dialogue_asset = repository.assets.update_asset(
            dialogue_asset.model_copy(
                update={
                    "metadata": dialogue_asset.metadata.model_copy(
                        update={
                            "duration_frames": 90,
                            "has_video": True,
                            "has_audio": True,
                        }
                    )
                }
            )
        )
        music_asset = repository.assets.import_external_asset(music_source, AssetKind.AUDIO)
        music_asset = repository.assets.update_asset(
            music_asset.model_copy(
                update={
                    "metadata": music_asset.metadata.model_copy(
                        update={"duration_frames": 90, "has_audio": True}
                    )
                }
            )
        )
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        buses = repository.audio.list_audio_buses(project.main_sequence_id)
        dialogue_bus = next(bus for bus in buses if bus.name == "对白")
        music_bus = next(bus for bus in buses if bus.name == "音乐")
        video_track = editor.add_track(
            TrackKind.VIDEO,
            "Linked Dialogue",
            audio_bus_id=dialogue_bus.id,
        )
        music_track = editor.add_track(
            TrackKind.AUDIO,
            "Music",
            audio_bus_id=music_bus.id,
        )
        editor.add_clip(
            track_id=video_track.id,
            asset_id=dialogue_asset.id,
            timeline_start=30,
            source_in=0,
            duration=30,
        )
        editor.add_clip(
            track_id=music_track.id,
            asset_id=music_asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        editor.set_track_state(
            video_track.id,
            enabled=True,
            locked=False,
            muted=False,
            solo=True,
            audio_bus_id=dialogue_bus.id,
        )
        effect = AudioEffect(
            bus_id=music_bus.id,
            kind=AudioEffectKind.DUCKING,
            position=0,
            parameters={
                "driver_bus_id": dialogue_bus.id,
                "reduction_db": -18.0,
                "attack_ms": 0.0,
                "release_ms": 0.0,
            },
        )
        repository.audio.save_audio_effect(effect)

        document = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(editor.state)

        assert f"effect_{effect.id}" in document.xml
        assert "30=-18dB" in document.xml
        linked_audio_track = next(
            track
            for track in editor.state.tracks
            if track.id
            == next(item for item in editor.state.tracks if item.id == video_track.id).linked_audio_track_id
        )
        assert MltGraph.audio_playlist_id(linked_audio_track.id) in document.xml
        assert MltGraph.audio_playlist_id(music_track.id) in document.xml


def test_export_task_persists_real_quality_report_history_and_proof_frames(
    tmp_path: Path,
    max_project_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "qa-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    repository = ProjectRepository.create(max_project_path, "QA Project")
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    assets.adopt_main_profile_from_video(asset.id)
    asset = repository.assets.get_asset(asset.id)
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        output = repository.project_dir / DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY / "qa-export.mp4"
        published_temporary_paths: list[Path] = []
        published_proof_temporaries: list[Path] = []
        temporary_probes = []
        original_replace = Path.replace

        def observe_output_publish(
            source_path: Path,
            destination: str | Path,
        ) -> Path:
            if Path(destination).resolve() == output.resolve():
                assert source_path.is_file()
                assert source_path.stat().st_size > 0
                assert utf16_units(str(source_path)) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
                published_temporary_paths.append(source_path)
                temporary_probes.append(MediaProbe(paths).probe(source_path))
            if (
                Path(destination).name.startswith("proof-")
                and Path(destination).parent.parent.name == "export-qa"
            ):
                assert source_path.is_file()
                assert source_path.stat().st_size > 0
                assert utf16_units(str(source_path)) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
                published_proof_temporaries.append(source_path)
            return original_replace(source_path, destination)

        monkeypatch.setattr(Path, "replace", observe_output_publish)
        preset = ExportPreset(
            name="QA export",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
        )
        task = project.start_task(
            ExportSequenceCommand(
                sequence_id=sequence_id,
                output_path=str(output),
                format=ExportFormat.H264,
                preset=preset,
            ),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=90)
        assert completed.status == TaskStatus.COMPLETED, completed.error
        assert len(published_temporary_paths) == 1
        assert temporary_probes[0].metadata.has_video is True
        assert not published_temporary_paths[0].exists()
        assert output.parent == (repository.project_dir / DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY)
        assert output.is_file()
        assert utf16_units(str(output)) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        final_probe = MediaProbe(paths).probe(output)
        assert final_probe.metadata.has_video is True
        history = repository.records.list_export_history(sequence_id)
        assert len(history) == 1
        record = history[0]
        assert record.task_id == task.id
        assert record.output_path == str(output.resolve())
        assert record.quality.sha256 and record.quality.passed is True
        checks = {check.key: check for check in record.quality.checks}
        assert {
            "streams",
            "duration",
            "black",
            "freeze",
            "silence",
            "true_peak",
            "safe_area",
            "proof_frames",
        } <= set(checks)
        assert "encoder_recovery" not in checks
        assert checks["streams"].status == "passed"
        assert checks["duration"].status == "passed"
        assert len(record.quality.proof_frames) == 3
        assert len(published_proof_temporaries) == 3
        assert not any(path.exists() for path in published_proof_temporaries)
        assert all(Path(path).is_file() for path in record.quality.proof_frames)
        assert all(
            MediaProbe(paths).probe(Path(path)).kind == AssetKind.IMAGE
            for path in record.quality.proof_frames
        )
        report = export_quality_directory(repository.project_dir, record.id) / "report.json"
        assert utf16_units(str(report)) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        assert report.is_file() and record.id in report.read_text(encoding="utf-8")
        assert report.resolve() in {
            artifact.resolve(repository.project_dir) for artifact in completed.artifacts
        }
    finally:
        project.close()


def test_export_history_failure_withdraws_real_published_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "history-failure-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    repository = ProjectRepository.create(
        tmp_path / "History Failure",
        "History Failure",
    )
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    assets.adopt_main_profile_from_video(asset.id)
    sequence_id = repository.projects.get_project().main_sequence_id
    editor = TimelineEditor(repository, sequence_id)
    track = editor.add_track(TrackKind.VIDEO)
    editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=25,
    )
    output = repository.project_dir / "exports" / "unrecorded.mp4"
    preset = ExportPreset(
        name="History failure",
        format=ExportFormat.H264,
        container="mp4",
        encoder_policy={"mode": "software"},
        audio_codec="aac",
        pixel_format="yuv420p",
        preset="ultrafast",
    )

    def fail_history_save(_self, _record) -> None:
        raise RuntimeError("injected export history failure")

    monkeypatch.setattr(
        type(repository.records),
        "save_export_history",
        fail_history_save,
    )
    project = EditorProject(
        repository,
        settings=ServiceSettings(),
        paths=paths,
    )
    try:
        task = project.start_task(
            ExportSequenceCommand(
                sequence_id=sequence_id,
                output_path=str(output),
                format=ExportFormat.H264,
                preset=preset,
            ),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=90)

        assert completed.status == TaskStatus.FAILED
        assert "injected export history failure" in (completed.error or "")
        assert not completed.artifacts
        assert not output.exists()
        assert repository.records.list_export_history(sequence_id) == []
        quality_directory = export_quality_directory(
            repository.project_dir,
            completed.id,
        )
        assert not quality_directory.exists()
        archived_quality = list((quality_directory.parent / "MediaFlow Pro Failed Export QA").glob("qa-*"))
        assert len(archived_quality) == 1
        assert (archived_quality[0] / "report.json").is_file()
        archived_proof_frames = list(archived_quality[0].glob("proof-*"))
        assert len(archived_proof_frames) == 3
        assert all(path.is_file() for path in archived_proof_frames)
        archived = list((output.parent / "MediaFlow Pro Failed Exports").glob("*.mp4"))
        assert len(archived) == 1
        archived_probe = MediaProbe(paths).probe(
            archived[0],
            timeline_profile=editor.state.sequence.profile,
        )
        assert archived_probe.metadata.has_video is True
    finally:
        project.close()


def test_hardware_encoder_failure_recovers_through_real_export_task_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimeContext.discover().paths
    original_resolve = VideoEncoderPolicyResolver.resolve

    def resolve_for_failed_attempt(resolver, export_format, policy):
        if policy.mode == "prefer_hardware" and policy.vendor == "amd":
            return ResolvedVideoEncoder("h264_amf", "amf", True)
        return original_resolve(resolver, export_format, policy)

    monkeypatch.setattr(
        VideoEncoderPolicyResolver,
        "resolve",
        resolve_for_failed_attempt,
    )
    source = tmp_path / "hardware-recovery-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    repository = ProjectRepository.create(
        tmp_path / "Hardware Recovery Project",
        "Hardware Recovery Project",
    )
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    assets.adopt_main_profile_from_video(asset.id)
    asset = repository.assets.get_asset(asset.id)
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        output = tmp_path / "hardware-recovered.mp4"
        preset = ExportPreset(
            name="Forced hardware recovery",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "prefer_hardware", "vendor": "amd"},
            audio_codec="aac",
            pixel_format="yuv444p",
        )
        task = project.start_task(
            ExportSequenceCommand(
                sequence_id=sequence_id,
                output_path=str(output),
                format=ExportFormat.H264,
                preset=preset,
            ),
            [asset.id],
            sequence_id=sequence_id,
        )

        completed = project.wait_for_task(task.id, timeout=90)

        assert completed.status == TaskStatus.COMPLETED, completed.error
        assert output.is_file() and output.stat().st_size > 0
        assert isinstance(completed.outcome, ExportTaskOutcome)
        assert completed.outcome.hardware_fallback_used is True
        file_outcome = completed.outcome.files[0]
        assert file_outcome.output.resolve(repository.project_dir) == output.resolve()
        assert file_outcome.requested_video_codec == "h264_amf"
        assert file_outcome.actual_video_codec == "libx264"
        assert file_outcome.hardware_fallback_reason
        assert len(file_outcome.archived_failed_outputs) == 1
        archived_attempt = file_outcome.archived_failed_outputs[0].resolve(repository.project_dir)
        assert archived_attempt.is_file()
        assert archived_attempt.parent.name == "MediaFlow Pro Failed Exports"
        assert any(
            item.step == "export_hardware_encoder_fallback" and item.status == "success"
            for item in completed.execution_trace
        )
        record = repository.records.list_export_history(sequence_id)[0]
        recovery = next(check for check in record.quality.checks if check.key == "encoder_recovery")
        assert recovery.status == "warning"
        assert recovery.details["requested_video_codec"] == "h264_amf"
        assert recovery.details["actual_video_codec"] == "libx264"
        assert recovery.details["archived_failed_outputs"] == [str(archived_attempt)]
        stream = next(item for item in record.quality.checks if item.key == "streams")
        assert stream.status == "passed"
        assert stream.details["video_codec"] == "h264"
    finally:
        project.close()

    controllers = EditorControllers()
    try:
        controllers.workspace_project.openProject(QUrl.fromLocalFile(str(repository.project_dir)).toString())
        task_row = next(
            controllers.tasks.tasksModel.get(index)
            for index in range(controllers.tasks.tasksModel.rowCount())
            if controllers.tasks.tasksModel.get(index)["taskId"] == task.id
        )
        assert task_row["encoderFallbackUsed"] is True
        assert task_row["configurationLabel"] == "硬件编码失败，已从 h264_amf 切换为 libx264"
        history_row = controllers.export.exportHistory[0]
        assert history_row["encoderFallbackUsed"] is True
        assert history_row["requestedVideoCodec"] == "h264_amf"
        assert history_row["actualVideoCodec"] == "libx264"
    finally:
        controllers.shutdown()


def test_selected_highlight_candidates_batch_export_to_separate_real_videos(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "batch-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    repository = ProjectRepository.create(tmp_path / "Batch Project", "Batch Project")
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    assets.adopt_main_profile_from_video(asset.id)
    source_document = SubtitleDocument(
        project_id=repository.projects.get_project().id,
        asset_id=asset.id,
        language="en",
    )
    repository.subtitles.create_subtitle_document(
        source_document,
        [
            SubtitleSegment(
                document_id=source_document.id,
                start_frame=0,
                end_frame=20,
                text="Batch subtitle",
            )
        ],
    )
    highlights = HighlightService(repository)
    first = highlights.add_manual_candidate(
        asset.id,
        start_frame=0,
        end_frame=10,
        title="CON",
        document_id=source_document.id,
    )
    second = highlights.add_manual_candidate(
        asset.id,
        start_frame=10,
        end_frame=20,
        title="😀" * 200,
        document_id=source_document.id,
    )
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        output_dir = tmp_path / "batch-exports"
        configured_preset = ExportPreset(
            name="Configured batch preset",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
            burn_subtitle_track_id="configured-source-track",
            subtitle_style=SubtitleStyle(
                font_family="Arial",
                font_color="#00FF00",
                shadow_size=6,
            ),
        )
        task = project.start_task(
            ExportHighlightsCommand(
                sequence_id=repository.projects.get_project().main_sequence_id,
                candidate_ids=[first.id, second.id],
                output_dir=str(output_dir),
                preset=configured_preset,
                burn_subtitles=True,
            ),
            [asset.id],
        )
        completed = project.wait_for_task(task.id, timeout=90)
        outputs = [
            artifact.resolve(repository.project_dir)
            for artifact in completed.artifacts
            if Path(artifact.path).suffix == ".mp4"
        ]
        assert completed.status == TaskStatus.COMPLETED, completed.error
        assert len(outputs) == 2
        assert all(path.is_file() and path.stat().st_size > 0 for path in outputs)
        assert all(len(path.name.encode("utf-16-le")) // 2 <= 240 for path in outputs)
        assert outputs[0].name.startswith("01-_CON-")
        for output in outputs:
            probe = subprocess.run(
                [
                    str(paths.ffprobe),
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type",
                    "-of",
                    "csv=p=0",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert probe.returncode == 0, probe.stderr
            assert {"video", "audio"} <= set(probe.stdout.split())
        assert len(repository.sequences.list_sequences()) == 3
        assert all(item.sequence_id for item in repository.highlights.list_highlights(asset.id))
        short_sequences = repository.sequences.list_sequences()[1:]
        assert all(sequence.profile.fps_numerator == 25 for sequence in short_sequences)
        graphs = [
            artifact.resolve(repository.project_dir)
            for artifact in completed.artifacts
            if Path(artifact.path).suffix == ".mlt"
        ]
        assert len(graphs) == len(short_sequences)
        for graph in graphs:
            xml = graph.read_text(encoding="utf-8")
            assert '<property name="family">Arial</property>' in xml
            assert '<property name="fgcolour">0x00ff00ff</property>' in xml
            assert '<property name="shadow">' in xml
        for output in outputs:
            probe = MediaProbe(paths).probe(
                output,
                timeline_profile=short_sequences[0].profile,
            )
            assert probe.metadata.duration_frames == 10
    finally:
        project.close()


def test_highlight_batch_preflights_every_output_before_rendering(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "batch-conflict-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    repository = ProjectRepository.create(
        tmp_path / "Batch Conflict",
        "Batch Conflict",
    )
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    assets.adopt_main_profile_from_video(asset.id)
    highlights = HighlightService(repository)
    first = highlights.add_manual_candidate(
        asset.id,
        start_frame=0,
        end_frame=10,
        title="first",
    )
    second = highlights.add_manual_candidate(
        asset.id,
        start_frame=10,
        end_frame=20,
        title="second",
    )
    preset = ExportPreset(
        name="Atomic batch conflict",
        format=ExportFormat.H264,
        container="mp4",
        encoder_policy={"mode": "software"},
        audio_codec="aac",
        pixel_format="yuv420p",
        preset="ultrafast",
    )
    output_dir = tmp_path / "batch-conflict-exports"
    output_dir.mkdir(parents=True)
    first_output = safe_child_path(
        output_dir,
        first.title,
        prefix="01-",
        suffix=f"-{first.id[:8]}.mp4",
        fallback="clip",
        required_sibling_component_utf16_units=(OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS),
    )
    second_output = safe_child_path(
        output_dir,
        second.title,
        prefix="02-",
        suffix=f"-{second.id[:8]}.mp4",
        fallback="clip",
        required_sibling_component_utf16_units=(OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS),
    )
    second_output.write_bytes(b"existing-user-output")
    project = EditorProject(
        repository,
        settings=ServiceSettings(),
        paths=paths,
    )
    try:
        task = project.start_task(
            ExportHighlightsCommand(
                sequence_id=(repository.projects.get_project().main_sequence_id),
                candidate_ids=[first.id, second.id],
                output_dir=str(output_dir),
                preset=preset,
            ),
            [asset.id],
        )
        completed = project.wait_for_task(task.id, timeout=30)

        assert completed.status == TaskStatus.FAILED
        assert "already exists" in (completed.error or "")
        assert not first_output.exists()
        assert second_output.read_bytes() == b"existing-user-output"
        assert not completed.artifacts
        assert len(repository.sequences.list_sequences()) == 1
        assert all(
            candidate.sequence_id is None for candidate in repository.highlights.list_highlights(asset.id)
        )
        assert not any(path.name.startswith(".") for path in output_dir.iterdir())
    finally:
        project.close()


def test_desktop_highlight_export_ignores_saved_audio_only_preset(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "highlight-video-after-audio.mp4"
    generate_real_media(source, paths, width=320, height=180)
    repository = ProjectRepository.create(
        tmp_path / "Highlight Video Preset",
        "Highlight Video Preset",
    )
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    assets.adopt_main_profile_from_video(asset.id)
    sequence_id = repository.projects.get_project().main_sequence_id
    HighlightService(repository).add_manual_candidate(
        asset.id,
        start_frame=0,
        end_frame=10,
        title="must remain video",
    )
    repository.sequences.save_sequence_export_preset(
        sequence_id,
        ExportPreset(
            name="Previous audio export",
            format=ExportFormat.AUDIO,
            container="flac",
            encoder_policy=None,
            audio_codec="flac",
            pixel_format=None,
        ),
    )
    project_root = repository.project_dir
    repository.close()

    controllers = EditorControllers()
    try:
        controllers.workspace_project.openProject(QUrl.fromLocalFile(str(project_root)).toString())
        output_dir = tmp_path / "desktop-highlight-exports"
        controllers.highlights.exportSelectedHighlights(str(output_dir))
        current = controllers.session.state.binding.current
        assert current is not None
        task = current.list_tasks()[-1]
        completed = current.wait_for_task(task.id, timeout=90)

        assert completed.status == TaskStatus.COMPLETED, completed.error
        assert isinstance(
            completed.command,
            ExportHighlightsCommand,
        )
        assert completed.command.preset is not None
        assert completed.command.preset.format == ExportFormat.H264
        videos = [
            artifact.resolve(project_root)
            for artifact in completed.artifacts
            if Path(artifact.path).suffix == ".mp4"
        ]
        assert len(videos) == 1
        assert not list(output_dir.glob("*.flac"))
        probe = MediaProbe(paths).probe(videos[0])
        assert probe.metadata.has_video is True
    finally:
        controllers.shutdown()


def test_canonical_timeline_compiles_and_real_mlt_export_is_consumable(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    assert paths.melt is not None
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    root = tmp_path / "Project"

    with ProjectRepository.create(root, "Project") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        first_clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=10,
        )
        second_clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=10,
            source_in=10,
            duration=15,
        )
        compound = editor.create_compound_clip([first_clip.id, second_clip.id])
        master_bus = next(
            bus
            for bus in repository.audio.list_audio_buses(project.main_sequence_id)
            if bus.parent_bus_id is None
        )
        repository.audio.save_audio_bus(master_bus.model_copy(update={"gain_db": -1.0}))
        repository.audio.save_audio_effect(
            AudioEffect(
                bus_id=master_bus.id,
                kind=AudioEffectKind.LIMITER,
                position=0,
                parameters={"ceiling_db": -1.0},
            )
        )
        state = editor.state
        assert state.compounds == [compound]
        compiler = TimelineCompiler(repository, RuntimeContext.discover().paths)
        document = compiler.compile(state)
        assert str(source.resolve()) in document.xml
        assert "tractor0" in document.xml
        assert "avfilter.alimiter" in document.xml
        assert "audio_bus_" in document.xml

        preset = ExportPreset(
            name="H.264 Test",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
            quality_value=23,
            preset="veryfast",
            gop_frames=25,
        )
        export_progress = []
        result = MltExportService(compiler, paths).export(
            state,
            preset,
            root / "exports" / "real-mlt-export.mp4",
            progress=export_progress.append,
        )

        assert result.output_path.is_file()
        video = next(stream for stream in result.probe["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in result.probe["streams"] if stream["codec_type"] == "audio")
        assert video["codec_name"] == "h264"
        assert audio["codec_name"] == "aac"
        assert (video["width"], video["height"]) == (320, 180)
        rendering = [item for item in export_progress if item.message_code == "export_rendering"]
        assert rendering
        assert all(item.mode == "determinate" and item.unit == "frames" for item in rendering)
        assert rendering[-1].completed == rendering[-1].total

        hardware = {item["value"] for item in EncoderDiscoveryService(paths).video_options()}
        if "prefer_hardware:nvidia" in hardware:
            hardware_result = MltExportService(compiler, paths).export(
                state,
                ExportPreset.model_validate(
                    {
                        **preset.model_dump(mode="python"),
                        "name": "H.264 NVENC Test",
                        "encoder_policy": {
                            "mode": "prefer_hardware",
                            "vendor": "nvidia",
                        },
                        "preset": "p4",
                    },
                ),
                root / "exports" / "real-nvenc-export.mp4",
            )
            hardware_video = next(
                stream for stream in hardware_result.probe["streams"] if stream["codec_type"] == "video"
            )
            assert hardware_video["codec_name"] == "h264"

        detached_video, detached_audio = editor.detach_clip_audio(first_clip.id)
        assert detached_video.media_kind.value == "video_only"
        assert detached_audio.media_kind.value == "audio_only"
        detached_state = editor.state
        detached_result = MltExportService(compiler, paths).export(
            detached_state,
            preset.model_copy(update={"name": "Detached audio test"}),
            root / "exports" / "detached-audio-export.mp4",
        )
        assert {stream["codec_type"] for stream in detached_result.probe["streams"]} >= {"video", "audio"}
        detached_probe = MediaProbe(paths).probe(
            detached_result.output_path,
            timeline_profile=detached_state.sequence.profile,
        )
        assert detached_probe.metadata.duration_frames == 25
        assert detached_probe.metadata.has_audio is True


def _generate_color_media(path: Path, paths: RuntimePaths, color: str) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r=25:d=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _prepare_external_subtitle_export(
    repository: ProjectRepository,
    paths: RuntimePaths,
    source: Path,
    *,
    track_name: str = "English",
    text: str = "TRANSACTIONAL SUBTITLE",
) -> tuple[TimelineState, ExportPreset]:
    asset_service = AssetService(repository, MediaProbe(paths))
    asset = asset_service.import_external(source)
    asset = asset_service.adopt_main_profile_from_video(asset.id)
    project = repository.projects.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    video_track = editor.add_track(TrackKind.VIDEO)
    subtitle_track = editor.add_track(
        TrackKind.SUBTITLE,
        track_name,
    )
    editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=25,
    )
    document = SubtitleDocument(
        project_id=project.id,
        asset_id=asset.id,
        language="en",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=5,
        end_frame=20,
        text=text,
    )
    repository.subtitles.create_subtitle_document(
        document,
        [segment],
    )
    repository.subtitles.place_subtitle_document(
        document.id,
        subtitle_track.id,
    )
    preset = ExportPreset(
        name="Transactional subtitle export",
        format=ExportFormat.H264,
        container="mp4",
        encoder_policy={"mode": "software"},
        audio_codec=None,
        pixel_format="yuv420p",
        quality_value=18,
        preset="veryfast",
        gop_frames=25,
    )
    return editor.state, preset


def test_external_subtitle_names_are_portable_bounded_and_collision_stable(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "sidecar-source.mp4"
    _generate_color_media(source, paths, "black")

    with ProjectRepository.create(
        tmp_path / "Portable Sidecar Project",
        "Portable Sidecar Project",
    ) as repository:
        state, _preset = _prepare_external_subtitle_export(
            repository,
            paths,
            source,
            track_name="CON",
        )
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        duplicate_track = editor.add_track(TrackKind.SUBTITLE, "CON")
        long_track = editor.add_track(
            TrackKind.SUBTITLE,
            "字幕🎬" * 100,
        )
        document = repository.subtitles.list_subtitle_documents()[0]
        repository.subtitles.place_subtitle_document(
            document.id,
            duplicate_track.id,
        )
        repository.subtitles.place_subtitle_document(
            document.id,
            long_track.id,
        )
        state = editor.reload()
        output = tmp_path / (("超长导出🎞️" * 60) + ".mp4")
        service = MltExportService(TimelineCompiler(repository, RuntimeContext.discover().paths), paths)

        first = service.sidecars.plan(
            state,
            output,
            start_frame=0,
            end_frame=25,
        )
        second = service.sidecars.plan(
            state,
            output,
            start_frame=0,
            end_frame=25,
        )

        assert len(first) == 3
        assert tuple(item.destination for item in first) == tuple(item.destination for item in second)
        names = [item.destination.name for item in first]
        assert len({name.casefold() for name in names}) == 3
        assert all(len(name.encode("utf-16-le")) // 2 <= 240 for name in names)
        assert all(name.endswith(".srt") for name in names)
        assert names[0].endswith("._CON.srt")
        assert re.search(r"\._CON-[0-9a-f]{12}\.srt$", names[1])


def _generate_watermark(path: Path, paths: RuntimePaths, color: str = "red") -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x32:d=0.04",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _generate_edge_black_media(path: Path, paths: RuntimePaths) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=25:d=0.08",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=25:d=0.84",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=25:d=0.08",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "3:a",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-g",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _generate_temporal_color_media(path: Path, paths: RuntimePaths) -> None:
    command = [str(paths.ffmpeg), "-y", "-hide_banner", "-v", "error"]
    for color in ("red", "green", "blue"):
        command.extend(["-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=25:d=1"])
    command.extend(
        [
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-g",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _generate_sine_audio(
    path: Path,
    paths: RuntimePaths,
    *,
    frequency: int,
    duration: int,
) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-af",
            "volume=0.35",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _band_mean_volume(
    path: Path,
    paths: RuntimePaths,
    *,
    start: float,
    duration: float,
    frequency: int,
) -> float:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-hide_banner",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(path),
            "-af",
            f"bandpass=f={frequency}:w=30,volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", result.stderr)
    assert match, result.stderr
    return float(match.group(1))


def _frame_rgb_means(path: Path, paths: RuntimePaths, frames: list[int]) -> list[tuple[float, float, float]]:
    expression = "+".join(f"eq(n\\,{frame})" for frame in frames)
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select='{expression}'",
            "-vsync",
            "0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    frame_size = 160 * 90 * 3
    assert len(result.stdout) == frame_size * len(frames)
    means = []
    for index in range(len(frames)):
        payload = result.stdout[index * frame_size : (index + 1) * frame_size]
        means.append(tuple(sum(payload[channel::3]) / (frame_size // 3) for channel in range(3)))
    return means


def test_mlt_transition_uses_two_real_sources_and_preserves_timeline_duration(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    red_source = tmp_path / "red.mp4"
    blue_source = tmp_path / "blue.mp4"
    _generate_color_media(red_source, paths, "red")
    _generate_color_media(blue_source, paths, "blue")

    with ProjectRepository.create(tmp_path / "Transition Project", "Transition Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        red = assets.import_external(red_source)
        blue = assets.import_external(blue_source)
        red = assets.adopt_main_profile_from_video(red.id)
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        left = editor.add_clip(
            track_id=video_track.id,
            asset_id=red.id,
            timeline_start=0,
            source_in=0,
            duration=75,
        )
        right = editor.add_clip(
            track_id=video_track.id,
            asset_id=blue.id,
            timeline_start=75,
            source_in=0,
            duration=75,
        )
        editor.create_transition(left.id, right.id, TransitionKind.DISSOLVE, 10)
        state = editor.state
        compiler = TimelineCompiler(repository, RuntimeContext.discover().paths)
        document = compiler.compile(state)
        assert 'mlt_service">luma<' in document.xml
        assert 'mlt_service">mix<' in document.xml
        assert "transition_hold_" in document.xml
        assert document.duration_frames == 150

        preset = ExportPreset(
            name="Transition Test",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
        )
        result = MltExportService(compiler, paths).export(
            state,
            preset,
            repository.project_dir / "exports" / "transition.mp4",
        )
        stream = next(item for item in result.probe["streams"] if item["codec_type"] == "video")
        assert int(stream["nb_frames"]) == 150
        before, middle, after = _frame_rgb_means(result.output_path, paths, [10, 75, 140])
        assert before[0] > 180 and before[2] < 80
        assert middle[0] > 55 and middle[2] > 55
        assert after[2] > 180 and after[0] < 80


def test_real_mlt_timewarp_reads_correct_forward_and_reverse_source_frames(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "temporal.mp4"
    _generate_temporal_color_media(source, paths)

    with ProjectRepository.create(tmp_path / "Timewarp Project", "Timewarp Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        asset = assets.import_external(source)
        asset = assets.adopt_main_profile_from_video(asset.id)
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        fast = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=37,
            speed_numerator=2,
        )
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=37,
            source_in=74,
            duration=75,
            speed_numerator=-1,
        )
        assert fast.duration == 37
        preset = ExportPreset(
            name="Timewarp Test",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
        )
        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths), paths
        ).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "timewarp.mp4",
        )
        colors = _frame_rgb_means(result.output_path, paths, [3, 18, 33, 42, 72, 102])
        fast_red, fast_green, fast_blue, reverse_blue, reverse_green, reverse_red = colors
        assert fast_red[0] > 150 and fast_red[2] < 80
        assert fast_green[1] > fast_green[0] and fast_green[1] > fast_green[2]
        assert fast_blue[2] > 150 and fast_blue[0] < 80
        assert reverse_blue[2] > 150 and reverse_blue[0] < 80
        assert reverse_green[1] > reverse_green[0] and reverse_green[1] > reverse_green[2]
        assert reverse_red[0] > 150 and reverse_red[2] < 80


def test_real_mlt_transition_accepts_speed_adjusted_adjacent_clips(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    red_source = tmp_path / "red-fast.mp4"
    blue_source = tmp_path / "blue-fast.mp4"
    _generate_color_media(red_source, paths, "red")
    _generate_color_media(blue_source, paths, "blue")

    with ProjectRepository.create(tmp_path / "Fast Transition", "Fast Transition") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        red = assets.import_external(red_source)
        blue = assets.import_external(blue_source)
        red = assets.adopt_main_profile_from_video(red.id)
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        left = editor.add_clip(
            track_id=video_track.id,
            asset_id=red.id,
            timeline_start=0,
            source_in=0,
            duration=37,
            speed_numerator=2,
        )
        right = editor.add_clip(
            track_id=video_track.id,
            asset_id=blue.id,
            timeline_start=37,
            source_in=0,
            duration=37,
            speed_numerator=2,
        )
        editor.create_transition(left.id, right.id, TransitionKind.DISSOLVE, 10)
        preset = ExportPreset(
            name="Fast Transition Test",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
        )
        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths), paths
        ).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "fast-transition.mp4",
        )
        stream = next(item for item in result.probe["streams"] if item["codec_type"] == "video")
        assert int(stream["nb_frames"]) == 74
        before, middle, after = _frame_rgb_means(result.output_path, paths, [5, 37, 68])
        assert before[0] > 150 and before[2] < 80
        assert middle[0] > 45 and middle[2] > 45
        assert after[2] > 150 and after[0] < 80


def test_subtitle_placement_is_burned_and_exported_as_external_srt(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "black.mp4"
    _generate_color_media(source, paths, "black")

    with ProjectRepository.create(tmp_path / "Subtitle Project", "Subtitle Project") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="en",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=5,
            end_frame=20,
            text="HELLO MEDIAFLOW",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        repository.subtitles.place_subtitle_document(document.id, subtitle_track.id)
        state = editor.state
        preset = ExportPreset(
            name="Subtitle Test",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
            burn_subtitle_track_id=subtitle_track.id,
        )
        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths), paths
        ).export(
            state,
            preset,
            repository.project_dir / "exports" / "subtitle.mp4",
        )
        assert len(result.subtitle_files) == 1
        assert "HELLO MEDIAFLOW" in result.subtitle_files[0].read_text(encoding="utf-8-sig")
        assert result.output_path.is_file()
        assert not [
            item
            for item in result.output_path.parent.iterdir()
            if item.is_file() and item.name.startswith(".mf-")
        ]
        without_text, with_text = _frame_rgb_means(result.output_path, paths, [2, 10])
        assert sum(with_text) > sum(without_text) + 2.0


def test_external_subtitle_conflict_prevents_render_before_video_is_published(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "conflict-source.mp4"
    _generate_color_media(source, paths, "red")

    with ProjectRepository.create(
        tmp_path / "Subtitle Conflict Project",
        "Subtitle Conflict Project",
    ) as repository:
        state, preset = _prepare_external_subtitle_export(
            repository,
            paths,
            source,
        )
        output = repository.project_dir / "exports" / "conflict.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        sidecar = output.with_name("conflict.English.srt")
        original_subtitle = b"\xef\xbb\xbfexisting subtitle"
        sidecar.write_bytes(original_subtitle)

        with pytest.raises(FileExistsError) as conflict:
            MltExportService(
                TimelineCompiler(repository, RuntimeContext.discover().paths),
                paths,
            ).export(
                state,
                preset,
                output,
                overwrite=False,
            )

        assert str(sidecar) in str(conflict.value)
        assert sidecar.read_bytes() == original_subtitle
        assert not output.exists()
        assert not [
            item for item in output.parent.iterdir() if item.is_file() and item.name.startswith(".mf-")
        ]


def test_external_subtitle_commit_failure_restores_complete_previous_output_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "rollback-source.mp4"
    _generate_color_media(source, paths, "red")

    with ProjectRepository.create(
        tmp_path / "Subtitle Rollback Project",
        "Subtitle Rollback Project",
    ) as repository:
        state, preset = _prepare_external_subtitle_export(
            repository,
            paths,
            source,
        )
        output = repository.project_dir / "exports" / "rollback.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        _generate_color_media(output, paths, "blue")
        sidecar = output.with_name("rollback.English.srt")
        sidecar.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nOLD SUBTITLE\n",
            encoding="utf-8-sig",
        )
        previous_video = output.read_bytes()
        previous_subtitle = sidecar.read_bytes()
        original_replace = Path.replace
        injected = False

        def fail_second_publish(
            source_path: Path,
            destination: str | Path,
        ) -> Path:
            nonlocal injected
            target = Path(destination).resolve()
            if not injected and target == sidecar.resolve() and source_path.name.startswith(".mf-subtitle-"):
                injected = True
                raise OSError("injected sidecar publish failure")
            return original_replace(source_path, destination)

        monkeypatch.setattr(Path, "replace", fail_second_publish)
        with pytest.raises(
            OSError,
            match="sidecar publish failure",
        ):
            MltExportService(
                TimelineCompiler(repository, RuntimeContext.discover().paths),
                paths,
            ).export(
                state,
                preset,
                output,
                overwrite=True,
            )

        assert injected is True
        assert output.read_bytes() == previous_video
        assert sidecar.read_bytes() == previous_subtitle
        archived = list((output.parent / "MediaFlow Pro Failed Exports").iterdir())
        archived_video = next(item for item in archived if item.suffix == ".mp4")
        archived_subtitle = next(item for item in archived if item.suffix == ".srt")
        assert archived_video.stat().st_size > 0
        assert "TRANSACTIONAL SUBTITLE" in archived_subtitle.read_text(encoding="utf-8-sig")
        archived_probe = MediaProbe(paths).probe(
            archived_video,
            timeline_profile=state.sequence.profile,
        )
        assert archived_probe.metadata.has_video is True
        assert archived_probe.metadata.duration_frames == 25
        assert not [
            item for item in output.parent.iterdir() if item.is_file() and item.name.startswith(".mf-")
        ]


def test_different_video_containers_contend_for_shared_subtitle_sidecar_across_processes(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "shared-sidecar-source.mp4"
    _generate_color_media(source, paths, "green")

    with ProjectRepository.create(
        tmp_path / "Shared Sidecar Project",
        "Shared Sidecar Project",
    ) as repository:
        state, preset = _prepare_external_subtitle_export(
            repository,
            paths,
            source,
        )
        output = repository.project_dir / "exports" / "shared.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        competing_video = output.with_suffix(".mkv")
        sidecar = output.with_name("shared.English.srt")
        script = """
import sys
from pathlib import Path
from mediaflow.infrastructure.output_reservation import reserve_outputs

video = Path(sys.argv[1])
sidecar = Path(sys.argv[2])
runtime_dir = Path(sys.argv[3])
with reserve_outputs((video, sidecar), runtime_dir=runtime_dir):
    print("ready", flush=True)
    sys.stdin.readline()
"""
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(competing_video),
                str(sidecar),
                str(paths.runtime_dir),
            ],
            cwd=Path(__file__).resolve().parents[3],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        try:
            with pytest.raises(
                RuntimeError,
                match="already writing",
            ):
                MltExportService(
                    TimelineCompiler(repository, RuntimeContext.discover().paths),
                    paths,
                ).export(
                    state,
                    preset,
                    output,
                )
            assert not output.exists()
            assert not sidecar.exists()
        finally:
            assert process.stdin is not None
            process.stdin.write("release\n")
            process.stdin.flush()
            process.wait(timeout=10)
        assert process.returncode == 0, process.stderr.read() if process.stderr is not None else ""

        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths),
            paths,
        ).export(
            state,
            preset,
            output,
        )

        assert result.output_path.is_file()
        assert result.subtitle_files == (sidecar,)
        assert "TRANSACTIONAL SUBTITLE" in sidecar.read_text(encoding="utf-8-sig")


def test_export_style_watermark_and_trim_reach_the_rendered_video(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "black.mp4"
    watermark_source = tmp_path / "watermark.png"
    _generate_color_media(source, paths, "black")
    _generate_watermark(watermark_source, paths)

    with ProjectRepository.create(tmp_path / "Styled Export", "Styled Export") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        watermark_asset = asset_service.import_external(watermark_source)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=75,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="zh",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=25,
            end_frame=65,
            text="样式已生效",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        repository.subtitles.place_subtitle_document(document.id, subtitle_track.id)
        style = SubtitleStyle(
            font_family="Arial",
            font_size=30,
            font_color="#00FF00",
            bold=False,
            italic=True,
            outline_size=3,
            shadow_size=6,
            outline_color="#0000FF",
            background_enabled=True,
            background_color="#FF0000",
            background_opacity=0.4,
            background_padding=9,
            position_x=0.4,
            position_y=0.7,
            alignment="left",
            multiline_alignment="top",
        )
        watermark = WatermarkOverlay(
            enabled=True,
            asset_id=watermark_asset.id,
            position="TL",
            position_x=0.72,
            position_y=0.63,
            width_ratio=0.2,
            opacity=0.8,
        )
        preset = ExportPreset(
            name="Styled Trim Test",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
            burn_subtitle_track_id=subtitle_track.id,
            subtitle_style=style,
            watermark=watermark,
        )
        editor.set_sequence_in_out(20, 70)
        compiler = TimelineCompiler(repository, RuntimeContext.discover().paths)
        graph = compiler.compile(
            editor.state,
            subtitle_track_id=subtitle_track.id,
            subtitle_style=style,
            watermark=watermark,
        )
        assert str(watermark_source.resolve()) in graph.xml
        assert '<property name="family">Arial</property>' in graph.xml
        assert '<property name="fgcolour">0x00ff00ff</property>' in graph.xml
        assert '<property name="olcolour">0x0000ffff</property>' in graph.xml
        assert '<property name="halign">left</property>' in graph.xml
        assert '<property name="valign">top</property>' in graph.xml
        assert '<property name="shadow">1</property>' in graph.xml
        assert '<property name="pad">2</property>' in graph.xml
        assert "62%/54.1111%:20%x17.7778%:80%" in graph.xml

        result = MltExportService(compiler, paths).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "styled-trim.mp4",
        )
        assert (result.start_frame, result.end_frame) == (20, 70)
        video = next(item for item in result.probe["streams"] if item["codec_type"] == "video")
        assert int(video["nb_frames"]) == 50
        subtitle_text = result.subtitle_files[0].read_text(encoding="utf-8-sig")
        assert "00:00:00,200 --> 00:00:01,800" in subtitle_text
        watermark_frame, subtitle_frame = _frame_rgb_means(result.output_path, paths, [2, 15])
        assert watermark_frame[0] > watermark_frame[1] + 2.0
        assert sum(subtitle_frame) > sum(watermark_frame) + 1.0


def test_smart_sequence_bounds_change_real_export_and_preserve_source_clips(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "leading-black.mp4"
    _generate_edge_black_media(source, paths)

    with ProjectRepository.create(max_project_path, "Auto Trim") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        assert asset.metadata.has_audio is True
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="zh",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=10,
            end_frame=15,
            text="对白区间",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        repository.subtitles.place_subtitle_document(document.id, subtitle_track.id)
        base = ExportPreset(
            name="Auto Trim",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
        )
        service = MltExportService(TimelineCompiler(repository, RuntimeContext.discover().paths), paths)
        analyzer = SequenceBoundaryAnalysisService(
            TimelineCompiler(repository, RuntimeContext.discover().paths), paths
        )
        snapshot_hash = analyzer.snapshot_hash(editor.state)
        analysis, artifact = analyzer.analyze(
            editor.state,
            expected_snapshot_hash=snapshot_hash,
        )
        assert artifact.is_file()
        assert utf16_units(str(artifact)) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        boundary_cache_files = [
            path for path in (repository.project_dir / "cache" / "b").rglob("*") if path.is_file()
        ]
        assert boundary_cache_files
        assert all(
            utf16_units(str(path)) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT for path in boundary_cache_files
        )
        assert not list(repository.project_dir.rglob(".mf-*"))
        assert analysis.black_in_frame == 2
        assert analysis.black_out_frame == 23
        assert analysis.speech_in_frame == 7
        assert analysis.speech_out_frame == 18
        assert analysis.suggested == SequenceInOut(in_frame=7, out_frame=18)

        original_clip = editor.state.clips[0]
        editor.set_sequence_in_out(
            analysis.suggested.in_frame,
            analysis.suggested.out_frame,
        )
        assert editor.state.clips[0] == original_clip
        trimmed = service.export(
            editor.state,
            base,
            repository.project_dir / "exports" / "smart-bounds.mp4",
        )
        assert (trimmed.start_frame, trimmed.end_frame) == (7, 18)
        video = next(item for item in trimmed.probe["streams"] if item["codec_type"] == "video")
        assert int(video["nb_frames"]) == 11
        first_frame, last_frame = _frame_rgb_means(trimmed.output_path, paths, [0, 10])
        assert first_frame[0] > 150 and first_frame[1] < 80
        assert last_frame[0] > 150 and last_frame[1] < 80


def test_real_hevc_hdr10_export_is_ten_bit_and_carries_mastering_metadata(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "sdr-source.mp4"
    _generate_color_media(source, paths, "white")
    profile = ProjectProfile(
        width=160,
        height=90,
        fps_numerator=25,
        fps_denominator=1,
        color_mode=ColorMode.HDR10_BT2020_PQ,
        bit_depth=10,
    )
    with ProjectRepository.create(tmp_path / "HDR Project", "HDR Project", profile) as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        first = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        second = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=25,
            source_in=0,
            duration=25,
        )
        editor.create_transition(first.id, second.id, TransitionKind.DISSOLVE, duration=8)
        preset = ExportPreset(
            name="HDR10 Test",
            format=ExportFormat.HEVC,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec=None,
            pixel_format="yuv420p10le",
            quality_value=28,
            preset="ultrafast",
            gop_frames=25,
        )
        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths), paths
        ).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "hdr10.mp4",
        )
        video = next(item for item in result.probe["streams"] if item["codec_type"] == "video")
        assert video["codec_name"] == "hevc"
        assert "10" in video["pix_fmt"]
        assert video["color_primaries"] == "bt2020"
        assert video["color_transfer"] == "smpte2084"
        side_data = list(video.get("side_data_list") or [])
        side_data.extend(result.probe["frames"][0].get("side_data_list") or [])
        side_types = {item.get("side_data_type") for item in side_data}
        assert "Mastering display metadata" in side_types
        assert "Content light level metadata" in side_types


def test_real_av1_prores_and_audio_exports_are_consumable(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    with ProjectRepository.create(tmp_path / "Formats Project", "Formats Project") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        service = MltExportService(TimelineCompiler(repository, RuntimeContext.discover().paths), paths)

        av1 = service.export(
            editor.state,
            ExportPreset(
                name="AV1 Test",
                format=ExportFormat.AV1,
                container="mkv",
                encoder_policy={"mode": "software"},
                audio_codec="libopus",
                pixel_format="yuv420p",
                quality_value=35,
                preset="12",
                gop_frames=25,
            ),
            repository.project_dir / "exports" / "video-av1.mkv",
        )
        assert (
            next(item for item in av1.probe["streams"] if item["codec_type"] == "video")["codec_name"]
            == "av1"
        )

        prores = service.export(
            editor.state,
            ExportPreset(
                name="ProRes Test",
                format=ExportFormat.PRORES,
                container="mov",
                encoder_policy={"mode": "software"},
                audio_codec="pcm_s16le",
                pixel_format="yuv422p10le",
                quality_value=0,
                preset="medium",
                gop_frames=25,
                advanced={"profile": 3},
            ),
            repository.project_dir / "exports" / "video-prores.mov",
        )
        assert (
            next(item for item in prores.probe["streams"] if item["codec_type"] == "video")["codec_name"]
            == "prores"
        )

        audio = service.export(
            editor.state,
            ExportPreset(
                name="FLAC Test",
                format=ExportFormat.AUDIO,
                container="flac",
                encoder_policy=None,
                audio_codec="flac",
                pixel_format=None,
                quality_value=0,
                preset="medium",
                gop_frames=25,
            ),
            repository.project_dir / "exports" / "audio.flac",
        )
        assert not any(item["codec_type"] == "video" for item in audio.probe["streams"])
        assert (
            next(item for item in audio.probe["streams"] if item["codec_type"] == "audio")["codec_name"]
            == "flac"
        )

        for name, container, codec, suffix, expected_codec in (
            ("AAC Test", "ipod", "aac", "m4a", "aac"),
            ("Opus Test", "ogg", "libopus", "ogg", "opus"),
            ("PCM Test", "wav", "pcm_s24le", "wav", "pcm_s24le"),
        ):
            result = service.export(
                editor.state,
                ExportPreset(
                    name=name,
                    format=ExportFormat.AUDIO,
                    container=container,
                    encoder_policy=None,
                    audio_codec=codec,
                    pixel_format=None,
                    quality_value=0,
                    preset="medium",
                    gop_frames=25,
                ),
                repository.project_dir / "exports" / f"audio-{codec}.{suffix}",
            )
            assert not any(item["codec_type"] == "video" for item in result.probe["streams"])
            assert (
                next(item for item in result.probe["streams"] if item["codec_type"] == "audio")["codec_name"]
                == expected_codec
            )

        surround = service.export(
            editor.state,
            ExportPreset(
                name="5.1 PCM Test",
                format=ExportFormat.AUDIO,
                container="wav",
                encoder_policy=None,
                audio_codec="pcm_s24le",
                pixel_format=None,
                quality_value=0,
                preset="medium",
                gop_frames=25,
                advanced={"audio_sample_rate": 48_000, "audio_channels": 6},
            ),
            repository.project_dir / "exports" / "audio-5.1.wav",
        )
        surround_audio = next(item for item in surround.probe["streams"] if item["codec_type"] == "audio")
        assert surround_audio["channels"] == 6
        assert surround_audio["sample_rate"] == "48000"


def test_fixed_audio_effect_catalog_renders_real_audio(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "dialogue.wav"
    _generate_sine_audio(source, paths, frequency=700, duration=2)
    with ProjectRepository.create(tmp_path / "Effect Catalog", "Effect Catalog") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=60,
        )
        master = next(
            item
            for item in repository.audio.list_audio_buses(project.main_sequence_id)
            if item.parent_bus_id is None
        )
        kinds = [
            AudioEffectKind.PARAMETRIC_EQ,
            AudioEffectKind.HIGH_PASS,
            AudioEffectKind.LOW_PASS,
            AudioEffectKind.COMPRESSOR,
            AudioEffectKind.LIMITER,
            AudioEffectKind.NOISE_GATE,
            AudioEffectKind.RNNOISE,
            AudioEffectKind.CHANNEL_MAP,
            AudioEffectKind.LOUDNESS_NORMALIZE,
        ]
        for position, kind in enumerate(kinds):
            repository.audio.save_audio_effect(AudioEffect(bus_id=master.id, kind=kind, position=position))

        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths), paths
        ).export(
            editor.state,
            ExportPreset(
                name="Effect Catalog Test",
                format=ExportFormat.AUDIO,
                container="flac",
                encoder_policy=None,
                audio_codec="flac",
                pixel_format=None,
                quality_value=0,
                preset="medium",
                gop_frames=30,
                advanced={"audio_sample_rate": 48_000, "audio_channels": 2},
            ),
            repository.project_dir / "exports" / "effect-catalog.flac",
        )
        audio = next(item for item in result.probe["streams"] if item["codec_type"] == "audio")
        assert audio["codec_name"] == "flac"
        assert audio["sample_rate"] == "48000"
        assert audio["channels"] == 2
        assert result.output_path.stat().st_size > 1000


def test_dialogue_bus_really_ducks_music_in_exported_audio(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    music_source = tmp_path / "music.wav"
    dialogue_source = tmp_path / "dialogue.wav"
    _generate_sine_audio(music_source, paths, frequency=440, duration=3)
    _generate_sine_audio(dialogue_source, paths, frequency=1000, duration=1)
    with ProjectRepository.create(tmp_path / "Ducking Project", "Ducking Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        music_asset = assets.import_external(music_source)
        dialogue_asset = assets.import_external(dialogue_source)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        buses = repository.audio.list_audio_buses(project.main_sequence_id)
        dialogue_bus = next(item for item in buses if item.name == "对白")
        music_bus = next(item for item in buses if item.name == "音乐")
        dialogue_track = editor.add_track(
            TrackKind.AUDIO,
            "Dialogue",
            audio_bus_id=dialogue_bus.id,
        )
        music_track = editor.add_track(
            TrackKind.AUDIO,
            "Music",
            audio_bus_id=music_bus.id,
        )
        editor.add_clip(
            track_id=music_track.id,
            asset_id=music_asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        editor.add_clip(
            track_id=dialogue_track.id,
            asset_id=dialogue_asset.id,
            timeline_start=30,
            source_in=0,
            duration=30,
        )
        repository.audio.save_audio_effect(
            AudioEffect(
                bus_id=music_bus.id,
                kind=AudioEffectKind.DUCKING,
                position=0,
                parameters={
                    "driver_bus_id": dialogue_bus.id,
                    "reduction_db": -20.0,
                    "attack_ms": 0.0,
                    "release_ms": 0.0,
                },
            )
        )
        output = (
            MltExportService(TimelineCompiler(repository, RuntimeContext.discover().paths), paths)
            .export(
                editor.state,
                ExportPreset(
                    name="Ducking Test",
                    format=ExportFormat.AUDIO,
                    container="flac",
                    encoder_policy=None,
                    audio_codec="flac",
                    pixel_format=None,
                    quality_value=0,
                    preset="medium",
                    gop_frames=60,
                ),
                repository.project_dir / "exports" / "ducking.flac",
            )
            .output_path
        )
        music_before = _band_mean_volume(output, paths, start=0.2, duration=0.5, frequency=440)
        music_during = _band_mean_volume(output, paths, start=1.2, duration=0.5, frequency=440)
        assert music_during < music_before - 12.0
