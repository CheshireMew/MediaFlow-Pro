from __future__ import annotations

from typing import cast

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.voiceover import VoiceoverCueStatus


def list_cues(context: OperationContext) -> dict:
    return {
        "cues": context.project.list_voiceover_cues(
            context.sequence_id(),
            include_archived=bool(context.arguments.get("include_archived", False)),
        )
    }


def create_cue(context: OperationContext) -> dict:
    cue = context.project.create_voiceover_cue(
        context.sequence_id(),
        start_frame=int(context.required("start_frame")),
        end_frame=int(context.required("end_frame")),
        text=str(context.required("text")),
        speaker=str(context.arguments.get("speaker") or "旁白"),
        notes=str(context.arguments.get("notes") or ""),
    )
    return {"cue": cue}


def update_cue(context: OperationContext) -> dict:
    cue = context.project.update_voiceover_cue(
        str(context.required("cue_id")),
        expected_revision=int(context.required("expected_revision")),
        start_frame=int(context.required("start_frame")),
        end_frame=int(context.required("end_frame")),
        text=str(context.required("text")),
        speaker=str(context.required("speaker")),
        notes=str(context.arguments.get("notes") or ""),
        status=cast(VoiceoverCueStatus, str(context.required("status"))),
    )
    return {"cue": cue}


def archive_cue(context: OperationContext) -> dict:
    cue = context.project.archive_voiceover_cue(
        str(context.required("cue_id")),
        expected_revision=int(context.required("expected_revision")),
    )
    return {"cue": cue}


def add_take(context: OperationContext) -> dict:
    cue, take = context.project.add_voiceover_take(
        str(context.required("cue_id")),
        str(context.required("source")),
        name=(str(context.arguments["name"]) if context.arguments.get("name") else None),
        notes=str(context.arguments.get("notes") or ""),
        calibration_device_id=(
            str(context.arguments["calibration_device_id"])
            if context.arguments.get("calibration_device_id")
            else None
        ),
    )
    return {"cue": cue, "take": take}


def update_take(context: OperationContext) -> dict:
    take = context.project.update_voiceover_take(
        str(context.required("take_id")),
        name=str(context.required("name")),
        notes=str(context.arguments.get("notes") or ""),
        rating=int(context.required("rating")),
    )
    return {"take": take}


def select_take(context: OperationContext) -> dict:
    cue = context.project.select_voiceover_take(
        str(context.required("cue_id")),
        str(context.required("take_id")),
        expected_revision=int(context.required("expected_revision")),
    )
    return {"cue": cue}


def archive_take(context: OperationContext) -> dict:
    cue, take = context.project.archive_voiceover_take(str(context.required("take_id")))
    return {"cue": cue, "take": take}


def place_take(context: OperationContext) -> dict:
    cue = context.project.place_voiceover_take(
        str(context.required("cue_id")),
        expected_revision=int(context.required("expected_revision")),
    )
    return {"cue": cue}


def list_latency_calibrations(context: OperationContext) -> dict:
    return {"calibrations": context.project.list_voiceover_latency_calibrations()}


def get_latency_calibration(context: OperationContext) -> dict:
    return {
        "calibration": context.project.get_voiceover_latency_calibration(
            str(context.required("device_id"))
        )
    }


def set_latency_calibration(context: OperationContext) -> dict:
    return {
        "calibration": context.project.set_voiceover_latency_calibration(
            device_id=str(context.required("device_id")),
            device_name=str(context.required("device_name")),
            latency_samples=int(context.required("latency_samples")),
            sample_rate=int(context.arguments.get("sample_rate", 48_000)),
        )
    }


def analyze_latency_calibration(context: OperationContext) -> dict:
    return {
        "calibration": context.project.analyze_voiceover_latency_calibration(
            str(context.required("recording")),
            device_id=str(context.required("device_id")),
            device_name=str(context.required("device_name")),
            marker_offset_samples=int(
                context.arguments.get("marker_offset_samples", 12_000)
            ),
        )
    }
