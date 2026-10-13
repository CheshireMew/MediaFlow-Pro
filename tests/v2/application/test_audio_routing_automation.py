from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mediaflow.application.project_mutation_planning import plan_automation_project_mutation
from mediaflow.automation.contracts import AutomationRequest
from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.composition import EditorProject
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.settings import ServiceSettings
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext


def invoke(project: EditorProject, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    definition = OPERATIONS[operation]
    envelope = AutomationRequest(
        operation=operation, project=str(project.project_dir),
        request_id=f"test-{operation}", actor=ActorIdentity(kind="agent", id="routing-test"),
        client_id="pytest", arguments=definition.validate_arguments(arguments),
    )
    return definition.validate_result(definition.handler(OperationContext(project, None, envelope)))


def test_audio_bus_add_and_track_routing_persist_without_changing_track_flags(tmp_path: Path) -> None:
    project_dir = tmp_path / "Routing"
    project = EditorProject(ProjectRepository.create(project_dir, "Routing"),
                           settings=ServiceSettings(), paths=RuntimeContext.discover().paths)
    try:
        sequence_id = project.get_project().main_sequence_id
        master = next(bus for bus in project.list_audio_buses(sequence_id) if bus.parent_bus_id is None)
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.AUDIO, "Voice")
        editor.set_track_state(track.id, enabled=True, locked=False, muted=True, solo=True)
        added = invoke(project, "audio.bus.add", {
            "sequence_id": sequence_id, "name": "Voice", "parent_bus_id": master.id,
        })["bus"]
        routed = invoke(project, "timeline.track.audio.route", {
            "sequence_id": sequence_id, "track_id": track.id, "bus_id": added["id"],
        })["track"]
        assert routed["muted"] and routed["solo"]
        assert routed["audio_bus_id"] == added["id"]
    finally:
        project.close()
    with ProjectRepository.open(project_dir) as reopened:
        state = reopened.timeline.load_timeline(sequence_id)
        assert next(item for item in state.tracks if item.id == track.id).audio_bus_id == added["id"]
        bus = next(bus for bus in reopened.audio.list_audio_buses(sequence_id) if bus.id == added["id"])
        assert bus.parent_bus_id == master.id


def test_audio_routing_rejects_unknown_bus_before_changing_timeline(tmp_path: Path) -> None:
    project = EditorProject(ProjectRepository.create(tmp_path / "Invalid Routing", "Invalid Routing"),
                           settings=ServiceSettings(), paths=RuntimeContext.discover().paths)
    try:
        sequence_id = project.get_project().main_sequence_id
        track = project.timeline(sequence_id).add_track(TrackKind.AUDIO, "Voice")
        before = project.timeline(sequence_id).state.model_dump()
        before_buses = project.list_audio_buses(sequence_id)
        with pytest.raises(ValueError, match="does not belong"):
            invoke(project, "timeline.track.audio.route", {
                "sequence_id": sequence_id, "track_id": track.id, "bus_id": "missing",
            })
        assert project.timeline(sequence_id).state.model_dump() == before
        with pytest.raises(ValueError, match="does not belong"):
            invoke(project, "audio.bus.add", {
                "sequence_id": sequence_id, "name": "Voice", "parent_bus_id": "missing",
            })
        assert project.list_audio_buses(sequence_id) == before_buses
    finally:
        project.close()
    plan = plan_automation_project_mutation("audio.bus.add", {}, default_sequence_id="main")
    assert plan.change_scopes == ["/audio/buses"]
