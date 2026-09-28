from pathlib import Path

from mediaflow.domain.enums import ClipMediaKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.infrastructure.interchange_timeline_loader import InterchangeTimelineLoader


def test_fcpxml_loader_preserves_native_timing_adjustments_and_captions(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"interchange-source")
    timeline = tmp_path / "roundtrip.fcpxml"
    timeline.write_text(
        f'''<?xml version="1.0" encoding="utf-8"?>
<fcpxml version="1.11">
  <resources>
    <format id="r1" frameDuration="1/25s" width="1920" height="1080" colorSpace="1-1-1 (Rec. 709)" />
    <asset id="r2" name="source.mp4" duration="2s" hasVideo="1" hasAudio="1" format="r1">
      <media-rep kind="original-media" src="{source.resolve().as_uri()}" />
    </asset>
  </resources>
  <library><event name="Import"><project name="Round trip"><sequence
      format="r1" duration="4/5s" audioLayout="stereo" audioRate="48k">
    <spine><clip name="Round trip" offset="0s" start="0s" duration="4/5s" format="r1">
      <spine>
        <asset-clip ref="r2" name="A" offset="0s" start="2/25s" duration="2/5s">
          <adjust-transform position="10 -20" scale="1.2 0.8" rotation="-15">
            <param name="position"><keyframeAnimation>
              <keyframe time="0s" value="10 -20" interp="linear" />
              <keyframe time="9/25s" value="20 -30" interp="linear" />
            </keyframeAnimation></param>
          </adjust-transform>
          <adjust-blend amount="0.75" />
          <adjust-volume amount="-3dB"><param name="amount" value="-3dB">
            <fadeIn type="linear" duration="2/25s" />
          </param></adjust-volume>
        </asset-clip>
        <transition name="Cross Dissolve" offset="8/25s" duration="4/25s" />
        <asset-clip ref="r2" name="B" offset="2/5s" start="12/25s" duration="2/5s" />
      </spine>
      <caption lane="-1" offset="1/25s" duration="4/25s"
          role="ITT Subtitles?captionFormat=ITT.zh-CN">
        <text><text-style>交换字幕</text-style></text>
      </caption>
      <marker start="3/25s" value="检查点" completed="0" />
    </clip></spine>
  </sequence></project></event></library>
</fcpxml>''',
        encoding="utf-8",
    )

    loaded = InterchangeTimelineLoader().load(
        timeline,
        default_profile=ProjectProfile(),
    )

    assert loaded.summary.format == "fcpxml"
    assert loaded.summary.name == "Round trip"
    assert loaded.summary.duration_frames == 20
    assert loaded.summary.clip_count == 2
    assert loaded.summary.caption_count == 1
    assert loaded.summary.marker_count == 1
    assert loaded.summary.transition_count == 1
    assert loaded.summary.missing_sources == []
    first = loaded.native_clips[0]
    assert first.media_kind == ClipMediaKind.LINKED_AV
    assert (first.timeline_start, first.source_in, first.duration) == (0, 2, 10)
    assert first.audio.gain_db == -3.0
    assert first.audio.fade_in_frames == 2
    assert first.adjustment.position == "10 -20"
    assert [item.frame for item in first.adjustment.animations["position"]] == [0, 9]
    assert loaded.transitions[0].duration == 4
    assert loaded.portable.document.markers[0].label == "检查点"


def test_cmx3600_loader_uses_explicit_source_mapping_and_project_profile(tmp_path: Path) -> None:
    source = tmp_path / "camera-a.mp4"
    source.write_bytes(b"edl-source")
    timeline = tmp_path / "offline.edl"
    timeline.write_text(
        "\n".join(
            (
                "TITLE: Offline assembly",
                "FCM: NON-DROP FRAME",
                "001  AX       V     C        00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00",
                "* FROM CLIP NAME: camera-a.mp4",
                "002  AX       V     D  005   00:00:01:00 00:00:02:00 01:00:01:00 01:00:02:00",
                "* FROM CLIP NAME: camera-a.mp4",
            )
        ),
        encoding="utf-8",
    )

    loaded = InterchangeTimelineLoader().load(
        timeline,
        default_profile=ProjectProfile(width=1280, height=720, fps_numerator=25),
        media_mappings={"AX": str(source)},
    )

    assert loaded.summary.format == "cmx3600"
    assert loaded.summary.name == "Offline assembly"
    assert loaded.summary.profile.width == 1280
    assert loaded.summary.profile.fps_numerator == 25
    assert loaded.summary.duration_frames == 50
    assert loaded.summary.transition_count == 1
    assert loaded.summary.missing_sources == []
    assert [(item.timeline_start, item.source_in, item.duration) for item in loaded.native_clips] == [
        (0, 0, 25),
        (25, 25, 25),
    ]
    assert loaded.transitions[0].duration == 5
