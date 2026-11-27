from __future__ import annotations

from mediaflow.desktop.session_state import ProjectBinding, SelectionState, TaskViewState
from mediaflow.domain.task_commands import AnalyzeSequenceBoundsCommand
from mediaflow.domain.timeline import Clip


def selected_clip_id(selection: SelectionState) -> str:
    if selection.compound_id:
        return ""
    return selection.clip_ids[-1] if selection.clip_ids else ""


def selected_video_clip(binding: ProjectBinding, selection: SelectionState) -> Clip:
    if not binding.timeline or len(selection.clip_ids) != 1:
        raise ValueError("请先选择一个视频片段")
    clip = next(item for item in binding.require_timeline().state.clips if item.id == selection.clip_ids[0])
    asset = binding.require_current().get_asset(clip.asset_id)
    if asset.kind.value != "video":
        raise ValueError("此操作只适用于视频片段")
    return clip


def sequence_boundary_analysis_running(binding: ProjectBinding, tasks: TaskViewState) -> bool:
    if not binding.current or not binding.active_sequence_id:
        return False
    return any(
        isinstance(task.command, AnalyzeSequenceBoundsCommand)
        and task.command.sequence_id == binding.active_sequence_id
        and task.status.is_active
        for task in tasks.items.values()
    )
