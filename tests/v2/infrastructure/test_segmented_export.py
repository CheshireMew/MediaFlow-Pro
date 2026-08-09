from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import ExportFormat, TaskStatus, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.task_commands import BuildSequenceCommand, SequenceBuildUnit
from mediaflow.domain.tasks import SequenceBuildTaskOutcome
from mediaflow.domain.timeline import ClipAddRequest, ClipAudio, ClipTransform
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.segmented_export_service import SegmentedExportService
from tests.v2.editor_service_api import EditorServiceApi

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "media-build-cases"
    / "segmented-video"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _generate_clip(
    path: Path,
    paths: RuntimePaths,
    source: dict[str, object],
    *,
    fps: int,
) -> None:
    duration = int(source["duration_frames"]) / fps
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={source['color']}:s=160x90:r={fps}:d={duration}",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency="
            f"{source['tone_hz']}:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _generate_avatar_clip(
    path: Path,
    paths: RuntimePaths,
    source: dict[str, object],
    *,
    fps: int,
) -> None:
    duration = int(source["duration_frames"]) / fps
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={source['color']}:s=48x48:r={fps}:d={duration}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_segmented_export_reuses_unchanged_units_and_continuous_audio(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    assert paths.melt is not None
    origin = json.loads((FIXTURE / "fixture-origin.json").read_text(encoding="utf-8"))
    assert origin["producer"] == (
        "visual-multimedia/assets/media-build-cases/segmented-video"
    )
    for relative, expected in origin["files"].items():
        assert _sha256(FIXTURE / relative) == expected
    plan = json.loads((FIXTURE / "media-build-plan.json").read_text(encoding="utf-8"))
    contract = json.loads((FIXTURE / "source-contract.json").read_text(encoding="utf-8"))
    assert plan["protocol"] == "visual-multimedia-media-build-plan"
    assert plan["source_contract_sha256"] == _sha256(FIXTURE / "source-contract.json")
    source_specs = [
        json.loads((FIXTURE / scene["source"]).read_text(encoding="utf-8"))
        for scene in contract["scenes"]
    ]
    avatar_specs = [
        json.loads(
            (FIXTURE / scene["avatar_overlay"]).read_text(encoding="utf-8")
        )
        for scene in contract["scenes"]
    ]
    fps = int(plan["output"]["fps"])
    sources = [tmp_path / f"{source['id']}.mp4" for source in source_specs]
    for source_path, source_spec in zip(sources, source_specs, strict=True):
        _generate_clip(source_path, paths, source_spec, fps=fps)
    avatar_sources = [
        tmp_path / f"{source['id']}.mp4" for source in avatar_specs
    ]
    for source_path, source_spec in zip(
        avatar_sources,
        avatar_specs,
        strict=True,
    ):
        _generate_avatar_clip(source_path, paths, source_spec, fps=fps)

    project_root = tmp_path / "Project"
    with ProjectRepository.create(project_root, "Segmented build") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        imported = [assets.import_external(source) for source in sources]
        imported_avatars = [
            assets.import_external(source) for source in avatar_sources
        ]
        imported[0] = assets.adopt_main_profile_from_video(imported[0].id)
        project = repository.catalog.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clips = [
            editor.add_clip(
                track_id=track.id,
                asset_id=asset.id,
                timeline_start=index * 25,
                source_in=0,
                duration=25,
            )
            for index, asset in enumerate(imported)
        ]
        avatar_track = editor.add_track(TrackKind.VIDEO, "Avatar overlay")
        avatar_clips = editor.add_clips(
            [
                ClipAddRequest(
                    track_id=avatar_track.id,
                    asset_id=asset.id,
                    timeline_start=index * 25,
                    source_in=0,
                    duration=25,
                )
                for index, asset in enumerate(imported_avatars)
            ]
        )
        units = [
            SequenceBuildUnit(
                id=unit["id"],
                start_frame=unit["timeline_start_frame"],
                end_frame=(
                    unit["timeline_start_frame"] + unit["duration_frames"]
                ),
            )
            for unit in plan["units"]
        ]
        preset = ExportPreset(
            name="Segmented H.264",
            format=ExportFormat.H264,
            container="mp4",
            encoder_policy={"mode": "software"},
            audio_codec="aac",
            pixel_format="yuv420p",
            quality_value=23,
            preset="veryfast",
            gop_frames=25,
        )
        service = SegmentedExportService(repository, paths)
        first = service.build(
            editor.state,
            preset,
            units,
            project_root / "exports" / "first.mp4",
            overwrite=False,
        )
        assert [item.status for item in first.units] == ["rendered"] * 3
        assert first.audio.status == "rendered"
        assert first.assembly_status == "assembled"

        second = service.build(
            editor.state,
            preset,
            units,
            project_root / "exports" / "second.mp4",
            overwrite=False,
        )
        assert [item.status for item in second.units] == ["reused"] * 3
        assert second.audio.status == "reused"
        assert second.assembly_status == "reused"

        editor.set_clip_transform(
            avatar_clips[1].id,
            ClipTransform(scale_x=0.8, scale_y=0.8),
        )
        third = service.build(
            editor.state,
            preset,
            units,
            project_root / "exports" / "third.mp4",
            overwrite=False,
        )
        assert [item.status for item in third.units] == [
            "reused",
            "rendered",
            "reused",
        ]
        assert third.audio.status == "reused"
        assert third.assembly_status == "assembled"
        assert third.export.requested_video_codec == "libx264"
        assert third.export.actual_video_codec == "libx264"
        assert first.units[0].sha256 == third.units[0].sha256
        assert first.units[1].sha256 != third.units[1].sha256
        assert first.units[2].sha256 == third.units[2].sha256
        assert third.export.output_path.is_file()
        probe = MediaProbe(paths).probe(
            third.export.output_path,
            timeline_profile=editor.state.sequence.profile,
        )
        assert probe.metadata.duration_frames == 75
        assert probe.metadata.has_video is True
        assert probe.metadata.has_audio is True

        editor.set_clip_audio(clips[1].id, ClipAudio(gain_db=-3.0))
        fourth = service.build(
            editor.state,
            preset,
            units,
            project_root / "exports" / "fourth.mp4",
            overwrite=False,
        )
        assert [item.status for item in fourth.units] == ["reused"] * 3
        assert fourth.audio.status == "rendered"
        assert fourth.assembly_status == "assembled"
        assert [item.sha256 for item in third.units] == [
            item.sha256 for item in fourth.units
        ]

        fourth.units[1].output_path.write_bytes(b"corrupted-cache-proof")
        fifth = service.build(
            editor.state,
            preset,
            units,
            project_root / "exports" / "fifth.mp4",
            overwrite=False,
        )
        assert [item.status for item in fifth.units] == [
            "reused",
            "rendered",
            "reused",
        ]
        assert fifth.audio.status == "reused"
        assert fifth.assembly_status == "reused"
        archived_invalid_cache = list(
            (service.cache_root / "archive" / "visual").glob("*")
        )
        assert any(
            item.is_file() and item.read_bytes() == b"corrupted-cache-proof"
            for item in archived_invalid_cache
        )

        project_app = EditorProject(
            repository,
            settings=ServiceSettings(),
            paths=paths,
        )
        try:
            task_output = project_root / "exports" / "task-backed.mp4"
            started = project_app.start_task(
                BuildSequenceCommand(
                    sequence_id=project.main_sequence_id,
                    units=units,
                    output_path=str(task_output),
                    format=ExportFormat.H264,
                    preset=preset,
                ),
                [asset.id for asset in [*imported, *imported_avatars]],
                sequence_id=project.main_sequence_id,
            )
            completed = project_app.wait_for_task(started.id, timeout=90)
            assert completed.status == TaskStatus.COMPLETED, completed.error
            assert isinstance(completed.outcome, SequenceBuildTaskOutcome)
            assert [item.status for item in completed.outcome.units] == [
                "reused",
                "reused",
                "reused",
            ]
            assert completed.outcome.audio.status == "reused"
            assert completed.outcome.assembly_status == "reused"
            report_path = completed.outcome.report.resolve(project_root)
            assert report_path in [
                artifact.resolve(project_root) for artifact in completed.artifacts
            ]
            report = json.loads(report_path.read_text(encoding="utf-8"))
            assert report["protocol"] == "mediaflow-sequence-build-report"
            assert report["task_id"] == started.id
            assert [item["status"] for item in report["units"]] == [
                "reused",
                "reused",
                "reused",
            ]
            assert report["audio"]["status"] == "reused"
            assert report["assembly"]["status"] == "reused"
            task_probe = MediaProbe(paths).probe(
                task_output,
                timeline_profile=editor.state.sequence.profile,
            )
            assert task_probe.metadata.duration_frames == 75
            assert task_probe.metadata.has_video is True
            assert task_probe.metadata.has_audio is True

            service_base_revision = project_app.content_revision()
            sequence_profile = editor.state.sequence.profile
            project_app.close()
            project_app = None

            cli_output = project_root / "exports" / "public-cli.mp4"
            cli_request = {
                "protocol": "mediaflow-editor",
                "version": 4,
                "operation": "export.sequence.build",
                "project": str(project_root),
                "request_id": "segmented-build-public-cli",
                "base_revision": service_base_revision,
                "actor": {"kind": "agent", "id": "segmented-export-test"},
                "client_id": "pytest-segmented-export",
                "arguments": {
                    "sequence_id": project.main_sequence_id,
                    "units": [unit.model_dump(mode="json") for unit in units],
                    "output_path": str(cli_output),
                    "format": ExportFormat.H264.value,
                    "preset": preset.model_dump(mode="json"),
                    "timeout": 90,
                },
            }
            cli = subprocess.run(
                [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
                cwd=Path(__file__).resolve().parents[3],
                input=json.dumps(cli_request),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                check=False,
            )
            assert cli.returncode == 0, cli.stdout
            cli_result = json.loads(cli.stdout)
            assert cli_result["ok"] is True
            cli_task_receipt = cli_result["result"]["result"]["task"]
            cli_task = EditorServiceApi().execute(
                "task.wait",
                project=project_root,
                arguments={"task_id": cli_task_receipt["id"], "timeout": 90},
            )["task"]
            assert cli_task["status"] == "completed"
            assert [item["status"] for item in cli_task["outcome"]["units"]] == [
                "reused",
                "reused",
                "reused",
            ]
            assert cli_task["outcome"]["audio"]["status"] == "reused"
            assert cli_task["outcome"]["assembly_status"] == "reused"
            cli_probe = MediaProbe(paths).probe(
                cli_output,
                timeline_profile=sequence_profile,
            )
            assert cli_probe.metadata.duration_frames == 75
            assert cli_probe.metadata.has_video is True
            assert cli_probe.metadata.has_audio is True
        finally:
            if project_app is not None:
                project_app.close()
            subprocess.run(
                [sys.executable, "-m", "mediaflow.cli", "service", "shutdown"],
                cwd=Path(__file__).resolve().parents[3],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
