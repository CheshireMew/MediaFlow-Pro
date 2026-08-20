from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from mediaflow.domain.audio import AUDIO_EFFECT_DEFINITIONS, AudioEffect
from mediaflow.domain.clip_transform_projection import project_clip_transform_points
from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.domain.editor_fields import EditorFieldValue
from mediaflow.domain.enums import (
    AudioEffectKind,
    ClipMediaKind,
    ColorMode,
    ExportFormat,
    SequenceKind,
    VisualEffectKind,
    WorkflowStage,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile, Sequence
from mediaflow.domain.storage_names import (
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    content_addressed_child_path,
    safe_child_path,
    safe_path_component,
    utf16_units,
)
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeScenesCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    TranslateSegmentsCommand,
    WorkflowTaskLink,
)
from mediaflow.domain.tasks import ArtifactReference
from mediaflow.domain.timebase import (
    frames_to_seconds,
    round_fraction,
    seconds_to_frames,
    source_frame_at_timeline_offset,
    source_interval_for_timeline_interval,
    timeline_offset_for_source_frame,
)
from mediaflow.domain.timeline import (
    Clip,
    ClipTransform,
    ClipTransformKeyframe,
    TimelineMarker,
    TimelineRange,
    TimelineState,
)
from mediaflow.domain.visual_effects import VISUAL_EFFECT_DEFINITIONS, new_visual_effect


def test_frame_timebase_preserves_ntsc_rate() -> None:
    frames = seconds_to_frames(Fraction(1001, 1000), 30_000, 1001)
    assert frames == 30
    assert frames_to_seconds(frames, 30_000, 1001) == Fraction(1001, 1000)


def test_fraction_rounding_has_one_symmetric_half_rule() -> None:
    assert round_fraction(Fraction(1, 2)) == 1
    assert round_fraction(Fraction(-1, 2)) == -1


@pytest.mark.parametrize(
    ("speed_numerator", "speed_denominator", "timeline_offset", "source_frame"),
    [
        (1, 4, 8, 102),
        (1, 1, 8, 108),
        (4, 1, 8, 132),
        (-1, 4, 8, 98),
        (-1, 1, 8, 92),
        (-4, 1, 8, 68),
    ],
)
def test_source_timeline_mapping_is_one_reversible_rational_boundary(
    speed_numerator: int,
    speed_denominator: int,
    timeline_offset: int,
    source_frame: int,
) -> None:
    mapped = source_frame_at_timeline_offset(
        100,
        timeline_offset,
        speed_numerator,
        speed_denominator,
    )

    assert mapped == source_frame
    assert (
        timeline_offset_for_source_frame(
            100,
            mapped,
            speed_numerator,
            speed_denominator,
        )
        == timeline_offset
    )


def test_source_interval_orders_reverse_and_freeze_frames() -> None:
    assert source_interval_for_timeline_interval(100, 2, 10, -2, 1) == (82, 97)
    assert source_interval_for_timeline_interval(
        100,
        2,
        10,
        1,
        1,
        freeze_source_frame=77,
    ) == (77, 78)
    assert round_fraction(Fraction(3, 2)) == 2
    assert round_fraction(Fraction(-3, 2)) == -2


def test_clip_transform_projection_uses_one_clip_local_clock_for_all_renderers() -> None:
    transformed = ClipTransform(x=12, scale_x=1.5)
    clip = Clip(
        track_id="video",
        asset_id="asset",
        timeline_start=100,
        source_in=10,
        duration=5,
        media_kind=ClipMediaKind.VIDEO_ONLY,
        speed_numerator=2,
        transform_keyframes=[
            ClipTransformKeyframe(source_frame=14, transform=transformed),
            ClipTransformKeyframe(source_frame=30, transform=ClipTransform(x=99)),
        ],
    )

    projection = project_clip_transform_points(clip)

    assert projection.has_keyframes is True
    assert projection.points == ((0, clip.transform), (2, transformed))


def test_storage_names_are_windows_safe_normalized_and_length_bounded() -> None:
    assert safe_path_component("  CON.txt  ") == "_CON.txt"
    assert safe_path_component("报告：第一期?.mp4") == "报告：第一期_.mp4"
    assert safe_path_component(" ... ", fallback="Untitled") == "Untitled"
    assert safe_path_component("e\u0301") == "é"
    assert safe_path_component("left\ud800right") == "left_right"

    emoji_name = safe_path_component("😀" * 20, max_utf16_units=9)
    assert emoji_name == "😀" * 4
    assert utf16_units(emoji_name) <= 9

    with pytest.raises(ValueError, match="no usable characters"):
        safe_path_component(" ... ")


def test_safe_child_path_budgets_the_complete_native_tool_path(
    tmp_path: Path,
) -> None:
    parent = tmp_path / ("deep-" * 8)
    child = safe_child_path(
        parent,
        "😀" * 200,
        prefix="02-",
        suffix="-12345678.mp4",
        required_sibling_component_utf16_units=(OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS),
    )

    assert child.parent == parent.resolve()
    assert child.name.startswith("02-")
    assert child.name.endswith("-12345678.mp4")
    assert utf16_units(str(child)) <= 240
    assert utf16_units(child.name) <= 240
    assert utf16_units(str(child.parent)) + 1 + OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS <= 240


def test_content_addressed_children_preserve_full_identity_with_real_path_budget(
    max_project_path: Path,
) -> None:
    parent = max_project_path / "cache" / "b"
    first = content_addressed_child_path(
        parent,
        "完整内容身份：片段一",
        namespace="artifact",
        suffix=".json",
    )
    repeated = content_addressed_child_path(
        parent,
        "完整内容身份：片段一",
        namespace="artifact",
        suffix=".json",
    )
    distinct = content_addressed_child_path(
        parent,
        "完整内容身份：片段二",
        namespace="artifact",
        suffix=".json",
    )
    directory = content_addressed_child_path(
        parent,
        "窗口缓存目录",
        namespace="window",
        suffix="",
        required_descendant_component_utf16_units=32,
    )

    assert first == repeated
    assert first != distinct
    assert utf16_units(str(first)) <= 240
    assert utf16_units(str(directory)) + 1 + 32 <= 240


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


def test_artifact_references_preserve_foreign_absolute_paths(tmp_path: Path) -> None:
    references = [
        ArtifactReference(scope="external", path="/home/runner/output.mp4"),
        ArtifactReference(scope="external", path="C:/Runner/output.mp4"),
    ]
    foreign = next(reference for reference in references if not Path(reference.path).is_absolute())

    assert foreign.display_path(tmp_path) == foreign.path
    assert foreign.local_path(tmp_path) is None
    with pytest.raises(ValueError, match="different operating-system path flavor"):
        foreign.resolve(tmp_path)

    local = ArtifactReference.external(tmp_path / "output.mp4")
    assert local.resolve(tmp_path) == (tmp_path / "output.mp4").resolve()
    with pytest.raises(ValidationError, match="project-relative"):
        ArtifactReference(scope="project", path="C:/Runner/output.mp4")


def test_export_commands_reject_conflicting_or_audio_highlight_presets() -> None:
    audio_preset = ExportPreset(
        name="FLAC",
        format=ExportFormat.AUDIO,
        container="flac",
        encoder_policy=None,
        audio_codec="flac",
        pixel_format=None,
    )

    conflicting = ExportSequenceCommand(
        sequence_id="sequence",
        output_path="output.mp4",
        format=ExportFormat.H264,
        preset=audio_preset,
    )
    with pytest.raises(
        ValueError,
        match="导出预设格式必须与请求的导出格式一致",
    ):
        conflicting.validate_for_execution()
    highlights = ExportHighlightsCommand(
        sequence_id="sequence",
        candidate_ids=["candidate"],
        output_dir="exports",
        preset=audio_preset,
    )
    with pytest.raises(
        ValueError,
        match="高光批量导出必须使用视频预设",
    ):
        highlights.validate_for_execution()

    h264_preset = ExportPreset(
        name="H.264",
        format=ExportFormat.H264,
        container="MP4",
        encoder_policy={"mode": "software"},
        audio_codec="aac",
        pixel_format="yuv420p",
    )
    assert h264_preset.container == "mp4"
    assert h264_preset.preferred_extension == "mp4"
    mismatched_destination = ExportSequenceCommand(
        sequence_id="sequence",
        output_path="mislabelled.mkv",
        format=ExportFormat.H264,
        preset=h264_preset,
    )
    with pytest.raises(ValueError, match="扩展名与封装格式不一致"):
        mismatched_destination.validate_for_execution()

    m4a_preset = audio_preset.model_copy(update={"container": "ipod", "audio_codec": "aac"})
    assert m4a_preset.preferred_extension == "m4a"
    assert m4a_preset.validate_destination("audio.m4a").suffix == ".m4a"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ExportSequenceCommand(
            sequence_id=" ",
            output_path="",
        ),
        lambda: TranslateSegmentsCommand(
            document_id="",
            segment_ids=[],
            target_language=" ",
        ),
        lambda: AnalyzeDownloadCommand(url=" "),
        lambda: AnalyzeScenesCommand(
            sequence_id="",
            clip_id=" ",
        ),
        lambda: WorkflowTaskLink(
            run_id=" ",
            stage=WorkflowStage.DOWNLOAD,
        ),
    ],
)
def test_task_commands_reject_blank_required_inputs(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


@pytest.mark.parametrize(
    "updates",
    [
        {"name": " "},
        {"container": ""},
        {"preset": ""},
        {"quality_mode": "nonsense"},
        {"quality_value": float("nan")},
        {"gop_frames": -10},
        {"audio_bitrate": -1},
    ],
)
def test_export_preset_rejects_unusable_values(
    updates: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "name": "H.264",
        "format": ExportFormat.H264,
        "container": "mp4",
        "encoder_policy": {"mode": "software"},
        "audio_codec": "aac",
        "pixel_format": "yuv420p",
    }
    values.update(updates)

    with pytest.raises(ValidationError):
        ExportPreset.model_validate(values)


def test_audio_export_preset_rejects_video_configuration() -> None:
    with pytest.raises(
        ValidationError,
        match="Audio-only export cannot use a video encoder policy",
    ):
        ExportPreset(
            name="Invalid audio",
            format=ExportFormat.AUDIO,
            container="flac",
            encoder_policy={"mode": "software"},
            audio_codec="flac",
            pixel_format="yuv420p",
        )


def test_export_preset_normalizes_integral_qvariant_numbers() -> None:
    preset = ExportPreset(
        name="QVariant H.264",
        format=ExportFormat.H264,
        container="mp4",
        encoder_policy={"mode": "software"},
        audio_codec="aac",
        pixel_format="yuv420p",
        advanced={
            "width": 1920.0,
            "height": 1080.0,
            "fps_numerator": 25.0,
            "fps_denominator": 1.0,
            "audio_sample_rate": 48_000.0,
            "audio_channels": 2.0,
        },
    )

    assert preset.advanced == {
        "width": 1920,
        "height": 1080,
        "fps_numerator": 25,
        "fps_denominator": 1,
        "audio_sample_rate": 48_000,
        "audio_channels": 2,
    }


def test_editor_field_descriptors_drive_audio_defaults_ranges_and_dynamic_choices() -> None:
    compressor = AudioEffect(
        bus_id="master",
        kind=AudioEffectKind.COMPRESSOR,
        position=0,
    )
    definition = AUDIO_EFFECT_DEFINITIONS[AudioEffectKind.COMPRESSOR]

    assert compressor.parameters == {
        descriptor.id: descriptor.default for descriptor in definition.descriptors
    }
    with pytest.raises(ValidationError, match="below minimum"):
        AudioEffect(
            bus_id="master",
            kind=AudioEffectKind.COMPRESSOR,
            position=0,
            parameters={"ratio": 0.5},
        )
    channel = AUDIO_EFFECT_DEFINITIONS[AudioEffectKind.CHANNEL_MAP].descriptors[0]
    assert [choice.value for choice in channel.constraints.choices] == [
        "mono",
        "stereo",
        "5.1",
    ]
    ducking_driver = AUDIO_EFFECT_DEFINITIONS[AudioEffectKind.DUCKING].descriptors[0]
    assert ducking_driver.options_source == "audio-buses"
    assert ducking_driver.control == "select"
    with pytest.raises(ValidationError, match="target"):
        EditorFieldValue(
            path="audio/driver_bus_id",
            target="audio-effect",
            source_id="driver_bus_id",
            descriptor=ducking_driver,
            value=ducking_driver.default,
        )


def test_visual_effects_use_the_same_descriptor_validation_and_keyframe_contract() -> None:
    effect = new_visual_effect(VisualEffectKind.COLOR_ADJUSTMENT, 0)
    definition = VISUAL_EFFECT_DEFINITIONS[VisualEffectKind.COLOR_ADJUSTMENT]

    assert effect.parameters == {descriptor.id: descriptor.default for descriptor in definition.descriptors}
    assert {descriptor.timeline for descriptor in definition.descriptors} == {"keyframe"}
    with pytest.raises(ValidationError, match="exceeds maximum"):
        type(effect).model_validate(
            {
                **effect.model_dump(mode="python"),
                "parameters": {**effect.parameters, "saturation": 4.0},
            }
        )
