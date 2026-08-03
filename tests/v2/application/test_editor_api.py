from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.automation.contracts import describe_contract
from mediaflow.cli import execute_request, main
from mediaflow.composition import EditorApplication
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.runtime_capabilities import RUNTIME_CAPABILITY_IDS
from mediaflow.domain.storage_names import utf16_units
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.storage_paths import default_project_root

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


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


def test_recent_projects_disable_corrupt_databases_but_keep_upgradable_projects(
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "Corrupt project"
    corrupt.mkdir()
    (corrupt / "project.mfp").write_bytes(b"not a sqlite database")
    missing = tmp_path / "Missing project"
    upgradable = tmp_path / "Upgradable project"
    shutil.copytree(FIXTURES / "editable-media-v4-project", upgradable)

    snapshot = EditorApplication().recent_projects(
        [str(corrupt), str(missing), str(upgradable)]
    )
    rows = {Path(item["path"]).name: item for item in snapshot.items}

    assert rows[corrupt.name]["available"] is False
    assert rows[corrupt.name]["unavailableReason"] == "项目文件损坏或格式不受支持"
    assert rows[missing.name]["available"] is False
    assert rows[missing.name]["unavailableReason"] == "项目文件不存在"
    assert rows[upgradable.name]["available"] is True
    assert rows[upgradable.name]["unavailableReason"] == ""


def _run_cli_request(
    tmp_path: Path,
    project_path: Path,
    operation: str,
    arguments: dict | None = None,
) -> tuple[int, dict]:
    request_path = tmp_path / f"{operation.replace('.', '-')}.json"
    request_path.write_text(
        json.dumps(
            {
                "protocol": "mediaflow-cli",
                "version": 2,
                "operation": operation,
                "project": str(project_path),
                "arguments": arguments or {},
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediaflow.cli",
            "execute",
            "--request",
            str(request_path),
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_cli_describes_ai_transcript_plan_shape_without_hidden_references() -> None:
    contract = describe_contract()
    assert contract["product"] == PRODUCT_NAME
    assert contract["version"] == 2
    assert contract["default_project_root"] == default_project_root()
    assert "$ref" not in json.dumps(contract)
    assert "#/$defs/" not in json.dumps(contract)
    operations = {
        item["name"]: item
        for item in contract["operations"]
    }
    assert set(operations["project.create"]["arguments_schema"]["required"]) == {
        "name",
        "directory_name",
        "profile",
    }
    edit_schema = operations["transcript.edit.preview"]["arguments_schema"][
        "properties"
    ]["edit"]
    assert edit_schema["properties"]["selections"]["items"]["properties"]["kind"][
        "enum"
    ] == ["words", "segments"]
    assert "$ref" not in json.dumps(edit_schema)
    plan_schema = operations["transcript.edit.apply"]["arguments_schema"][
        "properties"
    ]["plan"]
    assert "plan_digest" in plan_schema["required"]
    assert operations["transcript.edit.apply"]["result_schema"]["required"] == [
        "edit"
    ]
    assert operations["runtime.inspect"]["project_access"] == "none"
    assert operations["speech.transcribe"]["project_access"] == "none"
    assert operations["speech.transcribe"]["required_capabilities"] == [
        "faster-whisper-xxl"
    ]
    assert set(operations["speech.transcribe"]["arguments_schema"]["required"]) == {
        "input_path",
        "output_path",
    }
    assert operations["speech.synthesize"]["project_access"] == "none"
    assert operations["speech.synthesize"]["required_capabilities"] == [
        "gpt-sovits-v2pro"
    ]
    batch_clip_schema = operations["timeline.clip.batch.add"][
        "arguments_schema"
    ]
    assert batch_clip_schema["properties"]["clips"]["minItems"] == 1
    assert operations["timeline.clip.batch.add"]["idempotency"] == "optional"
    assert set(operations["speech.synthesize"]["arguments_schema"]["required"]) == {
        "text",
        "text_language",
        "reference_audio",
        "reference_text",
        "reference_language",
        "output_path",
    }
    assert operations["export.fcpxml"]["required_capabilities"] == [
        "project-editing",
        "fcpxml-export",
        "chromium",
        "ffmpeg",
        "ffprobe",
    ]
    assert all(
        set(operation) == {
            "name",
            "project_access",
            "execution_mode",
            "idempotency",
            "required_capabilities",
            "arguments_schema",
            "result_schema",
        }
        for operation in operations.values()
    )


def test_project_create_persists_the_explicit_public_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    requested = ProjectProfile(
        width=1280,
        height=720,
        fps_numerator=12,
        fps_denominator=1,
        audio_sample_rate=48_000,
        audio_channels=1,
    )

    created = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "project.create",
            "arguments": {
                "name": "Explicit Profile",
                "directory_name": "explicit-profile",
                "profile": requested.model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        },
        application=EditorApplication(),
    )

    main_sequence = next(
        item
        for item in created["sequences"]
        if item["id"] == created["project"]["main_sequence_id"]
    )
    assert main_sequence["profile"] == requested.model_dump(
        mode="json", exclude_computed_fields=True
    )


def test_runtime_inspection_is_projectless_and_reports_every_runtime_capability() -> None:
    inspected = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "runtime.inspect",
        }
    )

    assert {item["id"] for item in inspected["capabilities"]} == set(
        RUNTIME_CAPABILITY_IDS
    )
    assert all(
        item["status"] in {"ready", "unavailable", "unverified"}
        for item in inspected["capabilities"]
    )


def test_fcpxml_export_runs_through_the_public_cli_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    application = EditorApplication()
    created = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "project.create",
            "arguments": {
                "name": "FCPXML CLI Project",
                "directory_name": "fcpxml-cli-project",
                "profile": ProjectProfile().model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        },
        application=application,
    )
    project_path = Path(created["path"])
    assert project_path == (tmp_path / "fcpxml-cli-project").resolve()
    source = tmp_path / "handoff-tone.wav"
    _write_wave(source)
    imported = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "asset.import",
            "project": str(project_path),
            "arguments": {"source": str(source)},
        },
        application=application,
    )
    sequence_id = created["project"]["main_sequence_id"]
    track = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "timeline.track.add",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id, "kind": "audio"},
        },
        application=application,
    )["track"]
    execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "timeline.clip.add",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "track_id": track["id"],
                "asset_id": imported["asset"]["id"],
                "timeline_start": 0,
                "source_in": 0,
                "duration": min(
                    25,
                    imported["asset"]["metadata"]["duration_frames"],
                ),
            },
        },
        application=application,
    )
    output = tmp_path / "handoff.fcpxml"

    exported = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "export.fcpxml",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "output_path": str(output),
            },
        },
        application=application,
    )

    assert exported["format"] == "fcpxml"
    assert Path(exported["output_path"]) == output
    assert exported["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    root = ET.parse(output).getroot()
    assert root.tag == "fcpxml"
    assert root.find(".//asset") is not None
    assert root.find(".//audio") is not None


def test_clip_source_and_visual_effects_run_through_public_cli_contract(
    tmp_path: Path,
) -> None:
    application = EditorApplication()
    project_path = tmp_path / "Public Clip Editing"
    project = application.create_project(project_path, "Public Clip Editing")
    try:
        project.populate_sample_project()
        sequence_id = project.get_project().main_sequence_id
        state = project.timeline(sequence_id).state
        clip_id = state.clips[0].id
        replacement_id = project.list_assets()[1].id
    finally:
        project.close()

    replaced = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "timeline.clip.source.replace",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "asset_id": replacement_id,
            },
        },
        application=application,
    )["clip"]
    assert replaced["asset_id"] == replacement_id

    added = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "timeline.clip.effect.add",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "kind": "gaussian_blur",
            },
        },
        application=application,
    )["clip"]
    effect_id = added["visual_effects"][0]["id"]
    updated = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "timeline.clip.effect.update",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "effect_id": effect_id,
                "enabled": True,
                "parameters": {"sigma": 7.5},
            },
        },
        application=application,
    )["clip"]
    assert updated["visual_effects"][0]["parameters"] == {"sigma": 7.5}

    removed = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "timeline.clip.effect.remove",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "effect_id": effect_id,
            },
        },
        application=application,
    )["clip"]
    assert removed["visual_effects"] == []

    described = execute_request(
        {"protocol": "mediaflow-cli", "version": 2, "operation": "describe"},
        application=application,
    )
    operation_names = {item["name"] for item in described["operations"]}
    assert {
        "timeline.clip.source.replace",
        "timeline.clip.effect.add",
        "timeline.clip.effect.update",
        "timeline.clip.effect.move",
        "timeline.clip.effect.remove",
    } <= operation_names


def test_cli_and_desktop_composition_api_share_real_persisted_task_chain(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    application = EditorApplication()

    created = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "project.create",
            "arguments": {
                "name": "Headless Project",
                "directory_name": "headless-project",
                "profile": ProjectProfile().model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        },
        application=application,
    )
    project_path = Path(created["path"])
    assert project_path == (tmp_path / "headless-project").resolve()
    assert created["project"]["name"] == "Headless Project"
    assert created["sequences"][0]["id"] == created["project"]["main_sequence_id"]

    source = tmp_path / "tone.wav"
    _write_wave(source)
    imported = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
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
            "version": 2,
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
            "version": 2,
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
                "version": 2,
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


def test_cli_request_id_replays_persisted_result_without_repeating_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    application = EditorApplication()
    create_request = {
        "protocol": "mediaflow-cli",
        "version": 2,
        "operation": "project.create",
        "request_id": "create-project-once",
        "arguments": {
            "name": "Idempotent CLI Project",
            "directory_name": "idempotent-cli-project",
            "profile": ProjectProfile().model_dump(
                mode="json", exclude_computed_fields=True
            ),
        },
    }
    first_project = execute_request(create_request, application=application)
    repeated_project = execute_request(create_request, application=application)
    assert repeated_project == first_project
    mismatched_create = {
        **create_request,
        "arguments": {
            **create_request["arguments"],
            "profile": ProjectProfile(fps_numerator=24).model_dump(
                mode="json", exclude_computed_fields=True
            ),
        },
    }
    with pytest.raises(ValueError, match="does not match"):
        execute_request(mismatched_create, application=application)
    project_path = Path(first_project["path"])
    assert project_path == (tmp_path / "idempotent-cli-project").resolve()

    sequence_id = first_project["project"]["main_sequence_id"]
    add_track_request = {
        "protocol": "mediaflow-cli",
        "version": 2,
        "operation": "timeline.track.add",
        "project": str(project_path),
        "request_id": "add-track-once",
        "arguments": {
            "sequence_id": sequence_id,
            "kind": "video",
            "name": "Only once",
        },
    }
    first = execute_request(add_track_request, application=application)
    repeated = execute_request(add_track_request, application=application)
    assert repeated == first

    visible = execute_request(
        {
            "protocol": "mediaflow-cli",
            "version": 2,
            "operation": "timeline.get",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        },
        application=application,
    )
    assert [track["id"] for track in visible["timeline"]["tracks"]] == [
        first["track"]["id"]
    ]

    conflicting = dict(add_track_request)
    conflicting["arguments"] = {
        **add_track_request["arguments"],
        "name": "Different input",
    }
    with pytest.raises(ValueError, match="request_id was reused"):
        execute_request(conflicting, application=application)


def test_ai_transcript_edit_plan_runs_through_real_cli_and_can_restore(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "AI Transcript Project"
    source = tmp_path / "interview.mp4"
    source.write_bytes(b"timeline-source")
    with ProjectRepository.create(project_path, "AI Transcript Project") as repository:
        project = repository.catalog.get_project()
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        locked_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=locked_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        editor.set_track_state(
            locked_track.id,
            enabled=True,
            locked=True,
            muted=False,
            solo=False,
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
            )
            for position, text in enumerate(("one", "two", "three"))
        ]
        repository.subtitles.create_subtitle_document(document, [segment], words)

    code, inspected = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.get",
    )
    assert code == 0 and inspected["ok"] is True
    transcript = inspected["result"]["transcript"]
    assert transcript["recognized_word_count"] == 3
    assert transcript["estimated_word_count"] == 0
    word_id = transcript["segments"][0]["words"][1]["id"]
    edit = {
        "sequence_id": transcript["document"]["sequence_id"],
        "document_id": transcript["document"]["id"],
        "expected_content_revision": transcript["content_revision"],
        "selections": [
            {
                "kind": "words",
                "ids": [word_id],
                "reason": "Remove repeated filler",
            }
        ],
    }

    code, previewed = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.preview",
        {"edit": edit},
    )
    assert code == 0 and previewed["ok"] is True
    plan = previewed["result"]["plan"]
    assert plan["impact"]["removed_duration_frames"] == 30
    assert plan["impact"]["before_duration_frames"] == 90
    assert plan["impact"]["after_duration_frames"] == 90
    assert plan["impact"]["locked_track_ids"] == [locked_track.id]
    assert plan["warnings"]
    assert plan["resolved_selections"][0]["text"] == "two"

    code, unacknowledged = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.apply",
        {"plan": plan},
    )
    assert code == 1
    assert unacknowledged["error"]["code"] == "invalid_request"

    code, applied = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.apply",
        {"plan": plan, "accept_warnings": True},
    )
    assert code == 0 and applied["ok"] is True
    result = applied["result"]["edit"]
    assert result["removed_word_count"] == 1
    assert result["after_duration_frames"] == 90
    recovery_version = result["recovery_version"]
    recovery_path = project_path / recovery_version["snapshot_path"]
    assert recovery_path.is_file()

    with ProjectRepository.open(project_path) as repository:
        timeline = repository.timeline.load_timeline(transcript["document"]["sequence_id"])
        clips_by_track = {
            track_id: sorted(
                (
                    clip.timeline_start,
                    clip.source_in,
                    clip.duration,
                )
                for clip in timeline.clips
                if clip.track_id == track_id
            )
            for track_id in (track.id, locked_track.id)
        }
        assert clips_by_track == {
            track.id: [(0, 0, 30), (30, 60, 30)],
            locked_track.id: [(0, 0, 90)],
        }
        assert repository.subtitles.list_subtitle_segments(transcript["document"]["id"])[0].text == (
            "one three"
        )
        edited_srt = next(
            (project_path / "generated" / "subtitles").rglob("*.srt")
        )
        assert "one three" in edited_srt.read_text(encoding="utf-8-sig")

    code, stale = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.apply",
        {"plan": plan, "accept_warnings": True},
    )
    assert code == 1
    assert stale["error"]["code"] == "conflict"

    code, restored = _run_cli_request(
        tmp_path,
        project_path,
        "project.version.restore",
        {"version_id": recovery_version["id"]},
    )
    assert code == 0 and restored["ok"] is True
    with ProjectRepository.open(project_path) as repository:
        timeline = repository.timeline.load_timeline(transcript["document"]["sequence_id"])
        assert {
            clip.track_id: (clip.timeline_start, clip.source_in, clip.duration)
            for clip in timeline.clips
        } == {
            track.id: (0, 0, 90),
            locked_track.id: (0, 0, 90),
        }
        assert repository.subtitles.list_subtitle_segments(transcript["document"]["id"])[0].text == (
            "one two three"
        )
        restored_srt = next(
            (project_path / "generated" / "subtitles").rglob("*.srt")
        )
        assert "one two three" in restored_srt.read_text(encoding="utf-8-sig")


def test_timeline_navigation_preserves_one_project_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    with application.create_project(tmp_path / "Project", "Project") as project:
        main = project.get_project().main_sequence_id
        main_editor = project.timeline(main)
        main_editor.add_marker(10, "Keep undo")
        short = project.create_short_sequence("Short")

        assert project.timeline(short.id) is project.timeline(short.id)
        assert project.timeline(main) is main_editor
        assert project.can_undo is True

        project.timeline(short.id)
        assert project.can_undo is True
        project.undo()
        assert project.load_timeline(main).markers == []


def test_public_batch_clip_add_is_atomic_and_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    source = tmp_path / "batch-tone.wav"
    _write_wave(source)
    project_path = tmp_path / "Batch Clip Project"
    with application.create_project(project_path, "Batch Clip Project") as project:
        asset = project.import_external_asset(source)
        sequence_id = project.get_project().main_sequence_id
        track = project.timeline(sequence_id).add_track(TrackKind.AUDIO)

    request = {
        "protocol": "mediaflow-cli",
        "version": 2,
        "operation": "timeline.clip.batch.add",
        "project": str(project_path),
        "request_id": "batch-clips-once",
        "arguments": {
            "sequence_id": sequence_id,
            "clips": [
                {
                    "track_id": track.id,
                    "asset_id": asset.id,
                    "timeline_start": 0,
                    "source_in": 0,
                    "duration": 12,
                },
                {
                    "track_id": track.id,
                    "asset_id": asset.id,
                    "timeline_start": 12,
                    "source_in": 12,
                    "duration": 12,
                },
            ],
        },
    }
    first = execute_request(request, application=application)
    repeated = execute_request(request, application=application)

    assert [clip["timeline_start"] for clip in first["clips"]] == [0, 12]
    assert repeated == first
    with application.open_project(project_path, writable=False) as project:
        assert len(project.load_timeline(sequence_id).clips) == 2


def test_preview_snapshots_are_content_addressed_and_never_overwrite_active_graph(
    tmp_path: Path,
    max_project_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    source = tmp_path / "preview-tone.wav"
    _write_wave(source)

    with application.create_project(max_project_path, "Preview Project") as project:
        asset = project.import_external_asset(source)
        editor = project.timeline(project.get_project().main_sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
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
        assert first.is_relative_to(tmp_path / "runtime")
        assert second.is_relative_to(tmp_path / "runtime")
        assert utf16_units(str(first)) <= 240
        assert utf16_units(str(second)) <= 240
        assert first.read_text(encoding="utf-8") == first_xml
        assert first.name.startswith("pv-") and second.name.startswith("pv-")
        assert not (
            project.project_dir / "cache" / "mlt" / f"{editor.state.sequence.id}-preview.mlt"
        ).exists()
        assert not list(
            (project.project_dir / "cache" / "mlt").glob("*-preview-*.mlt")
        )
        assert not list((project.project_dir / "cache" / "mlt").glob("*.partial"))


def test_short_sequence_archive_is_recoverable_through_project_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    application = EditorApplication()
    with application.create_project(tmp_path / "Project", "Project") as project:
        short = project.create_short_sequence("Recoverable")

        project.archive_short_sequence(short.id)
        assert short.id not in {item.id for item in project.list_sequences()}
        assert project.get_sequence(short.id).archived is True

        project.undo()
        assert short.id in {item.id for item in project.list_sequences()}
        assert project.get_sequence(short.id).archived is False

        project.redo()
        assert short.id not in {item.id for item in project.list_sequences()}
