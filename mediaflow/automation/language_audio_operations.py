from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.audio import AudioEffect
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
                **document.model_dump(mode="json"),
                "segments": [
                    segment.model_dump(mode="json")
                    for segment in context.project.list_subtitle_segments(
                        document.id
                    )
                ],
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
    return {"segment": segment.model_dump(mode="json")}


def get_transcript(context: OperationContext) -> dict:
    snapshot = context.project.inspect_transcript(
        context.sequence_id(),
        document_id=(
            str(context.arguments["document_id"])
            if context.arguments.get("document_id")
            else None
        ),
    )
    return {"transcript": snapshot.model_dump(mode="json")}


def preview_transcript_edit(context: OperationContext) -> dict:
    edit = TranscriptEditRequest.model_validate(context.required("edit"))
    plan = context.project.preview_transcript_edit(
        edit,
        context.project.timeline(edit.sequence_id),
    )
    return {"plan": plan.model_dump(mode="json")}


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
    return {"edit": result.model_dump(mode="json")}


def inspect_audio(context: OperationContext) -> dict:
    buses = context.project.list_audio_buses(context.sequence_id())
    return {
        "buses": [
            {
                **bus.model_dump(mode="json"),
                "effects": [
                    effect.model_dump(mode="json")
                    for effect in context.project.list_audio_effects(bus.id)
                ],
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
    return {"bus": updated.model_dump(mode="json")}


def save_audio_effect(context: OperationContext) -> dict:
    effect = context.project.save_audio_effect(
        AudioEffect.model_validate(context.required("effect"))
    )
    return {"effect": effect.model_dump(mode="json")}


def remove_audio_effect(context: OperationContext) -> dict:
    context.project.remove_audio_effect(str(context.required("effect_id")))
    return {"removed": True}
