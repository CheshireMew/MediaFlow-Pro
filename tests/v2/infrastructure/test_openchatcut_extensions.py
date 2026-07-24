from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from mediaflow.application.asset_service import AssetService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import TaskStatus, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import GlobalSettings
from mediaflow.domain.task_commands import AnalyzeScenesCommand, TrackSubjectCommand
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from tests.v2.infrastructure.test_media_pipeline import (
    generate_black_intro_video,
    generate_real_media,
)


def test_scene_and_subject_tasks_write_observable_timeline_results(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "scene-source.mp4"
    generate_black_intro_video(source, paths)
    repository = ProjectRepository.create(tmp_path / "Visual Project", "Visual Project")
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    asset = assets.adopt_main_profile_from_video(asset.id)
    project = EditorProject(repository, settings=GlobalSettings(), paths=paths)
    try:
        sequence_id = repository.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        task_progress: dict[str, list[OperationProgress]] = {}

        def record_progress(event) -> None:
            task_progress.setdefault(event.task_id, []).append(
                OperationProgress.model_validate(event.payload["progress"])
            )

        project.tasks.events.subscribe(record_progress, include_snapshot=False)
        scene_task = project.start_task(
            AnalyzeScenesCommand(sequence_id=sequence_id, clip_id=clip.id, threshold=0.1),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed_scene = project.tasks.wait(scene_task.id, timeout=60)
        assert completed_scene.status == TaskStatus.COMPLETED, completed_scene.error
        scene_artifact = repository.project_dir / completed_scene.artifacts[0]
        scene_payload = json.loads(scene_artifact.read_text(encoding="utf-8"))
        state = repository.load_timeline(sequence_id)
        assert scene_payload["frames"]
        assert [marker.frame for marker in state.markers] == scene_payload["frames"]
        assert all(marker.name.startswith(f"场景切点 · {clip.id[:8]}") for marker in state.markers)
        scene_measurements = [
            item
            for item in task_progress[scene_task.id]
            if item.message_code == "scene_detection_analyzing"
        ]
        assert scene_measurements
        assert scene_measurements[-1].completed == scene_measurements[-1].total

        tracking_task = project.start_task(
            TrackSubjectCommand(
                sequence_id=sequence_id,
                clip_id=clip.id,
                mode="auto_reframe",
            ),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed_tracking = project.tasks.wait(tracking_task.id, timeout=60)
        assert completed_tracking.status == TaskStatus.COMPLETED, completed_tracking.error
        state = repository.load_timeline(sequence_id)
        tracked = next(item for item in state.clips if item.id == clip.id)
        assert len(tracked.transform_keyframes) >= 2
        assert all(item.source == "auto_reframe" for item in tracked.transform_keyframes)
        tracking_measurements = [
            item
            for item in task_progress[tracking_task.id]
            if item.message_code == "subject_tracking_analyzing"
        ]
        assert tracking_measurements
        assert tracking_measurements[-1].completed == tracking_measurements[-1].total
        xml = TimelineCompiler(repository).compile(state).xml
        transform_filter = ET.fromstring(xml).find(f".//filter[@id='transform_{clip.id}']")
        assert transform_filter is not None
        rect = next(
            item.text
            for item in transform_filter.findall("property")
            if item.attrib.get("name") == "rect"
        )
        assert rect is not None and ";" in rect and "=" in rect
    finally:
        project.close()


def test_fcpxml_exports_real_media_timing_markers_and_captions(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    subtitle = tmp_path / "source.zh.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\n城市夜景\n",
        encoding="utf-8",
    )
    with ProjectRepository.create(tmp_path / "FCPXML Project", "FCPXML Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        asset = assets.import_external(source)
        asset = assets.adopt_main_profile_from_video(asset.id)
        sequence_id = repository.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        subtitle_track = editor.add_track(TrackKind.SUBTITLE)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=5,
            source_in=2,
            duration=20,
        )
        editor.add_marker(10, "重点")
        publication = SubtitlePublicationService(repository)
        document = SubtitleAcquisitionService(repository, publication).import_subtitle_file(
            subtitle,
            assets,
            media_asset_id=asset.id,
        )
        repository.place_subtitle_document(
            document.id,
            subtitle_track.id,
            offset_frames=5,
            follow_clips=False,
        )
        output = FcpxmlExportService(repository).export(
            repository.load_timeline(sequence_id),
            tmp_path / "handoff.fcpxml",
        )
        root = ET.parse(output).getroot()
        resource = root.find("./resources/asset")
        exported_clip = root.find(".//asset-clip")
        marker = root.find(".//marker")
        caption = root.find(".//caption/text/text-style")
        assert root.attrib["version"] == "1.11"
        assert resource is not None and resource.attrib["src"] == source.resolve().as_uri()
        assert exported_clip is not None
        assert exported_clip.attrib["name"] == asset.name
        assert exported_clip.attrib["offset"] == "1/5s"
        assert exported_clip.attrib["start"] == "2/25s"
        assert exported_clip.attrib["duration"] == "4/5s"
        assert marker is not None and marker.attrib["value"] == "重点"
        assert caption is not None and caption.text == "城市夜景"
