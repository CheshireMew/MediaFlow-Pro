from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediaflow.composition import EditorProject
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.sequence_audio import select_audible_sequence_audio
from mediaflow.domain.settings import ServiceSettings
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.segmented_export_service import SegmentedExportService
from mediaflow.production_runner import _required_audio_provenance, run_production_bundle
from tests.v2.application.test_audio_routing_automation import invoke
from tests.v2.application.test_production_bundle import _bundle, _timeline_with_one_frame_cover
from tests.v2.editor_service_api import EditorServiceApi
from tests.v2.real_media import generate_real_media


@pytest.mark.parametrize(
    "blocked_by", ["audio_mute", "audio_solo", "bus_mute", "bus_solo", "video_mute", "video_solo"]
)
def test_required_original_audio_follows_the_actual_export_route(tmp_path: Path, blocked_by: str) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "voice.mp4"
    music = tmp_path / "music.wav"
    generate_real_media(source, paths, width=160, height=90)
    generated = FfmpegRunner(paths.ffmpeg).run(
        ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1", "-y", str(music)], timeout=30
    )
    assert generated.returncode == 0, generated.stderr
    project = EditorProject(
        ProjectRepository.create(tmp_path / "Project", "Audio"), settings=ServiceSettings(), paths=paths
    )
    try:
        sequence_id = project.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        asset = project.import_external_asset(source)
        music_asset = project.import_external_asset(music)
        video = editor.add_track(TrackKind.VIDEO, "Original")
        clip = editor.add_clip(
            track_id=video.id, asset_id=asset.id, timeline_start=0, source_in=0, duration=25
        )
        video = next(track for track in editor.state.tracks if track.id == video.id)
        audio = next(track for track in editor.state.tracks if track.id == video.linked_audio_track_id)
        bed = editor.add_track(TrackKind.AUDIO, "Music")
        editor.add_clip(track_id=bed.id, asset_id=music_asset.id, timeline_start=0, source_in=0, duration=25)
        overlay = editor.add_track(TrackKind.VIDEO, "Overlay")
        buses = project.list_audio_buses(sequence_id)
        master = next(bus for bus in buses if bus.parent_bus_id is None)
        voice_bus = invoke(
            project,
            "audio.bus.add",
            {"sequence_id": sequence_id, "name": "Voice", "parent_bus_id": master.id},
        )["bus"]
        music_bus = invoke(
            project,
            "audio.bus.add",
            {"sequence_id": sequence_id, "name": "Music", "parent_bus_id": master.id},
        )["bus"]
        invoke(
            project,
            "timeline.track.audio.route",
            {"sequence_id": sequence_id, "track_id": audio.id, "bus_id": voice_bus["id"]},
        )
        invoke(
            project,
            "timeline.track.audio.route",
            {"sequence_id": sequence_id, "track_id": bed.id, "bus_id": music_bus["id"]},
        )

        def check(expected: bool) -> None:
            audible = invoke(project, "audio.inspect", {"sequence_id": sequence_id})["audible_clip_ids"]
            audio_state = SegmentedExportService(project._repository, paths)._audio_state(editor.state)
            projected = select_audible_sequence_audio(
                audio_state,
                {asset.id: asset for asset in project.list_assets()},
                project.list_audio_buses(sequence_id),
            )
            assert set(projected.clip_ids) == set(audible)
            assert (clip.id in audible) is expected
            imported = {"source_assets": {"original": asset.model_dump(mode="json")}}
            timeline = editor.state.model_dump(mode="json")
            if expected:
                assert (
                    _required_audio_provenance(imported, timeline, ["original"], audible)[0]["active_clips"][
                        0
                    ]["clip_id"]
                    == clip.id
                )
            else:
                assert audible  # Music still reaches the master; file-level audio presence is insufficient.
                with pytest.raises(RuntimeError, match="not active after native import"):
                    _required_audio_provenance(imported, timeline, ["original"], audible)

        check(True)
        target = {"audio_mute": audio, "audio_solo": bed, "video_mute": video, "video_solo": overlay}.get(
            blocked_by
        )
        if target:
            editor.set_track_state(
                target.id,
                enabled=True,
                locked=False,
                muted=blocked_by.endswith("mute"),
                solo=blocked_by.endswith("solo"),
            )
        else:
            bus_id = voice_bus["id"] if blocked_by == "bus_mute" else music_bus["id"]
            invoke(
                project,
                "audio.bus.update",
                {
                    "bus_id": bus_id,
                    "changes": {"muted" if blocked_by == "bus_mute" else "solo": True},
                },
            )
        check(False)
        if target:
            editor.set_track_state(target.id, enabled=True, locked=False, muted=False, solo=False)
        else:
            invoke(
                project,
                "audio.bus.update",
                {"bus_id": bus_id, "changes": {"muted": False, "solo": False}},
            )
        check(True)
    finally:
        project.close()


def test_public_produce_rejects_inaudible_original_and_accepts_restored_route(tmp_path: Path) -> None:
    # Derive the delivery input from the real producer-generated portable case.
    timeline_path = _timeline_with_one_frame_cover(tmp_path)
    document = json.loads(timeline_path.read_text(encoding="utf-8"))
    background = timeline_path.parent / "sources" / "background.wav"
    paths = RuntimeContext.discover().paths
    generated = FfmpegRunner(paths.ffmpeg).run(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=6",
            "-y",
            str(background),
        ],
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr
    document["sources"].append(
        {
            "id": "background",
            "kind": "audio",
            "file": "sources/background.wav",
            "sha256": sha256_file(background),
            "duration_seconds": 6,
        }
    )
    document["tracks"].append(
        {
            "id": "background",
            "kind": "audio",
            "name": "Background",
            "muted": False,
            "clips": [
                {
                    "id": "background-clip",
                    "type": "media",
                    "source_id": "background",
                    "timeline_start_seconds": 0,
                    "source_in_seconds": 0,
                    "duration_seconds": 6,
                    "audio_enabled": True,
                }
            ],
        }
    )
    timeline_path.write_text(json.dumps(document), encoding="utf-8")
    bundle = _bundle(tmp_path, timeline_path)
    bundle["speech_review"] = None
    first = run_production_bundle(bundle)
    assert first["status"] == "completed"
    api = EditorServiceApi()
    project_path = first["project"]["path"]
    sequence_id = first["project"]["sequence_id"]
    original = first["timeline"]["required_audio_provenance"][0]["active_clips"][0]
    buses = api.execute("audio.inspect", project=project_path, arguments={"sequence_id": sequence_id})[
        "buses"
    ]
    master = next(bus for bus in buses if bus["parent_bus_id"] is None)
    voice_bus = api.execute(
        "audio.bus.add",
        project=project_path,
        arguments={"sequence_id": sequence_id, "name": "Required original", "parent_bus_id": master["id"]},
    )["bus"]
    api.execute(
        "timeline.track.audio.route",
        project=project_path,
        arguments={"sequence_id": sequence_id, "track_id": original["track_id"], "bus_id": voice_bus["id"]},
    )
    api.execute(
        "audio.bus.update",
        project=project_path,
        arguments={"bus_id": voice_bus["id"], "changes": {"muted": True}},
    )
    audible = api.execute("audio.inspect", project=project_path, arguments={"sequence_id": sequence_id})[
        "audible_clip_ids"
    ]
    assert audible and original["clip_id"] not in audible
    bundle["project"] = {"existing_path": project_path}
    bundle["request_id"] = "muted-original"
    with pytest.raises(RuntimeError, match="not active after native import: music"):
        run_production_bundle(bundle)
    api.execute(
        "audio.bus.update",
        project=project_path,
        arguments={"bus_id": voice_bus["id"], "changes": {"muted": False}},
    )
    bundle["request_id"] = "restored-original"
    restored = run_production_bundle(bundle)
    assert restored["status"] == "completed"
    assert restored["timeline"]["required_audio_provenance"][0]["active_clips"]
