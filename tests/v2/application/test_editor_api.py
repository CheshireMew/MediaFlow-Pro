from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

from mediaflow.cli import execute_request, main
from mediaflow.composition import EditorApplication
from mediaflow.domain.enums import TrackKind


def _write_wave(path: Path) -> None:
    sample_rate = 48_000
    frames = bytearray()
    for index in range(sample_rate):
        value = int(math.sin(2 * math.pi * 440 * index / sample_rate) * 8_000)
        frames.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(frames)


def test_cli_and_desktop_composition_api_share_real_persisted_task_chain(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    project_path = tmp_path / "Headless Project"

    created = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 1,
            "operation": "project.create",
            "project": str(project_path),
            "arguments": {"name": "Headless Project"},
        },
        application=application,
    )
    assert created["project"]["name"] == "Headless Project"
    assert created["sequences"][0]["id"] == created["project"]["main_sequence_id"]

    source = tmp_path / "tone.wav"
    _write_wave(source)
    imported = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 1,
            "operation": "asset.import",
            "project": str(project_path),
            "arguments": {"source": str(source)},
        },
        application=application,
    )
    asset_id = imported["asset"]["id"]

    completed = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 1,
            "operation": "task.start",
            "project": str(project_path),
            "arguments": {
                "task_command": {
                    "command_type": "generate_waveform",
                    "asset_id": asset_id,
                },
                "input_asset_ids": [asset_id],
                "timeout": 60,
            },
        },
        application=application,
    )["task"]
    assert completed["status"] == "completed"
    assert completed["artifacts"]

    inspected = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 1,
            "operation": "project.inspect",
            "project": str(project_path),
        },
        application=application,
    )
    asset = next(item for item in inspected["assets"] if item["id"] == asset_id)
    assert asset["waveform_path"]
    assert (project_path / asset["waveform_path"]).is_file()
    assert inspected["tasks"][-1]["id"] == completed["id"]

    request_path = tmp_path / "inspect-request.json"
    request_path.write_text(
        json.dumps(
            {
                "protocol": "mediaflow-cli",
                "version": 1,
                "operation": "project.inspect",
                "project": str(project_path),
            }
        ),
        encoding="utf-8",
    )
    assert main(["execute", "--request", str(request_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["result"]["assets"][0]["id"] == asset_id


def test_timeline_navigation_preserves_one_project_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    with application.create_project(tmp_path / "Project", "Project") as project:
        main = project.documents.get_project().main_sequence_id
        main_editor = project.timeline(main)
        main_editor.add_marker(10, "Keep undo")
        short = project.documents.create_short_sequence("Short")

        assert project.timeline(short.id) is project.timeline(short.id)
        assert project.timeline(main) is main_editor
        assert project.history.can_undo is True

        project.timeline(short.id)
        assert project.history.can_undo is True
        project.history.undo()
        assert project.documents.load_timeline(main).markers == []


def test_preview_snapshots_are_content_addressed_and_never_overwrite_active_graph(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    source = tmp_path / "preview-tone.wav"
    _write_wave(source)

    with application.create_project(tmp_path / "Preview Project", "Preview Project") as project:
        asset = project.assets.import_external(source)
        editor = project.timeline(project.documents.get_project().main_sequence_id)
        audio_track = next(track for track in editor.state.tracks if track.kind == TrackKind.AUDIO)
        clip = editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=min(25, asset.metadata.duration_frames),
        )

        first = application.write_preview_snapshot(
            project.project_dir,
            editor.state,
            use_proxies=False,
            prefer_sdr_preview_proxy=False,
        )
        first_xml = first.read_text(encoding="utf-8")
        editor.move_clip(clip.id, timeline_start=8)
        second = application.write_preview_snapshot(
            project.project_dir,
            editor.state,
            use_proxies=False,
            prefer_sdr_preview_proxy=False,
        )
        repeated = application.write_preview_snapshot(
            project.project_dir,
            editor.state,
            use_proxies=False,
            prefer_sdr_preview_proxy=False,
        )

        assert first != second
        assert repeated == second
        assert first.is_file() and second.is_file()
        assert first.read_text(encoding="utf-8") == first_xml
        assert "-preview-" in first.name and "-preview-" in second.name
        assert not (
            project.project_dir / "cache" / "mlt" / f"{editor.state.sequence.id}-preview.mlt"
        ).exists()
        assert not list((project.project_dir / "cache" / "mlt").glob("*.partial"))


def test_short_sequence_archive_is_recoverable_through_project_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    with application.create_project(tmp_path / "Project", "Project") as project:
        short = project.documents.create_short_sequence("Recoverable")

        project.archive_short_sequence(short.id)
        assert short.id not in {item.id for item in project.documents.list_sequences()}
        assert project.documents.get_sequence(short.id).archived is True

        project.history.undo()
        assert short.id in {item.id for item in project.documents.list_sequences()}
        assert project.documents.get_sequence(short.id).archived is False

        project.history.redo()
        assert short.id not in {item.id for item in project.documents.list_sequences()}
