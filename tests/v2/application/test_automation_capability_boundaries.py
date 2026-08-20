from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from mediaflow.automation.contracts import AutomationRequest
from mediaflow.automation.media_quality_operations import compare_reference
from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.automation.runtime_operations import inspect_runtime
from mediaflow.automation.speech_operations import synthesize, transcribe
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.speech import (
    SpeechSynthesizeArguments,
    SpeechTranscribeArguments,
)


class _RecordingCapability:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[object] = []

    def inspect(self) -> object:
        self.calls.append(None)
        return self.result

    def transcribe(self, request: object) -> object:
        self.calls.append(request)
        return self.result

    def synthesize(self, request: object) -> object:
        self.calls.append(request)
        return self.result

    def compare(self, **arguments: object) -> object:
        self.calls.append(arguments)
        return self.result


def _context(operation: str, arguments: dict[str, Any], application: object) -> OperationContext:
    return OperationContext(
        _project=None,
        _application=cast(Any, application),
        envelope=AutomationRequest(
            operation=operation,
            arguments=arguments,
            actor=ActorIdentity(kind="agent", id="capability-boundary-test"),
            client_id="pytest",
        ),
    )


def test_projectless_automation_handlers_use_composed_application_capabilities() -> None:
    runtime_result = object()
    transcription_result = object()
    synthesis_result = object()
    comparison_result = object()
    runtime = _RecordingCapability(runtime_result)
    speech = _RecordingCapability(transcription_result)
    comparison = _RecordingCapability(comparison_result)
    application = SimpleNamespace(
        runtime_inspection=runtime,
        speech=speech,
        reference_comparison=comparison,
    )

    assert inspect_runtime(_context("runtime.inspect", {}, application)) is runtime_result
    assert transcribe(
        _context(
            "speech.transcribe",
            {"input_path": "input.wav", "output_path": "output.srt"},
            application,
        )
    ) is transcription_result
    speech.result = synthesis_result
    assert synthesize(
        _context(
            "speech.synthesize",
            {
                "text": "你好",
                "text_language": "zh",
                "reference_audio": "reference.wav",
                "reference_text": "参考",
                "reference_language": "zh",
                "output_path": "output.wav",
            },
            application,
        )
    ) is synthesis_result
    assert compare_reference(
        _context(
            "quality.reference.compare",
            {
                "reference_path": "reference.mp4",
                "candidate_path": "candidate.mp4",
                "output_dir": "evidence",
            },
            application,
        )
    ) is comparison_result

    assert runtime.calls == [None]
    assert isinstance(speech.calls[0], SpeechTranscribeArguments)
    assert isinstance(speech.calls[1], SpeechSynthesizeArguments)
    assert comparison.calls == [
        {
            "reference_path": "reference.mp4",
            "candidate_path": "candidate.mp4",
            "output_dir": "evidence",
            "reference_start_frame": 0,
            "candidate_start_frame": 0,
            "frame_count": None,
            "temporal_search_radius_frames": 0,
            "boundary_frame_count": 3,
            "contact_sheet_rows": 8,
            "acceptance": None,
            "overwrite": False,
        }
    ]


def test_speech_operation_schema_names_remain_stable() -> None:
    assert OPERATIONS["speech.transcribe"].arguments_model.__name__ == (
        "SpeechTranscribeArguments"
    )
    assert OPERATIONS["speech.synthesize"].arguments_model.__name__ == (
        "SpeechSynthesizeArguments"
    )
