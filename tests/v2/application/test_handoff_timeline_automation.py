from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mediaflow.composition import EditorApplication
from mediaflow.domain.enums import AssetKind, TrackKind
from tests.v2.editor_service_api import EditorServiceApi


def _human_request(
    api: EditorServiceApi,
    operation: str,
    project: Path,
    arguments: dict,
) -> dict:
    request = api.request(operation, project=project, arguments=arguments)
    request["actor"] = {
        "kind": "human",
        "id": "desktop-user",
        "name": "Desktop user",
    }
    return api.execute_request(request)["result"]


def test_public_transitions_markers_and_async_handoff_use_one_project_journal(
    tmp_path: Path,
    editor_service_api: EditorServiceApi,
) -> None:
    project_path = tmp_path / "Async handoff project"
    application = EditorApplication()
    with application.create_project(project_path, "Async handoff project") as project:
        project.populate_sample_project()
        sequence_id = project.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        state = editor.state
        track = next(item for item in state.tracks if item.kind == TrackKind.VIDEO)
        asset = next(item for item in project.list_assets() if item.kind == AssetKind.IMAGE)
        start = state.duration_frames + 12
        left = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=start,
            source_in=0,
            duration=24,
        )
        right = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=start + 24,
            source_in=0,
            duration=24,
        )

    editor_service_api.execute("project.inspect", project=project_path)
    version = editor_service_api.execute(
        "project.version.create",
        project=project_path,
        arguments={"name": "AI initial delivery"},
    )["version"]
    transition = editor_service_api.execute(
        "timeline.transition.add",
        project=project_path,
        arguments={
            "sequence_id": sequence_id,
            "left_clip_id": left.id,
            "right_clip_id": right.id,
            "kind": "dissolve",
            "duration": 6,
        },
    )["transition"]
    updated_transition = editor_service_api.execute(
        "timeline.transition.update",
        project=project_path,
        arguments={
            "sequence_id": sequence_id,
            "transition_id": transition["id"],
            "kind": "fade",
            "duration": 4,
            "parameters": {"curve": "ease-in-out"},
        },
    )["transition"]
    assert updated_transition["kind"] == "fade"
    assert updated_transition["parameters"] == {"curve": "ease-in-out"}

    marker = _human_request(
        editor_service_api,
        "timeline.marker.add",
        project_path,
        {
            "sequence_id": sequence_id,
            "frame": start + 24,
            "name": "用户确认的换装落点",
            "color": "#ff9f43",
        },
    )["marker"]
    changed_marker = _human_request(
        editor_service_api,
        "timeline.marker.update",
        project_path,
        {
            "sequence_id": sequence_id,
            "marker_id": marker["id"],
            "frame": start + 25,
            "name": "用户微调后的换装落点",
            "color": "#ff9f43",
        },
    )["marker"]
    assert changed_marker["frame"] == start + 25

    changes = editor_service_api.execute(
        "project.changes.list",
        project=project_path,
        arguments={
            "since_revision": version["content_revision"],
            "actor_kind": "human",
        },
    )
    assert [item["operation"] for item in changes["events"]] == [
        "timeline.marker.add",
        "timeline.marker.update",
    ]
    assert all(item["actor_kind"] == "human" for item in changes["summaries"])
    assert "用户微调" not in changes["summaries"][-1]["summary"]
    assert changes["summaries"][-1]["paths"]

    handoff = editor_service_api.execute(
        "project.handoff.inspect",
        project=project_path,
        arguments={"version_id": version["id"], "sequence_id": sequence_id},
    )
    assert handoff["anchor_version"]["id"] == version["id"]
    assert handoff["current_revision"] > version["content_revision"]
    assert [item["actor"]["kind"] for item in handoff["events"]][-2:] == [
        "human",
        "human",
    ]
    assert handoff["offline_asset_ids"] == []
    assert handoff["latest_export"] is None
    assert handoff["export_matches_current_revision"] is False
    assert handoff["ready_for_handoff"] is False

    removed_marker = editor_service_api.execute(
        "timeline.marker.remove",
        project=project_path,
        arguments={
            "sequence_id": sequence_id,
            "marker_id": marker["id"],
        },
    )
    removed_transition = editor_service_api.execute(
        "timeline.transition.remove",
        project=project_path,
        arguments={
            "sequence_id": sequence_id,
            "transition_id": transition["id"],
        },
    )
    assert removed_marker == {"removed": True}
    assert removed_transition == {"removed": True}


def test_cli_batch_uses_the_same_atomic_editor_service_boundary(
    tmp_path: Path,
    editor_service_api: EditorServiceApi,
) -> None:
    project_path = tmp_path / "CLI batch project"
    application = EditorApplication()
    with application.create_project(project_path, "CLI batch project") as project:
        sequence_id = project.get_project().main_sequence_id
    editor_service_api.execute("project.inspect", project=project_path)
    base_revision = editor_service_api.revision(project_path)
    requests = [
        editor_service_api.request(
            "timeline.marker.add",
            project=project_path,
            base_revision=base_revision,
            arguments={
                "sequence_id": sequence_id,
                "frame": frame,
                "name": name,
                "color": "#4ea1ff",
            },
        )
        for frame, name in [(12, "跑图"), (36, "换装")]
    ]
    batch_file = tmp_path / "batch.json"
    batch_file.write_text(
        json.dumps(
            {
                "batch_id": "semantic-markers-batch",
                "label": "Add semantic markers",
                "requests": requests,
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediaflow.cli",
            "batch",
            "--request",
            str(batch_file),
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response["ok"] is True
    assert response["request_id"] == "semantic-markers-batch"
    assert len(response["result"]["results"]) == 2
    assert response["result"]["event"]["operation"] == "operation.execute_batch"

    changes = editor_service_api.execute(
        "project.changes.list",
        project=project_path,
        arguments={"since_revision": base_revision},
    )
    assert len(changes["events"]) == 1
    assert changes["events"][0]["undo_group_id"] == "semantic-markers-batch"
    assert changes["events"][0]["write_set"] == [f"/sequences/{sequence_id}/markers/new"]
