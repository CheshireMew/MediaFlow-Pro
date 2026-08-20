from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.speech import (
    SpeechSynthesisResult,
    SpeechSynthesizeArguments,
    SpeechTranscribeArguments,
    SpeechTranscriptionResult,
)


def transcribe(context: OperationContext) -> SpeechTranscriptionResult:
    return context.application.speech.transcribe(
        SpeechTranscribeArguments.model_validate(context.arguments)
    )


def synthesize(context: OperationContext) -> SpeechSynthesisResult:
    return context.application.speech.synthesize(
        SpeechSynthesizeArguments.model_validate(context.arguments)
    )
