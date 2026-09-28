from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import Property, QUrl, Signal, Slot

from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.review import ReviewPriority, ReviewThread

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope
from .timeline_selection import selected_clip_id


class TimelineReviewController(ControllerFacet[TimelinePresentationScope]):
    reviewChanged = Signal()

    def __init__(self, scope: TimelinePresentationScope) -> None:
        super().__init__(scope)
        self._review_mode = False
        self._status_filter = "open"
        self._snapshot_running = False
        self._snapshot_request_id = 0

    @Property(bool, notify=reviewChanged)
    def snapshotRunning(self) -> bool:
        return self._snapshot_running

    def _get_review_mode(self) -> bool:
        return self._review_mode

    def _set_review_mode(self, value: bool) -> None:
        normalized = bool(value)
        if normalized == self._review_mode:
            return
        self._review_mode = normalized
        self.reviewChanged.emit()

    reviewMode = Property(
        bool,
        _get_review_mode,
        _set_review_mode,
        notify=reviewChanged,
    )

    def _get_status_filter(self) -> str:
        return self._status_filter

    def _set_status_filter(self, value: str) -> None:
        if value not in {"open", "resolved", "archived", "all"}:
            raise ValueError("未知的审阅筛选状态")
        if value == self._status_filter:
            return
        self._status_filter = value
        self.reviewChanged.emit()

    statusFilter = Property(
        str,
        _get_status_filter,
        _set_status_filter,
        notify=reviewChanged,
    )

    @Property(list, notify=reviewChanged)
    def filterOptions(self) -> list[dict[str, str]]:
        return [
            {"label": "待处理", "value": "open"},
            {"label": "已解决", "value": "resolved"},
            {"label": "已归档", "value": "archived"},
            {"label": "全部", "value": "all"},
        ]

    @Property(list, notify=reviewChanged)
    def priorityOptions(self) -> list[dict[str, str]]:
        return [
            {"label": "普通", "value": "normal"},
            {"label": "低", "value": "low"},
            {"label": "高", "value": "high"},
            {"label": "阻断交付", "value": "blocking"},
        ]

    @Property(int, notify=reviewChanged)
    def openCount(self) -> int:
        return sum(item.status == "open" for item in self._threads())

    @Property(int, notify=reviewChanged)
    def blockingOpenCount(self) -> int:
        return sum(
            item.status == "open" and item.priority == "blocking"
            for item in self._threads()
        )

    @Property(list, notify=reviewChanged)
    def visibleThreads(self) -> list[dict[str, Any]]:
        rows = self._threads()
        if self._status_filter != "all":
            rows = [item for item in rows if item.status == self._status_filter]
        return [self._thread_row(item) for item in rows]

    @Slot(int, int, str, str, str)
    @report_ui_errors
    def addThread(
        self,
        start_frame: int,
        end_frame: int,
        subject: str,
        priority: str,
        body: str,
    ) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().add_review_thread(
            start_frame,
            body,
            self._actor(),
            end_frame=end_frame if end_frame > start_frame else None,
            clip_id=selected_clip_id(self._session) or None,
            subject=subject,
            priority=cast(ReviewPriority, priority),
        )
        self._after_change()
        self._session._set_status("审阅批注已添加")

    @Slot(str, str)
    @report_ui_errors
    def replyThread(self, thread_id: str, body: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().reply_review_thread(
            thread_id,
            body,
            self._actor(),
        )
        self._after_change()
        self._session._set_status("审阅回复已添加")

    @Slot(str)
    @report_ui_errors
    def resolveThread(self, thread_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().resolve_review_thread(
            thread_id,
            self._actor(),
        )
        self._after_change()
        self._session._set_status("审阅批注已解决")

    @Slot(str)
    @report_ui_errors
    def reopenThread(self, thread_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().reopen_review_thread(thread_id)
        self._after_change()
        self._session._set_status("审阅批注已重新打开")

    @Slot(str)
    @report_ui_errors
    def archiveThread(self, thread_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().archive_review_thread(thread_id)
        self._after_change()
        self._session._set_status("审阅批注已归档，可随时恢复")

    @Slot(str)
    @report_ui_errors
    def restoreThread(self, thread_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().restore_review_thread(thread_id)
        self._after_change()
        self._session._set_status("审阅批注已恢复")

    @Slot(str, int)
    @report_ui_errors
    def captureSnapshot(self, thread_id: str, frame: int) -> None:
        self._session._require_writable()
        current = self._session.state.binding.require_current()
        self._snapshot_request_id += 1
        request_id = self._snapshot_request_id
        self._snapshot_running = True
        self.reviewChanged.emit()
        self._session.background.submit_project_callback(
            "review_snapshot",
            request_id,
            lambda: self._session._api.execute_read_operation(
                "preview.frames.render",
                current.project_dir,
                {
                    "sequence_id": self._session.state.binding.active_sequence_id,
                    "frames": [frame],
                    "use_proxies": True,
                },
            ),
            on_result=lambda result: self._finish_snapshot(
                request_id, thread_id, frame, result
            ),
            on_error=lambda error: self._fail_snapshot(request_id, error),
        )

    @Slot(str, str, list)
    @report_ui_errors
    def setSnapshotMarkup(
        self, thread_id: str, snapshot_id: str, markup: list[object]
    ) -> None:
        self._session._require_writable()
        current = self._session.state.binding.require_current()
        normalized: list[dict[str, Any]] = []
        for item in markup:
            if not isinstance(item, dict):
                raise ValueError("审阅截图标注必须是对象列表")
            normalized.append(dict(item))
        current.set_review_snapshot_markup(
            self._session.state.binding.active_sequence_id,
            thread_id,
            snapshot_id,
            normalized,
        )
        self._after_change()
        self._session._set_status("审阅截图标注已保存")

    @Slot(str)
    @report_ui_errors
    def exportPackage(self, destination_url: str) -> None:
        current = self._session.state.binding.require_current()
        destination = self._local_path(destination_url)
        result = current.export_review_package(
            self._session.state.binding.active_sequence_id,
            destination,
        )
        self._session._set_status("审阅包已导出：%1", result.path)

    @Slot(str)
    @report_ui_errors
    def importPackage(self, source_url: str) -> None:
        self._session._require_writable()
        current = self._session.state.binding.require_current()
        result = current.import_review_package(
            self._session.state.binding.active_sequence_id,
            self._local_path(source_url),
        )
        self._after_change()
        self._session._set_status("已导入 %1 条审阅批注", result.thread_count)

    def _threads(self) -> list[ReviewThread]:
        if not self._session.state.binding.timeline:
            return []
        return sorted(
            self._session.state.binding.require_timeline().state.review_threads,
            key=lambda item: (item.start_frame, item.id),
        )

    def _actor(self) -> ActorIdentity:
        return self._session.state.binding.require_current().actor_identity

    def _thread_row(self, thread: ReviewThread) -> dict[str, Any]:
        return {
            "threadId": thread.id,
            "startFrame": thread.start_frame,
            "endFrame": thread.end_frame if thread.end_frame is not None else -1,
            "clipId": thread.clip_id or "",
            "projectRevision": thread.project_revision,
            "sequenceTimelineRevision": thread.sequence_timeline_revision,
            "subject": thread.subject,
            "priority": thread.priority,
            "status": thread.status,
            "archivedFrom": thread.archived_from or "",
            "createdAt": thread.created_at.isoformat(),
            "updatedAt": thread.updated_at.isoformat(),
            "messages": [
                {
                    "messageId": item.id,
                    "author": item.author.name or item.author.id,
                    "authorKind": item.author.kind,
                    "body": item.body,
                    "createdAt": item.created_at.isoformat(),
                    "editedAt": item.edited_at.isoformat() if item.edited_at else "",
                }
                for item in thread.messages
            ],
            "snapshots": [
                {
                    "snapshotId": item.id,
                    "frame": item.frame,
                    "relativePath": item.relative_path,
                    "fileUrl": QUrl.fromLocalFile(
                        str(
                            (
                                self._session.state.binding.require_current().project_dir
                                / Path(item.relative_path)
                            ).resolve()
                        )
                    ).toString(),
                    "sha256": item.sha256,
                    "width": item.width,
                    "height": item.height,
                    "markup": [shape.model_dump(mode="json") for shape in item.markup],
                }
                for item in thread.snapshots
            ],
        }

    def _after_change(self) -> None:
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self.reviewChanged.emit()

    def _finish_snapshot(
        self,
        request_id: int,
        thread_id: str,
        frame: int,
        result: object | None,
    ) -> None:
        if request_id != self._snapshot_request_id:
            return
        if not isinstance(result, dict) or not isinstance(result.get("frames"), list):
            self._fail_snapshot(request_id, RuntimeError("审阅截图返回了无效结果"))
            return
        frames = result["frames"]
        if len(frames) != 1 or not isinstance(frames[0], dict):
            self._fail_snapshot(request_id, RuntimeError("审阅截图数量不正确"))
            return
        proof = frames[0]
        current = self._session.state.binding.require_current()
        current.attach_review_snapshot(
            self._session.state.binding.active_sequence_id,
            thread_id,
            frame=frame,
            rendered_path=str(proof["path"]),
            rendered_sha256=str(proof["sha256"]),
            width=int(proof["width"]),
            height=int(proof["height"]),
        )
        self._snapshot_running = False
        self._after_change()
        self._session._set_status("审阅截图已保存")

    def _fail_snapshot(self, request_id: int, error: BaseException) -> None:
        if request_id != self._snapshot_request_id:
            return
        self._snapshot_running = False
        self.reviewChanged.emit()
        self._session.updates.report_error(str(error))

    @staticmethod
    def _local_path(value: str) -> str:
        url = QUrl(value)
        return url.toLocalFile() if url.isLocalFile() else value
