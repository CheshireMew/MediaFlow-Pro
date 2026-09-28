from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from mediaflow.application.production_bundle_inspection import (
    ProductionBundleInspectionService,
)
from mediaflow.automation.contracts import describe_contract
from mediaflow.domain.production_bundle import ProductionBundle, SpeechSimilarityPolicy
from mediaflow.infrastructure.portable_timeline_loader import load_portable_timeline
from mediaflow.production_runner import _required_audio_provenance, run_production_bundle

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "media-timeline-v1-project"


def _bundle(tmp_path: Path, timeline_path: Path) -> dict[str, Any]:
    return {
        "protocol": "mediaflow-production-bundle",
        "version": 1,
        "request_id": "production-test",
        "actor": {"kind": "agent", "id": "pytest-agent"},
        "client_id": "pytest-production",
        "project": {
            "name": "One request production",
            "directory_name": "one-request-production",
        },
        "timeline": {
            "path": str(timeline_path),
            "subtitle_track_ids": ["zh-captions"],
            "required_audio_source_ids": ["music"],
            "cover": {"source_id": "still", "max_duration_frames": 1},
        },
        "output": {
            "output_path": str((tmp_path / "production.mp4").resolve()),
            "overwrite": True,
            "timeout": 90,
        },
        "speech_review": {
            "segments": [
                {
                    "id": "first-take",
                    "start_seconds": 0.5,
                    "end_seconds": 2.0,
                    "text": "这个功能我们应该这样处理",
                },
                {
                    "id": "second-take",
                    "start_seconds": 2.2,
                    "end_seconds": 4.0,
                    "text": "这个功能我们应该这样处理才更连贯",
                },
                {
                    "id": "next-topic",
                    "start_seconds": 8.0,
                    "end_seconds": 9.0,
                    "text": "接下来进入另一个主题",
                },
            ]
        },
    }


def _timeline_with_one_frame_cover(tmp_path: Path) -> Path:
    root = tmp_path / "portable-production"
    shutil.copytree(FIXTURE, root)
    timeline_path = root / "media-timeline.json"
    document = json.loads(timeline_path.read_text(encoding="utf-8"))
    frame_duration = 1 / document["profile"]["frame_rate"]
    video_track = next(track for track in document["tracks"] if track["id"] == "main-video")
    for clip in video_track["clips"]:
        clip["timeline_start_seconds"] += frame_duration
    video_track["clips"].insert(
        0,
        {
            "id": "preview-cover",
            "type": "media",
            "source_id": "still",
            "timeline_start_seconds": 0,
            "source_in_seconds": 0,
            "duration_seconds": frame_duration,
            "speed": 1,
            "placement": {
                "fit": "cover",
                "x": 0,
                "y": 0,
                "width": document["profile"]["width"],
                "height": document["profile"]["height"],
            },
            "opacity": 1,
            "audio_enabled": False,
        },
    )
    document["profile"]["duration_seconds"] += frame_duration
    timeline_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return timeline_path


def test_bundle_preflight_validates_cover_subtitles_and_flags_probable_restart(
    tmp_path: Path,
) -> None:
    timeline_path = _timeline_with_one_frame_cover(tmp_path)
    bundle = ProductionBundle.model_validate(_bundle(tmp_path, timeline_path))

    inspected = ProductionBundleInspectionService(load_portable_timeline).inspect(bundle)

    assert inspected.status == "review_required"
    assert inspected.cover.starts_at_frame_zero is True
    assert inspected.cover.duration_frames == 1
    assert [item.track_id for item in inspected.subtitles] == ["zh-captions"]
    assert [item.source_id for item in inspected.required_audio_sources] == ["music"]
    assert inspected.required_audio_sources[0].active_duration_seconds == 6
    assert inspected.speech_review.candidate_count == 1
    candidate = inspected.speech_review.candidates[0]
    assert candidate.kind == "restart_extension"
    assert candidate.first_segment_id == "first-take"
    assert candidate.second_segment_id == "second-take"
    assert candidate.manual_review_required is True
    assert inspected.speech_review.automatic_deletions == 0


def test_similarity_review_ignores_short_fillers_and_unrelated_nearby_sentences() -> None:
    policy = SpeechSimilarityPolicy.model_validate(
        {
            "segments": [
                {"id": "filler", "start_seconds": 0, "end_seconds": 0.2, "text": "嗯"},
                {
                    "id": "topic-a",
                    "start_seconds": 0.3,
                    "end_seconds": 1.2,
                    "text": "今天先看字幕",
                },
                {
                    "id": "topic-b",
                    "start_seconds": 1.5,
                    "end_seconds": 2.4,
                    "text": "然后检查画面",
                },
            ]
        }
    )
    from mediaflow.domain.production_bundle import review_similar_speech

    review = review_similar_speech(policy)

    assert review.candidates == []
    assert review.blocks_production is False
    assert review.automatic_deletions == 0


def test_bundle_preflight_rejects_a_required_original_source_with_audio_disabled(
    tmp_path: Path,
) -> None:
    timeline_path = _timeline_with_one_frame_cover(tmp_path)
    value = _bundle(tmp_path, timeline_path)
    value["timeline"]["required_audio_source_ids"] = ["moving"]
    bundle = ProductionBundle.model_validate(value)

    with pytest.raises(
        ValueError,
        match="Required audio source is not active on the production timeline: moving",
    ):
        ProductionBundleInspectionService(load_portable_timeline).inspect(bundle)


def test_bundle_preflight_rejects_segmented_units_that_do_not_cover_the_timeline(
    tmp_path: Path,
) -> None:
    timeline_path = _timeline_with_one_frame_cover(tmp_path)
    value = _bundle(tmp_path, timeline_path)
    value["speech_review"]["block_on_candidates"] = False
    value["output"]["build_units"] = [
        {"id": "incomplete", "start_frame": 0, "end_frame": 10}
    ]
    bundle = ProductionBundle.model_validate(value)

    with pytest.raises(ValueError, match="must cover the complete production timeline"):
        ProductionBundleInspectionService(load_portable_timeline).inspect(bundle)


def test_production_project_requires_creation_fields_or_an_absolute_existing_path(
    tmp_path: Path,
) -> None:
    timeline_path = tmp_path / "timeline.json"
    missing_creation_fields = _bundle(tmp_path, timeline_path)
    missing_creation_fields["project"] = {"name": "Missing directory"}

    with pytest.raises(ValueError, match="name and project.directory_name are required"):
        ProductionBundle.model_validate(missing_creation_fields)

    relative_existing = _bundle(tmp_path, timeline_path)
    relative_existing["project"] = {"existing_path": "relative-project"}

    with pytest.raises(ValueError, match="project.existing_path must be absolute"):
        ProductionBundle.model_validate(relative_existing)

    reusable = _bundle(tmp_path, timeline_path)
    reusable["project"] = {"existing_path": str((tmp_path / "existing").resolve())}

    assert ProductionBundle.model_validate(reusable).project.existing_path == str(
        (tmp_path / "existing").resolve()
    )

    ambiguous = _bundle(tmp_path, timeline_path)
    ambiguous["project"]["existing_path"] = str((tmp_path / "existing").resolve())

    with pytest.raises(ValueError, match="cannot be combined"):
        ProductionBundle.model_validate(ambiguous)


@pytest.mark.parametrize(
    ("origin", "muted", "message"),
    [
        ("generated", False, "not imported as original external media"),
        ("external", True, "not active after native import"),
    ],
)
def test_required_audio_provenance_rejects_generated_or_inactive_audio(
    origin: str,
    muted: bool,
    message: str,
) -> None:
    imported = {
        "source_assets": {
            "original-voice": {"id": "asset-original-voice", "origin": origin}
        }
    }
    timeline = {
        "tracks": [
            {"id": "dialogue", "kind": "audio", "enabled": True, "muted": muted}
        ],
        "clips": [
            {
                "id": "dialogue-clip",
                "track_id": "dialogue",
                "asset_id": "asset-original-voice",
                "media_kind": "audio_only",
            }
        ],
    }

    with pytest.raises(RuntimeError, match=message):
        _required_audio_provenance(imported, timeline, ["original-voice"])


def test_production_runner_stops_before_project_creation_when_review_is_required(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "timeline.json")
    calls: list[str] = []

    def execute(request: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(str(request["operation"]))
        return {
            "result": {
                "status": "review_required",
                "speech_review": {"candidate_count": 1},
            },
            "project_revision": None,
        }

    result = run_production_bundle(bundle, execute=execute)

    assert result["status"] == "review_required"
    assert result["project_created"] is False
    assert result["render_started"] is False
    assert calls == ["production.bundle.inspect"]


@pytest.mark.parametrize("segmented", [False, True])
@pytest.mark.parametrize("reuse_project", [False, True])
def test_production_runner_executes_the_whole_delivery_chain_from_one_bundle(
    tmp_path: Path,
    segmented: bool,
    reuse_project: bool,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "timeline.json")
    bundle["speech_review"]["block_on_candidates"] = False
    project_path = (tmp_path / "project").resolve()
    if reuse_project:
        bundle["project"] = {"existing_path": str(project_path)}
    if segmented:
        bundle["output"]["build_units"] = [
            {"id": "whole-program", "start_frame": 0, "end_frame": 144}
        ]
    export_operation = "export.sequence.build" if segmented else "export.sequence"
    operations: list[str] = []
    timeout_values: list[float | None] = []
    imported_result = {
        "timeline_sha256": "a" * 64,
        "source_assets": {
            "music": {
                "id": "asset-music",
                "origin": "external",
            }
        },
        "timeline": {
            "sequence": {"id": "main"},
            "tracks": [
                {
                    "id": "music-track",
                    "kind": "audio",
                    "enabled": True,
                    "muted": False,
                }
            ],
            "clips": [
                {
                    "id": "music-clip",
                    "track_id": "music-track",
                    "asset_id": "asset-music",
                    "media_kind": "audio_only",
                }
            ],
        },
        "subtitle_document_ids": ["subtitle-document"],
    }
    persisted_events: list[dict[str, Any]] = (
        [
            {
                "operation": "timeline.portable.import",
                "request_id": "prior-production:timeline-import",
                "operation_result": imported_result,
            }
        ]
        if reuse_project
        else []
    )
    persisted_tasks: list[dict[str, Any]] = []
    revision = 1 if reuse_project else 0

    def execute(
        request: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        nonlocal revision
        operation = str(request["operation"])
        operations.append(operation)
        timeout_values.append(timeout_seconds)
        if operation == "production.bundle.inspect":
            result = {
                "status": "ready",
                "timeline_path": str((tmp_path / "timeline.json").resolve()),
                "timeline_sha256": "a" * 64,
                "project_profile": {
                    "width": 640,
                    "height": 360,
                    "fps_numerator": 24,
                    "fps_denominator": 1,
                    "color_mode": "sdr_bt709",
                    "bit_depth": 8,
                    "audio_sample_rate": 48000,
                    "audio_channels": 2,
                },
                "duration_seconds": 6,
                "source_count": 3,
                "track_count": 4,
                "clip_count": 7,
                "cover": {"requested": True, "clip_id": "preview-cover"},
                "subtitles": [{"track_id": "zh-captions", "caption_count": 1}],
                "required_audio_sources": [
                    {
                        "source_id": "music",
                        "active_clip_count": 1,
                        "active_duration_seconds": 6,
                        "track_ids": ["music-track"],
                    }
                ],
                "speech_review": {
                    "candidate_count": 1,
                    "blocks_production": False,
                    "automatic_deletions": 0,
                },
            }
        elif operation == "project.create":
            result = {
                "path": str(project_path),
                "project": {"id": "project-id", "main_sequence_id": "main"},
                "tasks": [],
            }
        elif operation == "project.inspect":
            result = {
                "path": str(project_path),
                "project": {"id": "project-id", "main_sequence_id": "main"},
                "sequences": [
                    {
                        "id": "main",
                        "profile": {
                            "width": 640,
                            "height": 360,
                            "fps_numerator": 24,
                            "fps_denominator": 1,
                            "color_mode": "sdr_bt709",
                            "bit_depth": 8,
                            "audio_sample_rate": 48000,
                            "audio_channels": 2,
                        },
                    }
                ],
                "tasks": list(persisted_tasks),
            }
        elif operation == "project.changes.list":
            result = {"events": list(persisted_events), "current_revision": revision}
        elif operation == "timeline.portable.import":
            revision += 1
            result = imported_result
            persisted_events.append(
                {
                    "operation": operation,
                    "request_id": request["request_id"],
                    "operation_result": result,
                }
            )
        elif operation == "project.version.create":
            revision += 1
            result = {"version": {"id": "version-id"}}
            persisted_events.append(
                {
                    "operation": operation,
                    "request_id": request["request_id"],
                    "operation_result": result,
                }
            )
        elif operation in {"export.sequence", "export.sequence.build"}:
            assert operation == export_operation
            assert ("units" in request["arguments"]) is segmented
            task = next(
                (
                    item
                    for item in persisted_tasks
                    if item["idempotency_key"]
                    == f"automation:{request['request_id']}:{export_operation}"
                ),
                None,
            )
            if task is None:
                task = {
                    "id": "export-task",
                    "idempotency_key": f"automation:{request['request_id']}:{export_operation}",
                    "status": "completed",
                }
                persisted_tasks.append(task)
            result = {"task": task}
        elif operation == "task.wait":
            task: dict[str, Any] = {"id": "export-task", "status": "completed"}
            if segmented:
                cache_status = "reused" if reuse_project else "rendered"
                task["outcome"] = {
                    "outcome_type": "sequence_build",
                    "output": {
                        "output": {"scope": "external", "path": bundle["output"]["output_path"]}
                    },
                    "units": [
                        {
                            "id": "whole-program",
                            "start_frame": 0,
                            "end_frame": 144,
                            "status": cache_status,
                            "cache_key": "c" * 64,
                            "output": {"scope": "project", "path": "cache/unit.mp4"},
                            "sha256": "d" * 64,
                        }
                    ],
                    "audio": {
                        "status": cache_status,
                        "cache_key": "e" * 64,
                        "output": {"scope": "project", "path": "cache/audio.wav"},
                        "sha256": "f" * 64,
                    },
                    "assembly_status": "reused" if reuse_project else "assembled",
                    "assembly_key": "a" * 64,
                    "report": {"scope": "project", "path": "cache/report.json"},
                }
            result = {"task": task}
        elif operation == "timeline.get":
            result = {"timeline": imported_result["timeline"]}
        elif operation == "project.handoff.inspect":
            result = {
                "latest_export": {
                    "output_path": str((tmp_path / "production.mp4").resolve()),
                    "format": "mp4",
                    "quality": {
                        "passed": True,
                        "sha256": "b" * 64,
                        "checks": [],
                        "proof_frames": [str(tmp_path / "proof.png")],
                    },
                },
                "ready_for_handoff": True,
                "offline_asset_ids": [],
                "export_matches_current_revision": True,
            }
        else:
            raise AssertionError(operation)
        return {"result": result, "project_revision": revision}

    result = run_production_bundle(bundle, execute=execute)

    assert result["status"] == "completed"
    assert result["project_created"] is (not reuse_project)
    assert result["project_reused"] is reuse_project
    assert result["internal_operation_count"] == (8 if reuse_project else 9)
    expected_first_operations = [
        "production.bundle.inspect",
        "project.inspect" if reuse_project else "project.create",
        "project.changes.list",
    ]
    if not reuse_project:
        expected_first_operations.append("timeline.portable.import")
    expected_first_operations.extend(
        [
            "project.version.create",
            export_operation,
            "task.wait",
            "timeline.get",
            "project.handoff.inspect",
        ]
    )
    assert operations == expected_first_operations
    assert timeout_values[operations.index("task.wait")] == 105
    assert result["verification"]["ready_for_handoff"] is True
    assert result["speech_review"]["automatic_deletions"] == 0
    assert result["output"]["strategy"] == ("segmented" if segmented else "single-pass")
    if segmented:
        assert result["output"]["build"]["units"][0]["status"] == (
            "reused" if reuse_project else "rendered"
        )
        assert result["output"]["build"]["audio"]["status"] == (
            "reused" if reuse_project else "rendered"
        )
        assert result["output"]["build"]["assembly_status"] == (
            "reused" if reuse_project else "assembled"
        )
    else:
        assert result["output"]["build"] is None
    assert result["timeline"]["required_audio_provenance"] == [
        {
            "portable_source_id": "music",
            "imported_asset_id": "asset-music",
            "asset_origin": "external",
            "active_clips": [
                {
                    "clip_id": "music-clip",
                    "track_id": "music-track",
                    "media_kind": "audio_only",
                }
            ],
        }
    ]

    operations.clear()
    timeout_values.clear()
    repeated = run_production_bundle(bundle, execute=execute)

    assert repeated["status"] == "completed"
    expected_repeat_operations = [
        "production.bundle.inspect",
        "project.inspect" if reuse_project else "project.create",
        "project.changes.list",
    ]
    if not reuse_project:
        expected_repeat_operations.append(export_operation)
    expected_repeat_operations.extend(
        ["task.wait", "timeline.get", "project.handoff.inspect"]
    )
    assert repeated["internal_operation_count"] == len(expected_repeat_operations)
    assert operations == expected_repeat_operations
    assert len(persisted_tasks) == 1


def test_production_runner_transcribes_and_stops_before_render_for_automatic_candidates(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path, tmp_path / "timeline.json")
    bundle["speech_review"] = {
        "auto_transcribe": True,
        "dialogue_track_id": "main-video--linked-audio",
    }
    operations: list[str] = []
    persisted_events: list[dict[str, Any]] = []
    revision = 0

    def execute(request: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal revision
        operation = str(request["operation"])
        operations.append(operation)
        if operation == "production.bundle.inspect":
            result = {
                "status": "ready",
                "timeline_path": str((tmp_path / "timeline.json").resolve()),
                "timeline_sha256": "a" * 64,
                "project_profile": {
                    "width": 640,
                    "height": 360,
                    "fps_numerator": 24,
                    "fps_denominator": 1,
                    "color_mode": "sdr_bt709",
                    "bit_depth": 8,
                    "audio_sample_rate": 48000,
                    "audio_channels": 2,
                },
                "speech_review": {
                    "candidate_count": 0,
                    "blocks_production": False,
                    "automatic_deletions": 0,
                },
            }
        elif operation == "project.create":
            result = {
                "path": str((tmp_path / "project").resolve()),
                "project": {"id": "project-id", "main_sequence_id": "main"},
                "tasks": [],
            }
        elif operation == "project.changes.list":
            result = {"events": list(persisted_events), "current_revision": revision}
        elif operation == "timeline.portable.import":
            revision += 1
            result = {"timeline_sha256": "a" * 64, "subtitle_document_ids": []}
            persisted_events.append(
                {
                    "operation": operation,
                    "request_id": request["request_id"],
                    "operation_result": result,
                }
            )
        elif operation == "transcript.sequence.transcribe":
            assert request["arguments"]["dialogue_track_id"] == "main-video--linked-audio"
            result = {"task": {"id": "transcription-task", "status": "pending"}}
            persisted_events.append(
                {
                    "operation": operation,
                    "request_id": request["request_id"],
                    "operation_result": result,
                }
            )
        elif operation == "task.wait":
            revision += 1
            result = {"task": {"id": "transcription-task", "status": "completed"}}
        elif operation == "speech.review.inspect":
            result = {
                "sequence_id": "main",
                "document_id": "transcript",
                "content_revision": revision,
                "timeline_duration_seconds": 6,
                "recognized_word_count": 20,
                "estimated_word_count": 0,
                "review": {
                    "requested": True,
                    "threshold": 0.82,
                    "candidate_count": 2,
                    "similarity_candidate_count": 1,
                    "filler_candidate_count": 1,
                    "gap_candidate_count": 0,
                    "candidates": [],
                    "blocks_production": True,
                    "automatic_deletions": 0,
                },
            }
        else:
            raise AssertionError(operation)
        return {"result": result, "project_revision": revision}

    result = run_production_bundle(bundle, execute=execute)

    assert result["status"] == "review_required"
    assert result["project_created"] is True
    assert result["render_started"] is False
    assert result["speech_review"]["automatic_deletions"] == 0
    assert operations == [
        "production.bundle.inspect",
        "project.create",
        "project.changes.list",
        "timeline.portable.import",
        "transcript.sequence.transcribe",
        "task.wait",
        "speech.review.inspect",
    ]

    operations.clear()
    repeated = run_production_bundle(bundle, execute=execute)

    assert repeated["status"] == "review_required"
    assert repeated["project_created"] is True
    assert repeated["render_started"] is False
    assert operations == [
        "production.bundle.inspect",
        "project.create",
        "project.changes.list",
        "task.wait",
        "speech.review.inspect",
    ]


def test_production_bundle_contract_is_discoverable() -> None:
    described = describe_contract(
        {"view": "operation", "name": "production.bundle.inspect"}
    )

    operation = described["operation"]
    assert operation["project_access"] == "none"
    assert operation["required_capabilities"] == []
    bundle_schema = operation["arguments_schema"]["properties"]["bundle"]
    assert bundle_schema["properties"]["protocol"]["const"] == "mediaflow-production-bundle"
    assert "speech_review" in bundle_schema["properties"]
    assert "required_audio_source_ids" in bundle_schema["properties"]["timeline"]["properties"]
    assert "build_units" in bundle_schema["properties"]["output"]["properties"]
    assert "existing_path" in bundle_schema["properties"]["project"]["properties"]

    speech_review = bundle_schema["properties"]["speech_review"]["anyOf"][0]
    assert "auto_transcribe" in speech_review["properties"]
    assert "dialogue_track_id" in speech_review["properties"]

    speech_operation = describe_contract(
        {"view": "operation", "name": "speech.review.inspect"}
    )["operation"]
    assert speech_operation["project_access"] == "read"
    assert speech_operation["required_capabilities"] == [
        "project-editing",
        "transcript-edit-plans",
    ]

    transcription = describe_contract(
        {"view": "operation", "name": "transcript.sequence.transcribe"}
    )["operation"]
    assert "dialogue_track_id" in transcription["arguments_schema"]["properties"]
