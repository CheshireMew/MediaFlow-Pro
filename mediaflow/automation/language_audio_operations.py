from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.asr import TranscriptionPlan
from mediaflow.domain.audio import AudioEffect
from mediaflow.domain.sequence_audio import build_dialogue_transcription_plan
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.task_commands import TranscribeSequenceCommand
from mediaflow.domain.transcript_edits import (
    TranscriptEditPlan,
    TranscriptEditRequest,
)


def list_subtitles(context: OperationContext) -> dict:
    documents = context.project.list_subtitle_documents(
        sequence_id=context.sequence_id()
    )
    return {
        "documents": [
            {
                **document.model_dump(
                    mode="python",
                    exclude_computed_fields=True,
                ),
                "segments": context.project.list_subtitle_segments(
                    document.id
                ),
            }
            for document in documents
        ]
    }


def update_subtitle_segment(context: OperationContext) -> dict:
    segment = context.project.update_subtitle_segment(
        str(context.required("document_id")),
        str(context.required("segment_id")),
        start_frame=int(context.required("start_frame")),
        end_frame=int(context.required("end_frame")),
        text=str(context.required("text")),
    )
    return {"segment": segment}


def get_transcript(context: OperationContext) -> dict:
    snapshot = context.project.inspect_transcript(
        context.sequence_id(),
        document_id=(
            str(context.arguments["document_id"])
            if context.arguments.get("document_id")
            else None
        ),
    )
    return {"transcript": snapshot}


def transcribe_sequence(context: OperationContext) -> dict:
    sequence_id = context.sequence_id()
    state = context.project.timeline(sequence_id).state
    duration = state.duration_frames
    if duration <= 0:
        raise ValueError("当前时间轴还没有可转录的素材")
    bounds = state.sequence.in_out
    start_value = context.arguments.get("start_frame")
    end_value = context.arguments.get("end_frame")
    start = (
        int(start_value)
        if start_value is not None
        else min(duration, bounds.in_frame) if bounds else 0
    )
    end = (
        int(end_value)
        if end_value is not None
        else min(duration, bounds.out_frame) if bounds else duration
    )
    asr_value = context.arguments.get("asr")
    asr = (
        AsrSettings.model_validate(asr_value)
        if asr_value is not None
        else context.application.service_settings.asr.model_copy(deep=True)
    )
    project = context.project.get_project()
    plan: TranscriptionPlan = build_dialogue_transcription_plan(
        state,
        {asset.id: asset for asset in context.project.list_assets()},
        asr,
        project_profile=context.project.get_sequence(project.main_sequence_id).profile,
        start_frame=start,
        end_frame=end,
    )
    command = TranscribeSequenceCommand(plan=plan)
    command.validate_for_execution()
    return context.task_receipt(
        context.project.start_task(
            command,
            [source.asset_id for source in plan.sources],
            sequence_id=sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )


def preview_transcript_edit(context: OperationContext) -> dict:
    edit = TranscriptEditRequest.model_validate(context.required("edit"))
    plan = context.project.preview_transcript_edit(
        edit,
        context.project.timeline(edit.sequence_id),
    )
    return {"plan": plan}


def apply_transcript_edit(context: OperationContext) -> dict:
    plan = TranscriptEditPlan.model_validate(context.required("plan"))
    if plan.warnings and not bool(
        context.arguments.get("accept_warnings", False)
    ):
        raise ValueError(
            "Transcript edit plan contains warnings; review them and set "
            "arguments.accept_warnings=true to apply"
        )
    result = context.project.apply_transcript_edit(
        plan,
        context.project.timeline(plan.sequence_id),
    )
    return {"edit": result}


def inspect_audio(context: OperationContext) -> dict:
    buses = context.project.list_audio_buses(context.sequence_id())
    return {
        "buses": [
            {
                **bus.model_dump(
                    mode="python",
                    exclude_computed_fields=True,
                ),
                "effects": context.project.list_audio_effects(bus.id),
            }
            for bus in buses
        ]
    }


def update_audio_bus(context: OperationContext) -> dict:
    bus_id = str(context.required("bus_id"))
    bus = next(
        item
        for item in context.project.list_audio_buses(context.sequence_id())
        if item.id == bus_id
    )
    updated = context.project.save_audio_bus(
        bus.model_copy(update=dict(context.required("changes")))
    )
    return {"bus": updated}


def save_audio_effect(context: OperationContext) -> dict:
    effect = context.project.save_audio_effect(
        AudioEffect.model_validate(context.required("effect"))
    )
    return {"effect": effect}


def remove_audio_effect(context: OperationContext) -> dict:
    context.project.remove_audio_effect(str(context.required("effect_id")))
    return {"removed": True}
