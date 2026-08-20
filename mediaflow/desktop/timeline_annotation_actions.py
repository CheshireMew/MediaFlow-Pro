from __future__ import annotations

from typing import Any


def add_marker(session: Any, frame: int) -> None:
    session._require_writable()
    timeline = session.state.binding.require_timeline()
    marker = timeline.add_marker(max(0, frame), f"标记 {len(timeline.state.markers) + 1}")
    session.state.selection.marker_id = marker.id
    session.projectors.timeline.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线标记已添加")


def rename_marker(session: Any, marker_id: str, name: str) -> None:
    session._require_writable()
    next_name = name.strip()
    if not next_name:
        raise ValueError("标记名称不能为空")
    timeline = session.state.binding.require_timeline()
    marker = next(item for item in timeline.state.markers if item.id == marker_id)
    timeline.update_marker(marker_id, frame=marker.frame, name=next_name, color=marker.color)
    session.projectors.timeline.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线标记已重命名")


def remove_marker(session: Any, marker_id: str) -> None:
    session._require_writable()
    session.state.binding.require_timeline().remove_marker(marker_id)
    session.state.selection.marker_id = ""
    session.projectors.timeline.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线标记已删除；可使用撤销恢复")


def set_range_in(session: Any, frame: int) -> None:
    session.state.selection.range_in_frame = max(0, frame)
    session.updates.commit(selection=True)


def commit_range(session: Any, frame: int) -> None:
    session._require_writable()
    if session.state.selection.range_in_frame is None:
        raise ValueError("请先设置选区入点")
    start_frame, end_frame = sorted((session.state.selection.range_in_frame, max(0, frame)))
    if start_frame == end_frame:
        raise ValueError("选区必须包含至少一帧")
    timeline = session.state.binding.require_timeline()
    item = timeline.add_range(
        start_frame,
        end_frame,
        f"选区 {len(timeline.state.ranges) + 1}",
    )
    session.state.selection.range_id = item.id
    session.state.selection.range_in_frame = None
    session.projectors.timeline.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线选区已添加")


def rename_range(session: Any, range_id: str, name: str) -> None:
    session._require_writable()
    next_name = name.strip()
    if not next_name:
        raise ValueError("选区名称不能为空")
    timeline = session.state.binding.require_timeline()
    item = next(candidate for candidate in timeline.state.ranges if candidate.id == range_id)
    timeline.update_range(
        range_id,
        start_frame=item.start_frame,
        end_frame=item.end_frame,
        name=next_name,
        color=item.color,
    )
    session.projectors.timeline.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线选区已重命名")


def remove_range(session: Any, range_id: str) -> None:
    session._require_writable()
    session.state.binding.require_timeline().remove_range(range_id)
    session.state.selection.range_id = ""
    session.projectors.timeline.refresh_timeline()
    session.updates.commit(selection=True)
    session.updates.commit(history=True)
    session._set_status("时间线选区已删除；可使用撤销恢复")
