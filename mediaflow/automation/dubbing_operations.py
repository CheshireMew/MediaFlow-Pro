from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.dubbing import DubbingSettings
from mediaflow.domain.task_commands import (
    CommitDubbingCommand,
    PrepareDubbingCommand,
    SynthesizeDubbingCommand,
)


def list_sessions(context: OperationContext) -> dict:
    return {
        "sessions": context.project.list_dubbing_sessions(
            sequence_id=context.sequence_id()
        )
    }


def get_session(context: OperationContext) -> dict:
    return {
        "session": context.project.get_dubbing_session(
            str(context.required("session_id"))
        )
    }


def prepare(context: OperationContext) -> dict:
    sequence_id = context.sequence_id()
    command = PrepareDubbingCommand(
        sequence_id=sequence_id,
        source_document_id=str(context.required("source_document_id")),
        target_language=str(context.arguments.get("target_language") or "zh_CN"),
        target_document_id=(
            str(context.arguments["target_document_id"])
            if context.arguments.get("target_document_id")
            else None
        ),
        settings=DubbingSettings.model_validate(
            context.arguments.get("settings") or {}
        ),
    )
    return context.task_receipt(
        context.project.start_task(
            command,
            sequence_id=sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )


def synthesize(context: OperationContext) -> dict:
    sequence_id = context.sequence_id()
    return context.task_receipt(
        context.project.start_task(
            SynthesizeDubbingCommand(
                sequence_id=sequence_id,
                session_id=str(context.required("session_id")),
                utterance_ids=[
                    str(item)
                    for item in context.arguments.get("utterance_ids") or ()
                ],
                regenerate=bool(context.arguments.get("regenerate", False)),
            ),
            sequence_id=sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )


def commit(context: OperationContext) -> dict:
    sequence_id = context.sequence_id()
    return context.task_receipt(
        context.project.start_task(
            CommitDubbingCommand(
                sequence_id=sequence_id,
                session_id=str(context.required("session_id")),
                track_name=str(context.arguments.get("track_name") or "中文配音"),
                mute_source_dialogue=bool(
                    context.arguments.get("mute_source_dialogue", True)
                ),
            ),
            sequence_id=sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )


def update_speaker(context: OperationContext) -> dict:
    return {
        "session": context.project.update_dubbing_speaker(
            str(context.required("session_id")),
            str(context.required("speaker_id")),
            expected_revision=int(context.required("expected_revision")),
            display_name=str(context.required("display_name")),
            review_status=str(context.required("review_status")),
            primary_reference_id=str(context.required("primary_reference_id")),
        )
    }


def update_reference(context: OperationContext) -> dict:
    return {
        "session": context.project.update_dubbing_reference(
            str(context.required("session_id")),
            str(context.required("speaker_id")),
            str(context.required("reference_id")),
            expected_revision=int(context.required("expected_revision")),
            text=str(context.required("text")),
            language=str(context.required("language")),
        )
    }


def update_utterance(context: OperationContext) -> dict:
    return {
        "session": context.project.update_dubbing_utterance(
            str(context.required("session_id")),
            str(context.required("utterance_id")),
            expected_revision=int(context.required("expected_revision")),
            target_text=str(context.required("target_text")),
            speaker_id=str(context.required("speaker_id")),
            review_status=str(context.required("review_status")),
        )
    }
