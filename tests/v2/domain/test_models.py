from fractions import Fraction

import pytest
from pydantic import ValidationError

from mediaflow.domain.enums import ColorMode
from mediaflow.domain.models import Clip, ProjectProfile
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames


def test_frame_timebase_preserves_ntsc_rate() -> None:
    frames = seconds_to_frames(Fraction(1001, 1000), 30_000, 1001)
    assert frames == 30
    assert frames_to_seconds(frames, 30_000, 1001) == Fraction(1001, 1000)


def test_hdr10_requires_ten_bit_profile() -> None:
    with pytest.raises(ValidationError, match="HDR10"):
        ProjectProfile(color_mode=ColorMode.HDR10_BT2020_PQ, bit_depth=8)

    profile = ProjectProfile(color_mode=ColorMode.HDR10_BT2020_PQ, bit_depth=10)
    assert profile.bit_depth == 10


def test_clip_rejects_speed_outside_creator_editor_range() -> None:
    with pytest.raises(ValidationError, match="0.25x"):
        Clip(
            track_id="track",
            asset_id="asset",
            timeline_start=0,
            source_in=0,
            duration=100,
            speed_numerator=5,
        )


def test_reverse_clip_has_positive_timeline_duration() -> None:
    clip = Clip(
        track_id="track",
        asset_id="asset",
        timeline_start=12,
        source_in=200,
        duration=30,
        speed_numerator=-1,
    )
    assert clip.timeline_end == 42
