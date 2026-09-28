from __future__ import annotations

from collections.abc import Callable

from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.review import (
    ReviewMarkupShape,
    ReviewMessage,
    ReviewPriority,
    ReviewSnapshot,
    ReviewThread,
    utc_now,
)
from mediaflow.domain.timeline import TimelineState

TimelineMutation = Callable[[TimelineState], None]
TimelineCommit = Callable[[str, TimelineMutation], None]


class TimelineReviewEditing:
    """Recoverable timecode review threads stored with the timeline."""

    def __init__(
        self,
        snapshot: Callable[[], TimelineState],
        apply_change: TimelineCommit,
        project_revision: Callable[[], int],
    ) -> None:
        self.snapshot = snapshot
        self.apply_change = apply_change
        self.project_revision = project_revision

    def add_thread(
        self,
        start_frame: int,
        body: str,
        author: ActorIdentity,
        *,
        end_frame: int | None = None,
        clip_id: str | None = None,
        subject: str = "",
        priority: ReviewPriority = "normal",
    ) -> ReviewThread:
        state = self.snapshot()
        if clip_id is not None and all(item.id != clip_id for item in state.clips):
            raise KeyError(clip_id)
        thread = ReviewThread(
            sequence_id=state.sequence.id,
            start_frame=start_frame,
            end_frame=end_frame,
            clip_id=clip_id,
            project_revision=self.project_revision(),
            sequence_timeline_revision=state.sequence.timeline_revision,
            subject=subject,
            priority=priority,
            messages=[ReviewMessage(author=author, body=body)],
        )

        def mutate(candidate: TimelineState) -> None:
            candidate.review_threads.append(thread)

        self.apply_change("添加审阅批注", mutate)
        return self._thread(thread.id)

    def reply(self, thread_id: str, body: str, author: ActorIdentity) -> ReviewThread:
        thread = self._thread(thread_id)
        self._require_open(thread)
        message = ReviewMessage(author=author, body=body)

        def mutate(state: TimelineState) -> None:
            self._replace(
                state,
                thread_id,
                self._updated_thread(
                    thread,
                    messages=[*thread.messages, message],
                    updated_at=utc_now(),
                ),
            )

        self.apply_change("回复审阅批注", mutate)
        return self._thread(thread_id)

    def add_snapshot(self, thread_id: str, snapshot: ReviewSnapshot) -> ReviewThread:
        thread = self._thread(thread_id)
        if thread.status == "archived":
            raise ValueError("请先恢复已归档的审阅批注")
        if any(item.id == snapshot.id for item in thread.snapshots):
            raise ValueError("审阅截图标识已存在")
        updated = self._updated_thread(
            thread,
            snapshots=[*thread.snapshots, snapshot],
            updated_at=utc_now(),
        )
        self._commit_status("添加审阅截图", thread_id, updated)
        return self._thread(thread_id)

    def set_snapshot_markup(
        self,
        thread_id: str,
        snapshot_id: str,
        markup: list[ReviewMarkupShape],
    ) -> ReviewThread:
        thread = self._thread(thread_id)
        if thread.status == "archived":
            raise ValueError("请先恢复已归档的审阅批注")
        if not any(item.id == snapshot_id for item in thread.snapshots):
            raise KeyError(snapshot_id)
        updated = self._updated_thread(
            thread,
            snapshots=[
                item.model_copy(update={"markup": markup})
                if item.id == snapshot_id
                else item
                for item in thread.snapshots
            ],
            updated_at=utc_now(),
        )
        self._commit_status("更新审阅标注", thread_id, updated)
        return self._thread(thread_id)

    def import_threads(self, threads: list[ReviewThread]) -> list[ReviewThread]:
        if not threads:
            raise ValueError("审阅包中没有可导入的批注")
        state = self.snapshot()
        existing_ids = {item.id for item in state.review_threads}
        incoming_ids = [item.id for item in threads]
        if len(incoming_ids) != len(set(incoming_ids)) or existing_ids.intersection(incoming_ids):
            raise ValueError("导入的审阅批注标识冲突")

        def mutate(candidate: TimelineState) -> None:
            candidate.review_threads.extend(threads)

        self.apply_change("导入审阅包", mutate)
        return [self._thread(item.id) for item in threads]

    def edit_message(
        self,
        thread_id: str,
        message_id: str,
        body: str,
        actor: ActorIdentity,
    ) -> ReviewThread:
        thread = self._thread(thread_id)
        self._require_open(thread)
        message = next(item for item in thread.messages if item.id == message_id)
        if message.author.kind != actor.kind or message.author.id != actor.id:
            raise PermissionError("只能编辑自己发布的审阅消息")
        edited = ReviewMessage.model_validate(
            {
                **message.model_dump(mode="python"),
                "body": body,
                "edited_at": utc_now(),
            }
        )

        def mutate(state: TimelineState) -> None:
            self._replace(
                state,
                thread_id,
                self._updated_thread(
                    thread,
                    messages=[
                        edited if item.id == message_id else item
                        for item in thread.messages
                    ],
                    updated_at=utc_now(),
                ),
            )

        self.apply_change("编辑审阅消息", mutate)
        return self._thread(thread_id)

    def update_thread(
        self,
        thread_id: str,
        *,
        start_frame: int,
        end_frame: int | None,
        clip_id: str | None,
        subject: str,
        priority: ReviewPriority,
    ) -> ReviewThread:
        thread = self._thread(thread_id)
        if thread.status == "archived":
            raise ValueError("请先恢复已归档的审阅批注")
        state = self.snapshot()
        if clip_id is not None and all(item.id != clip_id for item in state.clips):
            raise KeyError(clip_id)
        updated = self._updated_thread(
            thread,
            start_frame=start_frame,
            end_frame=end_frame,
            clip_id=clip_id,
            subject=subject,
            priority=priority,
            updated_at=utc_now(),
        )

        def mutate(candidate: TimelineState) -> None:
            self._replace(candidate, thread_id, updated)

        self.apply_change("更新审阅批注", mutate)
        return self._thread(thread_id)

    def resolve(self, thread_id: str, actor: ActorIdentity) -> ReviewThread:
        thread = self._thread(thread_id)
        self._require_open(thread)
        now = utc_now()
        resolved = self._updated_thread(
            thread,
            status="resolved",
            resolved_at=now,
            resolved_by=actor,
            updated_at=now,
        )
        self._commit_status("解决审阅批注", thread_id, resolved)
        return self._thread(thread_id)

    def reopen(self, thread_id: str) -> ReviewThread:
        thread = self._thread(thread_id)
        if thread.status != "resolved":
            raise ValueError("只有已解决的审阅批注可以重新打开")
        reopened = self._updated_thread(
            thread,
            status="open",
            resolved_at=None,
            resolved_by=None,
            updated_at=utc_now(),
        )
        self._commit_status("重新打开审阅批注", thread_id, reopened)
        return self._thread(thread_id)

    def archive(self, thread_id: str) -> ReviewThread:
        thread = self._thread(thread_id)
        if thread.status == "archived":
            raise ValueError("审阅批注已经归档")
        archived = self._updated_thread(
            thread,
            status="archived",
            archived_from=thread.status,
            updated_at=utc_now(),
        )
        self._commit_status("归档审阅批注", thread_id, archived)
        return self._thread(thread_id)

    def restore(self, thread_id: str) -> ReviewThread:
        thread = self._thread(thread_id)
        if thread.status != "archived" or thread.archived_from is None:
            raise ValueError("只有已归档的审阅批注可以恢复")
        restored = self._updated_thread(
            thread,
            status=thread.archived_from,
            archived_from=None,
            updated_at=utc_now(),
        )
        self._commit_status("恢复审阅批注", thread_id, restored)
        return self._thread(thread_id)

    def _commit_status(self, label: str, thread_id: str, updated: ReviewThread) -> None:
        def mutate(state: TimelineState) -> None:
            self._replace(state, thread_id, updated)

        self.apply_change(label, mutate)

    @staticmethod
    def _updated_thread(thread: ReviewThread, **updates: object) -> ReviewThread:
        return ReviewThread.model_validate(
            {
                **thread.model_dump(mode="python"),
                **updates,
            }
        )

    def _thread(self, thread_id: str) -> ReviewThread:
        try:
            return next(item for item in self.snapshot().review_threads if item.id == thread_id)
        except StopIteration as error:
            raise KeyError(thread_id) from error

    @staticmethod
    def _replace(state: TimelineState, thread_id: str, value: ReviewThread) -> None:
        try:
            index = next(
                index
                for index, item in enumerate(state.review_threads)
                if item.id == thread_id
            )
        except StopIteration as error:
            raise KeyError(thread_id) from error
        state.review_threads[index] = value

    @staticmethod
    def _require_open(thread: ReviewThread) -> None:
        if thread.status != "open":
            raise ValueError("请先重新打开这条审阅批注")
