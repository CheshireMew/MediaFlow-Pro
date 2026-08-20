from __future__ import annotations

from mediaflow.domain.task_commands import AnalyzeSequenceBoundsCommand


def selected_clip_id(session) -> str:
    if session.state.selection.compound_id:
        return ""
    return session.state.selection.clip_ids[-1] if session.state.selection.clip_ids else ""


def selected_video_clip(session):
    if not session.state.binding.timeline or len(session.state.selection.clip_ids) != 1:
        raise ValueError("请先选择一个视频片段")
    clip = next(
        item
        for item in session.state.binding.require_timeline().state.clips
        if item.id == session.state.selection.clip_ids[0]
    )
    asset = session.state.binding.require_current().get_asset(clip.asset_id)
    if asset.kind.value != "video":
        raise ValueError("此操作只适用于视频片段")
    return clip


def sequence_boundary_analysis_running(session) -> bool:
    if not session.state.binding.current or not session.state.binding.active_sequence_id:
        return False
    return any(
        isinstance(task.command, AnalyzeSequenceBoundsCommand)
        and task.command.sequence_id == session.state.binding.active_sequence_id
        and task.status.is_active
        for task in session.state.tasks.items.values()
    )
