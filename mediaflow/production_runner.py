from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mediaflow.automation.contracts import AUTOMATION_PROTOCOL, AUTOMATION_VERSION
from mediaflow.domain.production_bundle import ProductionBundle
from mediaflow.service.client import execute_sync

ExecuteOperation = Callable[..., dict[str, Any]]


def _required_audio_provenance(
    imported: dict[str, Any],
    timeline: dict[str, Any],
    required_source_ids: list[str],
    audible_clip_ids: list[str],
) -> list[dict[str, Any]]:
    source_assets = imported.get("source_assets")
    if not isinstance(source_assets, dict):
        raise RuntimeError("Portable timeline import returned no source asset mapping")
    tracks_value = timeline.get("tracks")
    clips_value = timeline.get("clips")
    if not isinstance(tracks_value, list) or not isinstance(clips_value, list):
        raise RuntimeError("Imported project returned no inspectable native timeline")
    tracks = {
        str(track["id"]): track
        for track in tracks_value
        if isinstance(track, dict) and track.get("id") is not None
    }
    inspections: list[dict[str, Any]] = []
    for source_id in required_source_ids:
        asset_value = source_assets.get(source_id)
        if not isinstance(asset_value, dict) or not asset_value.get("id"):
            raise RuntimeError(f"Required portable audio source has no imported asset mapping: {source_id}")
        asset_id = str(asset_value["id"])
        if asset_value.get("origin") != "external":
            raise RuntimeError(
                f"Required portable audio source was not imported as original external media: {source_id}"
            )
        active_clips: list[dict[str, str]] = []
        for clip in clips_value:
            if not isinstance(clip, dict) or str(clip.get("asset_id")) != asset_id:
                continue
            track = tracks.get(str(clip.get("track_id")))
            if (
                track is None
                or str(clip.get("id")) not in audible_clip_ids
                or track.get("enabled", True) is not True
                or (track.get("muted", False) is True)
                or (clip.get("media_kind") not in {"audio_only", "linked_av"})
            ):
                continue
            active_clips.append(
                {
                    "clip_id": str(clip.get("id")),
                    "track_id": str(track["id"]),
                    "media_kind": str(clip["media_kind"]),
                }
            )
        if not active_clips:
            raise RuntimeError(
                f"Required original audio source is not active after native import: {source_id}"
            )
        inspections.append(
            {
                "portable_source_id": source_id,
                "imported_asset_id": asset_id,
                "asset_origin": "external",
                "active_clips": active_clips,
            }
        )
    return inspections


def _require_project_profile(snapshot: dict[str, Any], sequence_id: str, expected: dict[str, Any]) -> None:
    sequences = snapshot.get("sequences")
    if not isinstance(sequences, list):
        raise RuntimeError("Existing production project returned no sequence list")
    sequence = next(
        (item for item in sequences if isinstance(item, dict) and str(item.get("id")) == sequence_id), None
    )
    if sequence is None or not isinstance(sequence.get("profile"), dict):
        raise RuntimeError("Existing production project has no inspectable main sequence")
    actual = sequence["profile"]
    keys = (
        "width",
        "height",
        "fps_numerator",
        "fps_denominator",
        "color_mode",
        "bit_depth",
        "audio_sample_rate",
        "audio_channels",
    )
    mismatches = [key for key in keys if actual.get(key) != expected.get(key)]
    if mismatches:
        raise RuntimeError(
            "Existing production project profile does not match the bundle: " + ", ".join(mismatches)
        )


def _operation_request(
    bundle: ProductionBundle,
    workflow_id: str,
    operation: str,
    arguments: dict[str, Any],
    *,
    project: str | None = None,
    base_revision: int | None = None,
    write_suffix: str | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "protocol": AUTOMATION_PROTOCOL,
        "version": AUTOMATION_VERSION,
        "operation": operation,
        "arguments": arguments,
        "actor": bundle.actor.model_dump(mode="json"),
        "client_id": bundle.client_id,
    }
    if project is not None:
        request["project"] = project
    if base_revision is not None:
        request["base_revision"] = base_revision
    if write_suffix is not None:
        request["request_id"] = f"{workflow_id}:{write_suffix}"
    return request


def _workflow_id(bundle: ProductionBundle) -> str:
    serialized = json.dumps(
        bundle.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{bundle.request_id}:{hashlib.sha256(serialized).hexdigest()[:16]}"


def _persisted_event_result(
    events: list[dict[str, Any]], *, request_id: str, operation: str
) -> dict[str, Any] | None:
    for event in events:
        if event.get("request_id") != request_id or event.get("operation") != operation:
            continue
        result = event.get("operation_result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Persisted {operation} event has no result object")
        return result
    return None


def _persisted_timeline_import(
    events: list[dict[str, Any]], timeline_sha256: str, sequence_id: str
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("operation") != "timeline.portable.import":
            continue
        result = event.get("operation_result")
        if not isinstance(result, dict) or result.get("timeline_sha256") != timeline_sha256:
            continue
        timeline = result.get("timeline")
        sequence = timeline.get("sequence") if isinstance(timeline, dict) else None
        if isinstance(sequence, dict) and str(sequence.get("id")) == sequence_id:
            return result
    return None


def _execute_result(
    execute: ExecuteOperation, request: dict[str, Any], *, timeout_seconds: float | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = (
        execute(request, timeout_seconds=timeout_seconds) if timeout_seconds is not None else execute(request)
    )
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Editor Service operation {request['operation']} returned no result object")
    return (response, result)


@dataclass(slots=True)
class _ProductionClient:
    bundle: ProductionBundle
    execute: ExecuteOperation
    workflow_id: str = field(init=False)
    operation_count: int = 0

    def __post_init__(self) -> None:
        self.workflow_id = _workflow_id(self.bundle)

    def call(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        project: str | None = None,
        base_revision: int | None = None,
        write_suffix: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = _execute_result(
            self.execute,
            _operation_request(
                self.bundle,
                self.workflow_id,
                operation,
                arguments,
                project=project,
                base_revision=base_revision,
                write_suffix=write_suffix,
            ),
            timeout_seconds=timeout_seconds,
        )
        self.operation_count += 1
        return result


@dataclass(frozen=True, slots=True)
class _ProductionProject:
    path: str
    record: dict[str, Any]
    snapshot: dict[str, Any]
    created: bool
    revision: int
    events: list[dict[str, Any]]

    @property
    def sequence_id(self) -> str:
        return str(self.record["main_sequence_id"])


@dataclass(frozen=True, slots=True)
class _SpeechReview:
    review: dict[str, Any] | None
    inspection: dict[str, Any] | None
    revision: int


@dataclass(frozen=True, slots=True)
class _ProductionExport:
    task_id: str
    task: dict[str, Any]
    build_outcome: dict[str, Any] | None
    revision: int


@dataclass(frozen=True, slots=True)
class _ProductionVerification:
    handoff: dict[str, Any]
    latest_export: dict[str, Any]
    quality: dict[str, Any]
    required_audio_provenance: list[dict[str, Any]]


def _open_production_project(client: _ProductionClient, preflight: dict[str, Any]) -> _ProductionProject:
    project_created = client.bundle.project.existing_path is None
    if project_created:
        create_response, project_snapshot = client.call(
            "project.create",
            {
                "name": client.bundle.project.name,
                "directory_name": client.bundle.project.directory_name,
                "profile": preflight["project_profile"],
            },
            write_suffix="project-create",
        )
    else:
        create_response, project_snapshot = client.call(
            "project.inspect", {}, project=client.bundle.project.existing_path
        )
    project_path = str(project_snapshot["path"])
    project_record = dict(project_snapshot["project"])
    sequence_id = str(project_record["main_sequence_id"])
    revision = int(create_response["project_revision"])
    if not project_created:
        _require_project_profile(project_snapshot, sequence_id, preflight["project_profile"])
    events: list[dict[str, Any]] = []
    since_revision = 0
    while True:
        changes_response, changes = client.call(
            "project.changes.list", {"since_revision": since_revision}, project=project_path
        )
        revision = int(changes_response["project_revision"])
        events.extend(dict(event) for event in changes.get("events", []))
        if not changes.get("has_more"):
            break
        next_revision = changes.get("next_revision")
        if type(next_revision) is not int or next_revision <= since_revision:
            raise RuntimeError("Project history returned a non-advancing continuation revision")
        since_revision = next_revision
    return _ProductionProject(
        project_path, project_record, project_snapshot, project_created, revision, events
    )


def _import_production_timeline(
    client: _ProductionClient, project: _ProductionProject, preflight: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    project_path = project.path
    sequence_id = project.sequence_id
    events = project.events
    project_created = project.created
    revision = project.revision
    import_request_id = f"{client.workflow_id}:timeline-import"
    imported = (
        _persisted_timeline_import(events, preflight["timeline_sha256"], sequence_id)
        if not project_created
        else _persisted_event_result(
            events, request_id=import_request_id, operation="timeline.portable.import"
        )
    )
    if imported is not None:
        if imported.get("timeline_sha256") != preflight["timeline_sha256"]:
            raise RuntimeError("Persisted production timeline does not match this bundle")
    elif not project_created:
        raise RuntimeError(
            "Existing production project has no persisted portable timeline import for this bundle"
        )
    else:
        import_response, imported = client.call(
            "timeline.portable.import",
            {"sequence_id": sequence_id, "timeline_path": preflight["timeline_path"]},
            project=project_path,
            base_revision=revision,
            write_suffix="timeline-import",
        )
        revision = int(import_response["project_revision"])
    return (imported, revision)


def _transcribe_production_speech(
    client: _ProductionClient, project: _ProductionProject, revision: int
) -> int:
    project_path = project.path
    sequence_id = project.sequence_id
    events = project.events
    speech_policy = client.bundle.speech_review
    assert speech_policy is not None
    transcription_request_id = f"{client.workflow_id}:speech-transcribe"
    transcription_receipt = _persisted_event_result(
        events, request_id=transcription_request_id, operation="transcript.sequence.transcribe"
    )
    if transcription_receipt is None:
        transcription_arguments: dict[str, Any] = {
            "sequence_id": sequence_id,
            "dialogue_track_id": speech_policy.dialogue_track_id,
        }
        if speech_policy.asr is not None:
            transcription_arguments["asr"] = speech_policy.asr.model_dump(mode="json", exclude_none=True)
        transcription_response, transcription_receipt = client.call(
            "transcript.sequence.transcribe",
            transcription_arguments,
            project=project_path,
            base_revision=revision,
            write_suffix="speech-transcribe",
        )
        revision = int(transcription_response["project_revision"])
    transcription_task = transcription_receipt.get("task")
    if not isinstance(transcription_task, dict):
        raise RuntimeError("Speech transcription returned no task")
    transcription_task_id = str(transcription_task["id"])
    transcription_wait_response, transcription_waited = client.call(
        "task.wait",
        {"task_id": transcription_task_id, "timeout": speech_policy.transcription_timeout},
        project=project_path,
        timeout_seconds=speech_policy.transcription_timeout + 15,
    )
    revision = int(transcription_wait_response["project_revision"])
    transcription_task = dict(transcription_waited["task"])
    if transcription_task.get("status") != "completed":
        detail = (
            transcription_task.get("error") or f"task ended with status {transcription_task.get('status')}"
        )
        raise RuntimeError(f"Speech transcription failed: {detail}")
    return revision


def _review_production_speech(
    client: _ProductionClient, project: _ProductionProject, revision: int
) -> _SpeechReview:
    project_path = project.path
    sequence_id = project.sequence_id
    speech_policy = client.bundle.speech_review
    if speech_policy is None or not speech_policy.auto_transcribe:
        return _SpeechReview(None, None, revision)
    revision = _transcribe_production_speech(client, project, revision)
    _speech_response, speech_inspection = client.call(
        "speech.review.inspect",
        {
            "sequence_id": sequence_id,
            "rules": speech_policy.model_dump(
                mode="json",
                exclude={"segments", "auto_transcribe", "dialogue_track_id", "asr", "transcription_timeout"},
            ),
        },
        project=project_path,
    )
    automatic_speech_review = dict(speech_inspection["review"])
    return _SpeechReview(automatic_speech_review, speech_inspection, revision)


def _speech_review_required(
    client: _ProductionClient, project: _ProductionProject, preflight: dict[str, Any], speech: _SpeechReview
) -> dict[str, Any]:
    project_path = project.path
    sequence_id = project.sequence_id
    project_created = project.created
    project_record = project.record
    revision = speech.revision
    automatic_speech_review = speech.review
    speech_inspection = speech.inspection
    return {
        "status": "review_required",
        "request_id": client.bundle.request_id,
        "project_created": project_created,
        "project_reused": not project_created,
        "render_started": False,
        "internal_operation_count": client.operation_count,
        "project": {
            "id": project_record["id"],
            "path": project_path,
            "revision": revision,
            "sequence_id": sequence_id,
        },
        "preflight": preflight,
        "speech_review": automatic_speech_review,
        "speech_review_inspection": speech_inspection,
    }


def _create_production_version(
    client: _ProductionClient, project: _ProductionProject, revision: int
) -> tuple[dict[str, Any], int]:
    project_path = project.path
    events = project.events
    version_request_id = f"{client.workflow_id}:version-create"
    version_result = _persisted_event_result(
        events, request_id=version_request_id, operation="project.version.create"
    )
    if version_result is None:
        version_response, version_result = client.call(
            "project.version.create",
            {"name": client.bundle.version_name},
            project=project_path,
            base_revision=revision,
            write_suffix="version-create",
        )
        revision = int(version_response["project_revision"])
    version = dict(version_result["version"])
    return (version, revision)


def _submit_production_export(
    client: _ProductionClient, project: _ProductionProject, revision: int
) -> tuple[str, int]:
    project_path = project.path
    sequence_id = project.sequence_id
    project_snapshot = project.snapshot
    export_arguments: dict[str, Any] = {
        "sequence_id": sequence_id,
        **client.bundle.output.model_dump(mode="json", exclude_none=True, exclude={"build_units"}),
    }
    export_operation = "export.sequence"
    if client.bundle.output.build_units:
        export_operation = "export.sequence.build"
        export_arguments["units"] = [
            unit.model_dump(mode="json") for unit in client.bundle.output.build_units
        ]
    export_request_id = f"{client.workflow_id}:sequence-export"
    task_idempotency = f"automation:{export_request_id}:{export_operation}"
    existing_task = next(
        (
            dict(task)
            for task in project_snapshot.get("tasks", [])
            if task.get("idempotency_key") == task_idempotency
        ),
        None,
    )
    if existing_task is None:
        export_response, export_receipt = client.call(
            export_operation,
            export_arguments,
            project=project_path,
            base_revision=revision,
            write_suffix="sequence-export",
        )
        revision = int(export_response["project_revision"])
    else:
        export_receipt = {"task": existing_task}
    task_id = str(export_receipt["task"]["id"])
    return (task_id, revision)


def _export_production(
    client: _ProductionClient, project: _ProductionProject, revision: int
) -> _ProductionExport:
    project_path = project.path
    task_id, revision = _submit_production_export(client, project, revision)
    wait_response, waited = client.call(
        "task.wait",
        {"task_id": task_id, "timeout": client.bundle.output.timeout},
        project=project_path,
        timeout_seconds=client.bundle.output.timeout + 15,
    )
    revision = int(wait_response["project_revision"])
    task = dict(waited["task"])
    if task.get("status") != "completed":
        detail = task.get("error") or f"task ended with status {task.get('status')}"
        raise RuntimeError(f"Production export failed: {detail}")
    build_outcome: dict[str, Any] | None = None
    if client.bundle.output.build_units:
        outcome = task.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("outcome_type") != "sequence_build":
            raise RuntimeError("Segmented production export returned no build outcome")
        build_outcome = {
            "units": outcome.get("units", []),
            "audio": outcome.get("audio"),
            "assembly_status": outcome.get("assembly_status"),
            "assembly_key": outcome.get("assembly_key"),
            "report": outcome.get("report"),
        }
    return _ProductionExport(task_id, task, build_outcome, revision)


def _verify_production(
    client: _ProductionClient, project: _ProductionProject, version: dict[str, Any], imported: dict[str, Any]
) -> _ProductionVerification:
    project_path = project.path
    sequence_id = project.sequence_id
    _timeline_response, current_timeline_result = client.call(
        "timeline.get", {"sequence_id": sequence_id}, project=project_path
    )
    current_timeline = current_timeline_result.get("timeline")
    if not isinstance(current_timeline, dict):
        raise RuntimeError("Production project returned no native timeline for final verification")
    _audio_response, audio = client.call("audio.inspect", {"sequence_id": sequence_id}, project=project_path)
    audible_clip_ids = audio.get("audible_clip_ids")
    if not isinstance(audible_clip_ids, list):
        raise RuntimeError("Production project returned no audible audio inspection")
    required_audio_provenance = _required_audio_provenance(
        imported, current_timeline, client.bundle.timeline.required_audio_source_ids, audible_clip_ids
    )
    _handoff_response, handoff = client.call(
        "project.handoff.inspect",
        {"version_id": version["id"], "sequence_id": sequence_id},
        project=project_path,
    )
    latest_export = handoff.get("latest_export")
    if not isinstance(latest_export, dict):
        raise RuntimeError("Production export completed without an export history record")
    quality = latest_export.get("quality")
    if not isinstance(quality, dict) or quality.get("passed") is not True:
        raise RuntimeError("Production export did not pass MediaFlow quality analysis")
    if handoff.get("ready_for_handoff") is not True:
        raise RuntimeError("Production export did not pass project handoff verification")
    if handoff.get("export_matches_current_revision") is not True:
        raise RuntimeError("Production export no longer matches the verified native timeline")
    return _ProductionVerification(handoff, latest_export, quality, required_audio_provenance)


def _completed_production_result(
    client: _ProductionClient,
    project: _ProductionProject,
    preflight: dict[str, Any],
    imported: dict[str, Any],
    speech: _SpeechReview,
    version: dict[str, Any],
    exported: _ProductionExport,
    verified: _ProductionVerification,
) -> dict[str, Any]:
    project_path = project.path
    sequence_id = project.sequence_id
    project_created = project.created
    project_record = project.record
    revision = exported.revision
    task_id = exported.task_id
    task = exported.task
    build_outcome = exported.build_outcome
    automatic_speech_review = speech.review
    handoff = verified.handoff
    latest_export = verified.latest_export
    quality = verified.quality
    required_audio_provenance = verified.required_audio_provenance
    return {
        "status": "completed",
        "request_id": client.bundle.request_id,
        "project_created": project_created,
        "project_reused": not project_created,
        "render_started": True,
        "internal_operation_count": client.operation_count,
        "project": {
            "id": project_record["id"],
            "path": project_path,
            "revision": revision,
            "sequence_id": sequence_id,
            "version_id": version["id"],
        },
        "timeline": {
            "path": preflight["timeline_path"],
            "sha256": preflight["timeline_sha256"],
            "duration_seconds": preflight["duration_seconds"],
            "source_count": preflight["source_count"],
            "track_count": preflight["track_count"],
            "clip_count": preflight["clip_count"],
            "cover": preflight["cover"],
            "subtitles": preflight["subtitles"],
            "required_audio_sources": preflight["required_audio_sources"],
            "required_audio_provenance": required_audio_provenance,
            "imported_subtitle_document_ids": imported["subtitle_document_ids"],
        },
        "speech_review": automatic_speech_review or preflight["speech_review"],
        "speech_review_preflight": preflight["speech_review"],
        "output": {
            "path": latest_export["output_path"],
            "task_id": task_id,
            "status": task["status"],
            "format": latest_export["format"],
            "sha256": quality["sha256"],
            "quality_passed": quality["passed"],
            "checks": quality["checks"],
            "proof_frames": quality["proof_frames"],
            "strategy": "segmented" if client.bundle.output.build_units else "single-pass",
            "build": build_outcome,
        },
        "verification": {
            "ready_for_handoff": handoff["ready_for_handoff"],
            "offline_asset_ids": handoff["offline_asset_ids"],
            "export_matches_current_revision": handoff["export_matches_current_revision"],
        },
    }


def run_production_bundle(
    value: dict[str, Any] | ProductionBundle, *, execute: ExecuteOperation = execute_sync
) -> dict[str, Any]:
    bundle = value if isinstance(value, ProductionBundle) else ProductionBundle.model_validate(value)
    client = _ProductionClient(bundle, execute)
    _preflight_response, preflight = client.call(
        "production.bundle.inspect", {"bundle": client.bundle.model_dump(mode="json", exclude_none=True)}
    )
    if preflight.get("status") == "review_required":
        return {
            "status": "review_required",
            "request_id": client.bundle.request_id,
            "project_created": False,
            "render_started": False,
            "internal_operation_count": client.operation_count,
            "preflight": preflight,
        }
    project = _open_production_project(client, preflight)
    imported, revision = _import_production_timeline(client, project, preflight)
    speech = _review_production_speech(client, project, revision)
    if speech.review is not None and speech.review.get("blocks_production") is True:
        return _speech_review_required(client, project, preflight, speech)
    version, revision = _create_production_version(client, project, speech.revision)
    exported = _export_production(client, project, revision)
    verified = _verify_production(client, project, version, imported)
    return _completed_production_result(
        client, project, preflight, imported, speech, version, exported, verified
    )
