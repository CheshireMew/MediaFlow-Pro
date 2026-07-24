from fractions import Fraction

import pytest
from pydantic import ValidationError

from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.domain.enums import ClipMediaKind, ColorMode, SequenceKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile, Sequence
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames
from mediaflow.domain.timeline import Clip, TimelineMarker, TimelineRange, TimelineState


def test_frame_timebase_preserves_ntsc_rate() -> None:
    frames = seconds_to_frames(Fraction(1001, 1000), 30_000, 1001)
    assert frames == 30
    assert frames_to_seconds(frames, 30_000, 1001) == Fraction(1001, 1000)


def test_hdr10_requires_ten_bit_profile() -> None:
    with pytest.raises(ValidationError, match="HDR10"):
        ProjectProfile(color_mode=ColorMode.HDR10_BT2020_PQ, bit_depth=8)

    profile = ProjectProfile(color_mode=ColorMode.HDR10_BT2020_PQ, bit_depth=10)
    assert profile.bit_depth == 10


def test_operation_progress_only_exposes_percent_for_measured_work() -> None:
    measured = OperationProgress.determinate(
        "rendering",
        completed=25,
        total=200,
        unit="frames",
    )
    unknown = OperationProgress.indeterminate("loading_model")

    assert measured.percent == 12.5
    assert unknown.percent is None
    contextual = measured.with_task_context(
        item_index=2,
        item_total=4,
        item_label="Interview.wav",
        overall_completed=75,
        overall_total=300,
        overall_unit="media_seconds",
    )
    assert contextual.percent == 12.5
    assert contextual.overall_percent == 25.0
    assert contextual.item_index == 2
    with pytest.raises(ValidationError, match="cannot carry measured work"):
        OperationProgress(
            mode="indeterminate",
            message_code="loading_model",
            completed=1,
            total=2,
            unit="items",
        )
    with pytest.raises(ValidationError, match="within its total"):
        OperationProgress.determinate(
            "rendering",
            completed=201,
            total=200,
            unit="frames",
        )


def test_clip_rejects_speed_outside_creator_editor_range() -> None:
    with pytest.raises(ValidationError, match="0.25x"):
        Clip(
            track_id="track",
            asset_id="asset",
            timeline_start=0,
            source_in=0,
            duration=100,
            media_kind=ClipMediaKind.VIDEO_ONLY,
            speed_numerator=5,
        )


def test_reverse_clip_has_positive_timeline_duration() -> None:
    clip = Clip(
        track_id="track",
        asset_id="asset",
        timeline_start=12,
        source_in=200,
        duration=30,
        media_kind=ClipMediaKind.VIDEO_ONLY,
        speed_numerator=-1,
    )
    assert clip.timeline_end == 42


def test_timeline_media_duration_is_not_extended_by_annotations() -> None:
    state = TimelineState(
        sequence=Sequence(
            project_id="project",
            name="Sequence",
            kind=SequenceKind.MAIN,
        ),
        clips=[
            Clip(
                track_id="video",
                asset_id="asset",
                timeline_start=12,
                source_in=0,
                duration=30,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            )
        ],
        markers=[TimelineMarker(sequence_id="sequence", frame=500)],
        ranges=[TimelineRange(sequence_id="sequence", start_frame=400, end_frame=600)],
    )

    assert state.duration_frames == 42


def test_download_request_persists_one_absolute_output_directory(tmp_path) -> None:
    entry = DownloadEntry(
        index=1,
        title="Video",
        page_url="https://example.com/video",
        download_url="https://example.com/video",
    )
    request = DownloadRequest(entry=entry, output_directory=str(tmp_path / "Selected"))

    assert request.output_directory == str((tmp_path / "Selected").resolve())
    with pytest.raises(ValidationError, match="cannot be empty"):
        DownloadRequest(entry=entry, output_directory="")
    with pytest.raises(ValidationError, match="must be absolute"):
        DownloadRequest(entry=entry, output_directory="relative/folder")
