from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.timeline_controller_scopes import TimelineStructureScope


def add_marker(session: TimelineStructureScope, frame: int) -> None:
    session._require_writable()
    timeline = session.project_binding.require_timeline()
    marker = timeline.add_marker(max(0, frame), f"标记 {len(timeline.state.markers) + 1}")
    session.read_selection().marker_id = marker.id
    session.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线标记已添加")


def rename_marker(session: TimelineStructureScope, marker_id: str, name: str) -> None:
    session._require_writable()
    next_name = name.strip()
    if not next_name:
        raise ValueError("标记名称不能为空")
    timeline = session.project_binding.require_timeline()
    marker = next(item for item in timeline.state.markers if item.id == marker_id)
    timeline.update_marker(marker_id, frame=marker.frame, name=next_name, color=marker.color)
    session.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线标记已重命名")


def remove_marker(session: TimelineStructureScope, marker_id: str) -> None:
    session._require_writable()
    session.project_binding.require_timeline().remove_marker(marker_id)
    session.read_selection().marker_id = ""
    session.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线标记已删除；可使用撤销恢复")


def set_range_in(session: TimelineStructureScope, frame: int) -> None:
    session.read_selection().range_in_frame = max(0, frame)
    session.updates.commit(selection=True)


def commit_range(session: TimelineStructureScope, frame: int) -> None:
    session._require_writable()
    selection = session.read_selection()
    if selection.range_in_frame is None:
        raise ValueError("请先设置选区入点")
    start_frame, end_frame = sorted((selection.range_in_frame, max(0, frame)))
    if start_frame == end_frame:
        raise ValueError("选区必须包含至少一帧")
    timeline = session.project_binding.require_timeline()
    item = timeline.add_range(
        start_frame,
        end_frame,
        f"选区 {len(timeline.state.ranges) + 1}",
    )
    session.read_selection().range_id = item.id
    session.read_selection().range_in_frame = None
    session.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线选区已添加")


def rename_range(session: TimelineStructureScope, range_id: str, name: str) -> None:
    session._require_writable()
    next_name = name.strip()
    if not next_name:
        raise ValueError("选区名称不能为空")
    timeline = session.project_binding.require_timeline()
    item = next(candidate for candidate in timeline.state.ranges if candidate.id == range_id)
    timeline.update_range(
        range_id,
        start_frame=item.start_frame,
        end_frame=item.end_frame,
        name=next_name,
        color=item.color,
    )
    session.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线选区已重命名")


def remove_range(session: TimelineStructureScope, range_id: str) -> None:
    session._require_writable()
    session.project_binding.require_timeline().remove_range(range_id)
    session.read_selection().range_id = ""
    session.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线选区已删除；可使用撤销恢复")
