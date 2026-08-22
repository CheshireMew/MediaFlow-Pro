from __future__ import annotations

import json
from pathlib import Path

from mediaflow.composition import EditorApplication
from mediaflow.file_digest import sha256_file
from tests.v2.editor_service_api import EditorServiceApi

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "media-timeline-v1-project"


def _synced_visual_timeline() -> Path:
    origin = json.loads((FIXTURE / "fixture-origin.json").read_text(encoding="utf-8"))
    assert origin["protocol"] == "mediaflow-generated-test-fixture"
    assert origin["producer"] == "visual-multimedia/scripts/self-test-media-timeline.mjs"
    assert len(origin["producer_revision"]) == 40
    int(origin["producer_revision"], 16)
    assert origin["media_timeline_version"] == 1
    assert set(origin["files"]) | {"fixture-origin.json"} == {
        path.relative_to(FIXTURE).as_posix()
        for path in FIXTURE.rglob("*")
        if path.is_file()
    }
    assert all(
        sha256_file(FIXTURE / relative) == digest
        for relative, digest in origin["files"].items()
    )
    return FIXTURE / "media-timeline.json"


def test_visual_multimedia_timeline_becomes_a_native_exportable_mediaflow_project(
    tmp_path: Path,
    editor_service_api: EditorServiceApi,
) -> None:
    timeline_path = _synced_visual_timeline()
    project_path = tmp_path / "Portable native project"
    application = EditorApplication()
    with application.create_project(project_path, "Portable native project") as project:
        sequence_id = project.get_project().main_sequence_id

    inspected = editor_service_api.execute(
        "timeline.portable.inspect",
        project=project_path,
        arguments={"sequence_id": sequence_id, "timeline_path": str(timeline_path)},
    )
    assert inspected["mediaflow_compatible"] is True
    assert inspected["duration_seconds"] == 6
    assert inspected["source_count"] == 3

    imported = editor_service_api.execute(
        "timeline.portable.import",
        project=project_path,
        arguments={"sequence_id": sequence_id, "timeline_path": str(timeline_path)},
    )
    state = imported["timeline"]
    assert len(imported["source_assets"]) == 3
    assert len(imported["subtitle_document_ids"]) == 2
    assert len(state["markers"]) == 3
    assert any(clip["freeze_source_frame"] is not None for clip in state["clips"])
    subtitle_tracks = [track for track in state["tracks"] if track["kind"] == "subtitle"]
    assert [track["subtitle_style"]["font_size"] for track in subtitle_tracks] == [45, 27]
    assert [track["subtitle_style"]["position_y"] for track in subtitle_tracks] == [
        0.8611111111111112,
        0.9388888888888889,
    ]

    version = editor_service_api.execute(
        "project.version.create",
        project=project_path,
        arguments={"name": "AI portable timeline delivery"},
    )["version"]
    human_style = dict(subtitle_tracks[0]["subtitle_style"])
    human_style["font_size"] = 42
    human_request = editor_service_api.request(
        "subtitle.track.style.update",
        project=project_path,
        arguments={
            "sequence_id": sequence_id,
            "track_id": subtitle_tracks[0]["id"],
            "style": human_style,
        },
    )
    human_request["actor"] = {
        "kind": "human",
        "id": "desktop-user",
        "name": "Desktop user",
    }
    changed_style = editor_service_api.execute_request(human_request)["result"]["track"]
    assert changed_style["subtitle_style"]["font_size"] == 42
    human_changes = editor_service_api.execute(
        "project.changes.list",
        project=project_path,
        arguments={
            "since_revision": version["content_revision"],
            "actor_kind": "human",
        },
    )
    assert [event["operation"] for event in human_changes["events"]] == ["subtitle.track.style.update"]
    assert human_changes["summaries"][0]["summary"] == "Desktop user执行了调整字幕轨样式"
    output = tmp_path / "portable-native-export.mp4"
    receipt = editor_service_api.execute(
        "export.sequence",
        project=project_path,
        arguments={
            "sequence_id": sequence_id,
            "output_path": str(output),
            "overwrite": True,
        },
    )
    completed = editor_service_api.execute(
        "task.wait",
        project=project_path,
        arguments={"task_id": receipt["task"]["id"], "timeout": 90},
    )["task"]
    assert completed["status"] == "completed", completed
    assert output.is_file() and output.stat().st_size > 0

    handoff = editor_service_api.execute(
        "project.handoff.inspect",
        project=project_path,
        arguments={"version_id": version["id"], "sequence_id": sequence_id},
    )
    assert handoff["offline_asset_ids"] == []
    assert handoff["latest_export"]["output_path"] == str(output.resolve())
    assert handoff["export_matches_current_revision"] is True
    assert handoff["ready_for_handoff"] is True
    assert any(
        event["operation"] == "subtitle.track.style.update" and event["actor"]["kind"] == "human"
        for event in handoff["events"]
    )
