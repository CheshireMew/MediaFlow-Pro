from __future__ import annotations

from .controller_scopes import WorkspacePlaybackScope


def apply_remote_timeline_selection(
    session: WorkspacePlaybackScope,
    values: dict,
) -> None:
    clip_ids = [str(value) for value in values.get("clip_ids", [])]
    transition_id = str(values.get("transition_id") or "")
    binding = session.state.binding
    if binding.timeline is None:
        if clip_ids or transition_id:
            session.updates.report_error("远程定位失败：当前没有打开的时间线")
        return
    timeline = binding.require_timeline().state
    available_clip_ids = {clip.id for clip in timeline.clips}
    available_transition_ids = {transition.id for transition in timeline.transitions}
    missing_clip_ids = [clip_id for clip_id in clip_ids if clip_id not in available_clip_ids]
    if missing_clip_ids:
        session.updates.report_error(
            "远程定位失败：片段不存在：" + "、".join(missing_clip_ids)
        )
        return
    if transition_id and transition_id not in available_transition_ids:
        session.updates.report_error(f"远程定位失败：转场不存在：{transition_id}")
        return
    selection = session.state.selection
    selection.clip_ids = list(dict.fromkeys(clip_ids))
    selection.transition_id = transition_id
    selection.compound_id = ""
    selection.marker_id = ""
    selection.range_id = ""
    selection.range_in_frame = None
    session.updates.commit(selection=True)
