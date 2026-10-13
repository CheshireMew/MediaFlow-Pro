from __future__ import annotations

import pytest
from pydantic import ValidationError

from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.domain.speech import SpeechSegmentResult, SpeechTranscribeArguments


def test_public_transcription_contract_exposes_prompt_and_actual_word_times() -> None:
    operation = OPERATIONS["speech.transcribe"]
    arguments = operation.arguments_model.model_json_schema()
    result = operation.result_model.model_json_schema()
    assert "initial_prompt" in arguments["properties"]
    assert "words" in result["$defs"]["SpeechSegmentResult"]["properties"]
    request = SpeechTranscribeArguments(
        input_path="recording.wav", output_path="transcript.srt", initial_prompt="Qwen3、导出视频"
    )
    assert request.initial_prompt == "Qwen3、导出视频"


def test_speech_segment_preserves_recognized_word_boundaries() -> None:
    segment = SpeechSegmentResult(
        start_seconds=1.0, end_seconds=2.0, text="Export video",
        words=[{"text": "Export", "start_seconds": 1.1, "end_seconds": 1.4},
               {"text": "video", "start_seconds": 1.5, "end_seconds": 1.9}],
    )
    assert [word.text for word in segment.words] == ["Export", "video"]
    assert segment.words[1].end_seconds == 1.9


def test_speech_word_outside_its_segment_is_rejected() -> None:
    with pytest.raises(ValidationError, match="inside their segment"):
        SpeechSegmentResult(start_seconds=1, end_seconds=2, text="Export", words=[
            {"text": "Export", "start_seconds": .5, "end_seconds": 1.5},
        ])
