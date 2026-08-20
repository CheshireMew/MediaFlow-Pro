from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from mediaflow.application.export_catalog import default_export_preset
from mediaflow.domain.asr import TranscriptionPlan
from mediaflow.domain.enums import ExportFormat, TrackKind
from mediaflow.domain.exports import (
    ExportPreset,
    SubtitleStyle,
    VideoEncoderPolicy,
    WatermarkOverlay,
)
from mediaflow.domain.sequence_audio import (
    audio_clips_for_track,
    build_dialogue_transcription_plan,
)
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.storage_names import (
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    safe_child_path,
)
from mediaflow.domain.task_commands import ExportSequenceCommand, TranscribeSequenceCommand
from mediaflow.domain.tasks import Task
from mediaflow.domain.timebase import source_frame_at_timeline_offset
from mediaflow.domain.timeline import Clip

from .controllers.web_editor_context import WebEditorContext
from .coordinators import SettingsPersistence, TaskOperations
from .session_state import DesktopSessionState


class PlanningSession(Protocol):
    @property
    def state(self) -> DesktopSessionState: ...


class TranscriptionSession(PlanningSession, Protocol):
    @property
    def settings_persistence(self) -> SettingsPersistence: ...

    @property
    def tasks(self) -> TaskOperations: ...

    def _require_writable(self) -> None: ...


def next_default_export_output(session: PlanningSession, suffix: str) -> Path:
    current = session.state.binding.current
    sequence_id = session.state.binding.active_sequence_id
    if current is None or not sequence_id:
        raise RuntimeError("请先打开一个项目")
    sequence = current.get_sequence(sequence_id)
    extension = "".join(character for character in suffix if character.isalnum()).lower()
    if not extension:
        raise ValueError("导出格式缺少有效的文件扩展名")
    directory = current.project_dir / "exports"
    reserved_outputs = {
        Path(task.command.output_path).resolve()
        for task in session.state.tasks.items.values()
        if isinstance(task.command, ExportSequenceCommand) and not task.status.is_terminal
    }
    sequence_number = 1
    while True:
        numbered_suffix = "" if sequence_number == 1 else f" ({sequence_number})"
        output = safe_child_path(
            directory,
            sequence.name,
            suffix=f"{numbered_suffix}.{extension}",
            required_sibling_component_utf16_units=(OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS),
        )
        if not output.exists() and output.resolve() not in reserved_outputs:
            return output
        sequence_number += 1


def export_preset_for_options(
    session: PlanningSession,
    format_name: str,
    options: dict[str, Any],
) -> ExportPreset:
    timeline = session.state.binding.timeline
    if timeline is None:
        raise RuntimeError("当前没有可导出的时间轴")
    export_format = ExportFormat(format_name)
    profile = timeline.state.sequence.profile
    preset = default_export_preset(export_format, profile.color_mode, profile.fps)
    updates: dict[str, Any] = {}
    field_map = {
        "container": "container",
        "audioCodec": "audio_codec",
        "pixelFormat": "pixel_format",
        "qualityValue": "quality_value",
        "preset": "preset",
        "gopFrames": "gop_frames",
        "audioBitrate": "audio_bitrate",
        "burnSubtitleTrackId": "burn_subtitle_track_id",
    }
    for source_name, target_name in field_map.items():
        value = options.get(source_name)
        if value not in {"", None}:
            updates[target_name] = value
    if export_format != ExportFormat.AUDIO and isinstance(options.get("encoderPolicy"), dict):
        updates["encoder_policy"] = VideoEncoderPolicy.model_validate(options["encoderPolicy"])
    if isinstance(options.get("advanced"), dict):
        updates["advanced"] = options["advanced"]
    if isinstance(options.get("subtitleStyle"), dict):
        updates["subtitle_style"] = SubtitleStyle.model_validate(options["subtitleStyle"])
    if isinstance(options.get("watermark"), dict):
        updates["watermark"] = WatermarkOverlay.model_validate(options["watermark"])
    return ExportPreset.model_validate({**preset.model_dump(mode="python"), **updates})


def current_transcription_plan(
    session: PlanningSession,
    asr: AsrSettings | None = None,
) -> TranscriptionPlan:
    current = session.state.binding.current
    timeline = session.state.binding.timeline
    sequence_id = session.state.binding.active_sequence_id
    if current is None or timeline is None or not sequence_id:
        raise RuntimeError("当前没有可转录的时间轴")
    state = timeline.state
    duration = state.duration_frames
    if duration <= 0:
        raise ValueError("当前时间轴还没有可转录的素材")
    bounds = state.sequence.in_out
    start_frame = min(duration, bounds.in_frame) if bounds else 0
    end_frame = min(duration, bounds.out_frame) if bounds else duration
    project = current.get_project()
    project_profile = current.get_sequence(project.main_sequence_id).profile
    return build_dialogue_transcription_plan(
        state,
        {asset.id: asset for asset in current.list_assets()},
        asr or session.state.service_settings.asr,
        project_profile=project_profile,
        start_frame=start_frame,
        end_frame=end_frame,
    )


def inferred_dialogue_track_id(session: PlanningSession) -> str:
    """Return the existing primary track or one unambiguous audio candidate."""

    current = session.state.binding.current
    timeline = session.state.binding.timeline
    if current is None or timeline is None:
        return ""
    state = timeline.state
    primary = [track.id for track in state.tracks if track.primary_dialogue]
    if len(primary) == 1:
        return primary[0]
    assets = {asset.id: asset for asset in current.list_assets()}
    candidates = [
        track.id
        for track in state.tracks
        if track.kind == TrackKind.AUDIO
        and any(
            clip.asset_id in assets and assets[clip.asset_id].metadata.has_audio
            for clip in audio_clips_for_track(state, track.id)
        )
    ]
    return candidates[0] if len(candidates) == 1 else ""


def start_current_transcription_task(
    session: TranscriptionSession,
    asr: AsrSettings,
) -> Task:
    """Persist the selected ASR settings and start the canonical sequence task."""

    session._require_writable()
    if not asr.model:
        raise ValueError("请选择转录模型")
    candidate = session.state.service_settings.model_copy(deep=True)
    candidate.asr = asr.model_copy(deep=True)
    session.settings_persistence.commit(candidate, "转录设置已更新")
    plan = current_transcription_plan(session, asr)
    if plan.region_count <= 0:
        raise ValueError("请指定主要对白轨，并确认当前范围内有对白素材")
    return session.tasks.create(
        TranscribeSequenceCommand(plan=plan),
        [source.asset_id for source in plan.sources],
        sequence_id=plan.sequence_id,
    )


def selected_web_clip(
    session: PlanningSession,
    context: WebEditorContext,
) -> Clip | None:
    timeline = session.state.binding.timeline
    if timeline is None or not context.clip_id:
        return None
    return next((item for item in timeline.state.clips if item.id == context.clip_id), None)


def web_time_ms_for_frame(
    session: PlanningSession,
    context: WebEditorContext,
    frame: int,
) -> int:
    timeline = session.state.binding.timeline
    clip = selected_web_clip(session, context)
    if clip is None or timeline is None:
        raise RuntimeError("No editable web clip is selected")
    profile = timeline.state.sequence.profile
    local_frame = max(0, min(clip.duration - 1, int(frame) - clip.timeline_start))
    source_frame = source_frame_at_timeline_offset(
        clip.source_in,
        local_frame,
        clip.speed_numerator,
        clip.speed_denominator,
        freeze_source_frame=clip.freeze_source_frame,
    )
    return max(0, round(source_frame * 1000 / profile.fps))


def web_scene_time_for_frame(
    session: PlanningSession,
    context: WebEditorContext,
    frame: int,
) -> tuple[str, int]:
    global_time_ms = web_time_ms_for_frame(session, context, frame)
    elapsed = 0
    scenes = context.manifest.get("scenes", [])
    for scene in scenes:
        duration = int(scene.get("duration_ms") or 0)
        if global_time_ms < elapsed + duration:
            return str(scene["id"]), max(0, global_time_ms - elapsed)
        elapsed += duration
    if not scenes:
        raise RuntimeError("Editable media manifest has no scenes")
    last = scenes[-1]
    return str(last["id"]), max(0, int(last.get("duration_ms") or 1) - 1)
