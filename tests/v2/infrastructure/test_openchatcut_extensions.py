from __future__ import annotations

import json
import threading
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    ColorMode,
    TaskStatus,
    TrackKind,
    TransitionKind,
)
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.storage_names import (
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    python_io_path,
    utf16_units,
)
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import AnalyzeScenesCommand, TrackSubjectCommand
from mediaflow.domain.timeline import (
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    FreezeClipAddRequest,
)
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.subtitle_file_store import LocalSubtitleFileStore
from mediaflow.infrastructure.subtitle_publication_storage import (
    LocalSubtitlePublicationStorage,
)
from tests.v2.infrastructure.test_media_pipeline import (
    generate_black_intro_video,
    generate_real_media,
)


def test_scene_and_subject_tasks_write_observable_timeline_results(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "scene-source.mp4"
    generate_black_intro_video(source, paths)
    repository = ProjectRepository.create(tmp_path / "Visual Project", "Visual Project")
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    asset = assets.adopt_main_profile_from_video(asset.id)
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        sequence_id = repository.projects.get_project().main_sequence_id
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
        user_marker_id = ""
        user_write_error: list[BaseException] = []

        def write_user_marker() -> None:
            nonlocal user_marker_id
            try:
                user_marker_id = project.timeline(sequence_id).add_marker(
                    1,
                    "分析期间用户标记",
                ).id
            except BaseException as error:
                user_write_error.append(error)

        def record_progress(event) -> None:
            progress = OperationProgress.model_validate(event.payload["progress"])
            task_progress.setdefault(event.task_id, []).append(
                progress
            )
            if (
                not user_marker_id
                and progress.message_code == "scene_detection_analyzing"
            ):
                writer = threading.Thread(
                    target=write_user_marker,
                    name="mediaflow-test-human-writer",
                )
                writer.start()
                writer.join(timeout=10)
                if writer.is_alive():
                    user_write_error.append(
                        TimeoutError("Human project command did not settle")
                    )

        project.subscribe_task_events(record_progress, include_snapshot=False)
        scene_task = project.start_task(
            AnalyzeScenesCommand(sequence_id=sequence_id, clip_id=clip.id, threshold=0.1),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed_scene = project.wait_for_task(scene_task.id, timeout=60)
        assert completed_scene.status == TaskStatus.COMPLETED, completed_scene.error
        assert not user_write_error, user_write_error
        scene_artifact = completed_scene.artifacts[0].resolve(repository.project_dir)
        scene_payload = json.loads(scene_artifact.read_text(encoding="utf-8"))
        state = repository.timeline.load_timeline(sequence_id)
        assert scene_payload["frames"]
        assert user_marker_id
        assert next(marker for marker in state.markers if marker.id == user_marker_id).name == (
            "分析期间用户标记"
        )
        scene_markers = [
            marker
            for marker in state.markers
            if marker.name.startswith(f"场景切点 · {clip.id[:8]}")
        ]
        assert [marker.frame for marker in scene_markers] == scene_payload["frames"]
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
        completed_tracking = project.wait_for_task(tracking_task.id, timeout=60)
        assert completed_tracking.status == TaskStatus.COMPLETED, completed_tracking.error
        state = repository.timeline.load_timeline(sequence_id)
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
        xml = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(state).xml
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
    paths = RuntimeContext.discover().paths
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
        sequence_id = repository.projects.get_project().main_sequence_id
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
        editor.add_marker(2, "空白区标记")
        editor.add_marker(10, "重点")
        publication = SubtitlePublicationService(repository, LocalSubtitlePublicationStorage())
        document = SubtitleAcquisitionService(
            repository,
            publication,
            LocalSubtitleFileStore(),
        ).import_subtitle_file(
            subtitle,
            assets,
            media_asset_id=asset.id,
        )
        repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            offset_frames=5,
            follow_clips=False,
        )
        output = FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
            repository.timeline.load_timeline(sequence_id),
            tmp_path / "handoff.fcpxml",
        )
        root = ET.parse(output).getroot()
        resource = root.find("./resources/asset")
        exported_clip = root.find(".//asset-clip")
        timeline_container = root.find(
            "./library/event/project/sequence/spine/clip"
        )
        markers = (
            timeline_container.findall("marker")
            if timeline_container is not None
            else []
        )
        caption_element = root.find(".//caption")
        caption = root.find(".//caption/text/text-style")
        assert root.attrib["version"] == "1.11"
        media_rep = resource.find("media-rep") if resource is not None else None
        assert media_rep is not None
        assert media_rep.attrib["src"] == source.resolve().as_uri()
        assert resource is not None
        assert resource.attrib["hasVideo"] == "1"
        assert resource.attrib["hasAudio"] == "1"
        assert exported_clip is not None
        assert exported_clip.attrib["name"] == asset.name
        assert exported_clip.attrib["offset"] == "1/5s"
        assert exported_clip.attrib["start"] == "2/25s"
        assert exported_clip.attrib["duration"] == "4/5s"
        assert exported_clip.find("audio-channel-source") is None
        assert [
            (marker.attrib["start"], marker.attrib["value"])
            for marker in markers
        ] == [
            ("2/25s", "空白区标记"),
            ("2/5s", "重点"),
        ]
        assert exported_clip.find("marker") is None
        assert caption_element is not None
        assert caption_element.attrib["role"] == (
            "ITT Subtitles?captionFormat=ITT.zh-CN"
        )
        assert caption is not None and caption.text == "城市夜景"
        original = output.read_bytes()
        with pytest.raises(FileExistsError):
            FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
                repository.timeline.load_timeline(sequence_id),
                output,
            )
        assert output.read_bytes() == original


def test_fcpxml_uses_effective_tracks_retime_maps_and_hdr10_pq(tmp_path: Path) -> None:
    source = tmp_path / "retime.mp4"
    source.write_bytes(b"fcpxml-source")
    with ProjectRepository.create(tmp_path / "FCPXML Semantics", "FCPXML Semantics") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={
                            "duration_frames": 100,
                            "has_video": True,
                        }
                    )
                }
            )
        )
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        editor.set_sequence_profile(
            editor.state.sequence.profile.model_copy(
                update={
                    "color_mode": ColorMode.HDR10_BT2020_PQ,
                    "bit_depth": 10,
                    "audio_channels": 1,
                }
            )
        )
        active_track = editor.add_track(TrackKind.VIDEO, "Active")
        disabled_track = editor.add_track(TrackKind.VIDEO, "Disabled")
        subtitle_track = editor.add_track(TrackKind.SUBTITLE, "Disabled Captions")
        editor.add_clip(
            track_id=active_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=30,
            duration=10,
            speed_numerator=-2,
            speed_denominator=1,
        )
        editor.add_clip(
            track_id=disabled_track.id,
            asset_id=asset.id,
            timeline_start=10,
            source_in=0,
            duration=10,
        )
        editor.set_track_state(
            disabled_track.id,
            enabled=False,
            locked=False,
            muted=False,
            solo=False,
        )
        editor.set_track_state(
            subtitle_track.id,
            enabled=False,
            locked=False,
            muted=False,
            solo=False,
        )
        document = SubtitleDocument(
            project_id=repository.projects.get_project().id,
            asset_id=asset.id,
            language="en",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=0,
            end_frame=10,
            text="must stay hidden",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
            follow_clips=False,
        )

        output = FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
            repository.timeline.load_timeline(sequence_id),
            tmp_path / "semantic-handoff.fcpxml",
        )
        root = ET.parse(output).getroot()
        format_element = root.find("./resources/format")
        video_components = root.findall(".//clip/video")
        time_points = root.findall(".//clip/video/timeMap/timept")

        assert format_element is not None
        assert format_element.attrib["colorSpace"] == "9-16-9 (Rec. 2020 PQ)"
        assert root.find(".//sequence").attrib["audioLayout"] == "mono"
        assert root.find(".//asset-clip") is None
        assert len(video_components) == 1
        assert video_components[0].attrib["start"] == "1s"
        assert video_components[0].attrib["duration"] == "1/3s"
        assert [point.attrib["value"] for point in time_points] == ["1s", "1/3s"]
        assert root.find(".//caption") is None


def test_short_sequence_clock_is_shared_by_mlt_and_fcpxml(tmp_path: Path) -> None:
    main_profile = ProjectProfile(fps_numerator=25, fps_denominator=1)
    source = tmp_path / "short-clock.mp4"
    source.write_bytes(b"short-clock-source")
    with ProjectRepository.create(
        tmp_path / "Short Clock Export",
        "Short Clock Export",
        main_profile,
    ) as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={"duration_frames": 25, "has_video": True}
                    )
                }
            )
        )
        short = repository.sequences.create_short_sequence(
            "30 fps short",
            main_profile.model_copy(update={"fps_numerator": 30}),
        )
        editor = TimelineEditor(repository, short.id)
        primary_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=primary_track.id,
            asset_id=asset.id,
            timeline_start=5,
            source_in=0,
            duration=30,
        )
        overlap_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=overlap_track.id,
            asset_id=asset.id,
            timeline_start=10,
            source_in=0,
            duration=10,
        )
        editor.add_marker(2, "空白区")
        editor.add_marker(15, "重叠区")
        state = editor.state

        compiled = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(state)
        assert compiled.duration_frames == 35
        output = FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
            state,
            tmp_path / "short-clock.fcpxml",
        )
        root = ET.parse(output).getroot()
        resource = root.find("./resources/asset")
        exported_videos = root.findall(".//clip/video")
        sequence = root.find(".//sequence")
        timeline_container = root.find(
            "./library/event/project/sequence/spine/clip"
        )
        assert resource is not None and resource.attrib["duration"] == "1s"
        assert [video.attrib["duration"] for video in exported_videos] == [
            "1s",
            "1/3s",
        ]
        assert sequence is not None and sequence.attrib["duration"] == "7/6s"
        assert timeline_container is not None
        assert [
            (marker.attrib["start"], marker.attrib["value"])
            for marker in timeline_container.findall("marker")
        ] == [
            ("1/15s", "空白区"),
            ("1/2s", "重叠区"),
        ]
        assert len(root.findall(".//marker")) == 2
        assert all(
            not clip.findall("marker")
            for spine in timeline_container.findall("spine")
            for clip in spine.findall("clip")
        )


def test_fcpxml_long_destination_uses_a_short_atomic_sibling(
    tmp_path: Path,
) -> None:
    source = tmp_path / "long-handoff-source.mp4"
    source.write_bytes(b"long-handoff-source")
    with ProjectRepository.create(
        tmp_path / "Long FCPXML Project",
        "Long FCPXML Project",
    ) as repository:
        asset = repository.assets.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={
                            "duration_frames": 30,
                            "has_video": True,
                        }
                    )
                }
            )
        )
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )

        parent = tmp_path
        filename = f"{'feature-handoff-' * 6}timeline.fcpxml"
        while len(str(parent / filename)) < 240:
            parent /= "deep-user-export-folder"
        parent.mkdir(parents=True)
        destination = (parent / filename).resolve()
        output = FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
            editor.state,
            destination,
        )

        assert output == destination
        assert ET.parse(python_io_path(output)).getroot().attrib["version"] == "1.11"
        assert not [
            item
            for item in parent.iterdir()
            if item.is_file() and item.name.startswith(".mf-")
        ]


def test_fcpxml_preserves_linked_mute_detached_components_and_adjustments(
    tmp_path: Path,
) -> None:
    source = tmp_path / "semantic-source.mov"
    source.write_bytes(b"semantic-source")
    with ProjectRepository.create(
        tmp_path / "FCPXML Component Semantics",
        "FCPXML Component Semantics",
    ) as repository:
        asset = repository.assets.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={
                            "duration_frames": 120,
                            "width": 1920,
                            "height": 1080,
                            "has_video": True,
                            "has_audio": True,
                        }
                    )
                }
            )
        )
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)

        linked_track = editor.add_track(
            TrackKind.VIDEO,
            "Linked muted audio",
        )
        linked_clip = editor.add_clip(
            track_id=linked_track.id,
            asset_id=asset.id,
            timeline_start=20,
            source_in=40,
            duration=10,
        )
        assert linked_clip.media_kind == ClipMediaKind.LINKED_AV
        linked_track = next(
            track
            for track in editor.state.tracks
            if track.id == linked_track.id
        )
        linked_audio_track = next(
            track
            for track in editor.state.tracks
            if track.id == linked_track.linked_audio_track_id
        )
        editor.set_track_state(
            linked_audio_track.id,
            enabled=True,
            locked=False,
            muted=True,
            solo=False,
        )

        component_track = editor.add_track(
            TrackKind.VIDEO,
            "Detached components",
        )
        first_linked = editor.add_clip(
            track_id=component_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=10,
            speed_numerator=2,
        )
        second_linked = editor.add_clip(
            track_id=component_track.id,
            asset_id=asset.id,
            timeline_start=10,
            source_in=20,
            duration=10,
        )
        first_video, first_audio = editor.detach_clip_audio(
            first_linked.id
        )
        second_video, second_audio = editor.detach_clip_audio(
            second_linked.id
        )
        assert first_video.media_kind == ClipMediaKind.VIDEO_ONLY
        assert first_audio.media_kind == ClipMediaKind.AUDIO_ONLY
        assert second_video.media_kind == ClipMediaKind.VIDEO_ONLY
        assert second_audio.media_kind == ClipMediaKind.AUDIO_ONLY

        editor.set_clip_transform(
            first_video.id,
            ClipTransform(
                x=10,
                y=5,
                scale_x=1.2,
                scale_y=0.8,
                rotation=15,
                crop_left=0.1,
                crop_top=0.2,
                crop_right=0.05,
                crop_bottom=0.1,
                opacity=0.75,
            ),
        )
        editor.set_clip_transform_keyframes(
            first_video.id,
            [
                ClipTransformKeyframe(
                    source_frame=6,
                    transform=ClipTransform(
                        x=20,
                        y=-4,
                        scale_x=1.4,
                        scale_y=0.9,
                        rotation=30,
                        crop_left=0.2,
                        crop_top=0.1,
                        crop_right=0.1,
                        crop_bottom=0.05,
                        opacity=0.5,
                    ),
                )
            ],
        )
        editor.set_clip_audio(
            first_audio.id,
            ClipAudio(
                gain_db=-3,
                pan=0.25,
                fade_in_frames=2,
                fade_out_frames=3,
            ),
        )
        editor.create_transition(
            first_video.id,
            second_video.id,
            TransitionKind.DISSOLVE,
            4,
        )

        output = FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
            repository.timeline.load_timeline(sequence_id),
            tmp_path / "component-semantics.fcpxml",
        )
        root = ET.parse(output).getroot()
        resources = root.findall("./resources/asset")
        assert len(resources) == 1
        media_rep = resources[0].find("media-rep")
        assert media_rep is not None
        assert media_rep.attrib["src"] == source.resolve().as_uri()

        linked = root.find(".//asset-clip")
        assert linked is not None
        assert linked.attrib["offset"] == "2/3s"
        assert linked.attrib["start"] == "4/3s"
        assert linked.attrib["srcEnable"] == "video"
        assert linked.find("audio-channel-source") is None

        component_clips = [
            element
            for element in root.findall(".//clip")
            if element.find("video") is not None
            or element.find("audio") is not None
        ]
        video_clips = [
            element
            for element in component_clips
            if element.find("video") is not None
        ]
        audio_clips = [
            element
            for element in component_clips
            if element.find("audio") is not None
        ]
        assert len(video_clips) == 2
        assert len(audio_clips) == 2
        assert all(
            [
                child.tag
                for child in element
                if child.tag in {"video", "audio", "asset-clip"}
            ]
            == ["video"]
            for element in video_clips
        )
        assert all(
            [
                child.tag
                for child in element
                if child.tag in {"video", "audio", "asset-clip"}
            ]
            == ["audio"]
            for element in audio_clips
        )

        first_video_element = next(
            element
            for element in video_clips
            if element.attrib["offset"] == "0s"
        )
        video_component = first_video_element.find("video")
        assert video_component is not None
        assert video_component.attrib["start"] == "0s"
        assert video_component.attrib["duration"] == "1/3s"
        time_points = video_component.findall("timeMap/timept")
        assert [point.attrib["time"] for point in time_points] == [
            "0s",
            "1/3s",
        ]
        assert [point.attrib["value"] for point in time_points] == [
            "0s",
            "2/3s",
        ]

        crop = first_video_element.find("adjust-crop/trim-rect")
        transform = first_video_element.find("adjust-transform")
        blend = first_video_element.find("adjust-blend")
        assert crop is not None
        assert float(crop.attrib["left"]) == pytest.approx(
            17.7778,
            abs=0.0001,
        )
        assert crop.attrib["top"] == "20"
        assert transform is not None
        position_x, position_y = map(
            float,
            transform.attrib["position"].split(),
        )
        assert position_x == pytest.approx(17.7778, abs=0.0001)
        assert position_y == -5
        assert transform.attrib["scale"] == "1.2 0.8"
        assert transform.attrib["rotation"] == "-15"
        assert blend is not None and blend.attrib["amount"] == "0.75"
        transform_keys = transform.findall(
            "./param[@name='position']/keyframeAnimation/keyframe"
        )
        assert [key.attrib["time"] for key in transform_keys] == [
            "0s",
            "1/10s",
        ]
        assert transform_keys[1].attrib["value"].endswith(" 4")

        first_audio_element = next(
            element
            for element in audio_clips
            if element.attrib["offset"] == "0s"
        )
        volume = first_audio_element.find("adjust-volume")
        panner = first_audio_element.find("adjust-panner")
        assert volume is not None and volume.attrib["amount"] == "-3dB"
        assert volume.find("./param/fadeIn").attrib["duration"] == "1/15s"
        assert volume.find("./param/fadeOut").attrib["duration"] == "1/10s"
        assert panner is not None
        assert panner.attrib == {"mode": "1", "amount": "0.25"}

        transition = root.find(".//transition")
        assert transition is not None
        assert transition.attrib == {
            "name": "Cross Dissolve",
            "offset": "4/15s",
            "duration": "2/15s",
        }


def test_fcpxml_preflight_rejects_unreliable_transition_and_bus_processing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "preflight-source.mp4"
    source.write_bytes(b"preflight-source")
    with ProjectRepository.create(
        tmp_path / "FCPXML Preflight",
        "FCPXML Preflight",
    ) as repository:
        asset = repository.assets.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={
                            "duration_frames": 40,
                            "has_video": True,
                        }
                    )
                }
            )
        )
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        left = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=10,
        )
        right = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=10,
            source_in=10,
            duration=10,
        )
        transition = editor.create_transition(
            left.id,
            right.id,
            TransitionKind.WIPE_LEFT,
            4,
        )

        unsupported_output = (
            tmp_path
            / "must-not-be-created"
            / "unsupported-transition.fcpxml"
        )
        with pytest.raises(
            ValueError,
            match="标准交叉溶解",
        ):
            FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
                editor.state,
                unsupported_output,
            )
        assert not unsupported_output.parent.exists()

        editor.update_transition(
            transition.id,
            kind=TransitionKind.DISSOLVE,
            duration=4,
            parameters={},
        )
        freeze = editor.add_freeze_clip(
            FreezeClipAddRequest(
                track_id=track.id,
                asset_id=asset.id,
                timeline_start=20,
                source_frame=5,
                duration=5,
            )
        )
        with pytest.raises(ValueError, match="定格"):
            FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
                editor.state,
                tmp_path / "unsupported-freeze.fcpxml",
            )
        editor.delete_clip(freeze.id)

        master = next(
            bus
            for bus in repository.audio.list_audio_buses(sequence_id)
            if bus.parent_bus_id is None
        )
        repository.audio.save_audio_bus(
            master.model_copy(update={"gain_db": -1.5})
        )
        protected_output = tmp_path / "protected.fcpxml"
        protected_bytes = b"existing handoff"
        protected_output.write_bytes(protected_bytes)
        with pytest.raises(
            ValueError,
            match="音频总线",
        ):
            FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
                editor.state,
                protected_output,
                overwrite=True,
            )
        assert protected_output.read_bytes() == protected_bytes


def test_fcpxml_preflight_rejects_an_unusable_path_before_creating_directories(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(
        tmp_path / "FCPXML Path Preflight",
        "FCPXML Path Preflight",
    ) as repository:
        sequence_id = repository.projects.get_project().main_sequence_id
        state = repository.timeline.load_timeline(sequence_id)
        output_parent = tmp_path
        output = output_parent / "handoff.fcpxml"
        while utf16_units(str(output.resolve())) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT:
            output_parent /= "deep-export-directory"
            output = output_parent / "handoff.fcpxml"

        assert not output_parent.exists()
        with pytest.raises(ValueError, match="路径过深"):
            FcpxmlExportService(repository, RuntimeContext.discover().paths).export(state, output)
        assert not output_parent.exists()
