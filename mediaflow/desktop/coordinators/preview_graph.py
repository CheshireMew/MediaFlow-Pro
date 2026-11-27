from __future__ import annotations

import hashlib

from PySide6.QtCore import QTimer, Slot

from mediaflow.desktop.component_contexts import PreviewGraphCoordinatorContext
from mediaflow.desktop.coordinators.base import SessionCoordinator
from mediaflow.domain.enums import AssetKind, ColorMode, TaskStatus
from mediaflow.domain.task_commands import RenderWebClipCommand
from mediaflow.domain.tasks import Task

PREVIEW_GRAPH_BASE_IDLE_MS = 180
PREVIEW_GRAPH_MAX_IDLE_MS = 2_500
PREVIEW_GRAPH_CLIPS_PER_ADDED_MS = 2


def preview_graph_idle_delay_ms(clip_count: int) -> int:
    """Debounce heavier native graphs until foreground timeline edits are idle."""

    return min(
        PREVIEW_GRAPH_MAX_IDLE_MS,
        PREVIEW_GRAPH_BASE_IDLE_MS + max(0, int(clip_count)) // PREVIEW_GRAPH_CLIPS_PER_ADDED_MS,
    )


class PreviewGraphCoordinator(SessionCoordinator[PreviewGraphCoordinatorContext]):
    """Own preview debounce, missing render admission, and background graph requests."""

    def __init__(self, context: PreviewGraphCoordinatorContext):
        super().__init__(context)
        self._preview_timer = QTimer(context.parent)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_GRAPH_BASE_IDLE_MS)
        self._preview_timer.timeout.connect(self.compile)
        self._web_attempts: dict[tuple[int, str, str], str] = {}
        self._retry_requested = False

    def retry(self) -> None:
        self._web_attempts.clear()
        self._retry_requested = True
        self.schedule()

    def _set_preview_status(self, status: str, error: str = "") -> None:
        presentation = self._context.state.presentation
        if presentation.preview_status != status or presentation.preview_error != error:
            presentation.preview_status = status
            presentation.preview_error = error
            self._context.updates.commit(preview_graph=True)

    def stop(self) -> None:
        self._preview_timer.stop()

    def schedule(self, *, clip_count: int | None = None) -> None:
        # Invalidate an already compiled result as soon as the timeline changes,
        # rather than waiting for the debounce timer to submit its replacement.
        self._context.state.requests.preview_id += 1
        timeline = self._context.state.binding.timeline
        resolved_clip_count = (
            clip_count if clip_count is not None else len(timeline.state.clips) if timeline else 0
        )
        if (
            not self._context.state.binding.current
            or not self._context.state.binding.timeline
            or resolved_clip_count == 0
        ):
            self._preview_timer.stop()
            self._set_preview_status("empty")
            if self._context.state.presentation.preview_graph_path:
                self._context.state.presentation.preview_graph_path = ""
                self._context.updates.commit(preview_graph=True)
            return
        self._set_preview_status("preparing")
        self._preview_timer.setInterval(preview_graph_idle_delay_ms(resolved_clip_count))
        self._preview_timer.start()

    @Slot()
    def compile(self) -> None:
        try:
            self._compile()
        except Exception as error:
            self._set_preview_status("failed", str(error))
            self._context.updates.report_error(f"预览图编译失败：{error}")

    def _compile(self) -> None:
        retry_requested, self._retry_requested = self._retry_requested, False
        if not self._context.state.binding.current or not self._context.state.binding.timeline:
            return
        state = self._context.state.binding.require_timeline().state
        if not state.clips:
            return
        assets = {asset.id: asset for asset in self._context.state.binding.require_current().list_assets()}
        missing_web_clips = [
            clip
            for clip in state.clips
            if assets[clip.asset_id].kind == AssetKind.WEB
            and not self._context.state.binding.require_current().web_render_cache_ready(state, clip.id)
        ]
        if missing_web_clips:
            generation = self._context.state.binding.generation
            self._web_attempts = {
                key: value for key, value in self._web_attempts.items() if key[0] == generation
            }
            latest_tasks: dict[str, Task] = {}
            for candidate in self._context.state.tasks.items.values():
                if (
                    not isinstance(candidate.command, RenderWebClipCommand)
                    or candidate.command.sequence_id != state.sequence.id
                ):
                    continue
                previous = latest_tasks.get(candidate.command.clip_id)
                if previous is None or (candidate.created_at, candidate.id) > (
                    previous.created_at,
                    previous.id,
                ):
                    latest_tasks[candidate.command.clip_id] = candidate
            blocked = ""
            blocked_error = ""
            for clip in missing_web_clips:
                scope = (generation, state.sequence.id, clip.id)
                # Render identity excludes placement and native post-processing.
                # A new web state or source range may prepare once; retries of the
                # same content remain an explicit user action, even after removal
                # of the failed/cancelled task from the visible history.
                identity = hashlib.sha256(
                    (
                        state.web_states[clip.id].model_dump_json(exclude={"revision"})
                        + state.sequence.profile.model_dump_json()
                        + repr(
                            (
                                clip.asset_id,
                                clip.source_in,
                                clip.duration,
                                clip.speed_numerator,
                                clip.speed_denominator,
                                clip.freeze_source_frame,
                            )
                        )
                    ).encode("utf-8")
                ).hexdigest()
                task = latest_tasks.get(clip.id)
                if task is not None and task.status.is_active:
                    self._web_attempts.setdefault(scope, identity)
                    if task.status == TaskStatus.PAUSED:
                        blocked = blocked or "paused"
                    continue
                if task is not None and task.status == TaskStatus.COMPLETED:
                    # Completed output may have been evicted from the render
                    # cache. Rebuild it rather than presenting it as cancelled.
                    self._web_attempts.pop(scope, None)
                if (
                    not retry_requested
                    and task is not None
                    and task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}
                ):
                    self._web_attempts.setdefault(scope, identity)
                if self._web_attempts.get(scope) == identity:
                    blocked = (
                        "failed"
                        if task is not None and task.status == TaskStatus.FAILED
                        else blocked or "cancelled"
                    )
                    if task is not None and task.status == TaskStatus.FAILED:
                        blocked_error = task.error or ""
                    continue
                self._web_attempts[scope] = identity
                self._context.create(
                    RenderWebClipCommand(sequence_id=state.sequence.id, clip_id=clip.id),
                    [clip.asset_id],
                    sequence_id=state.sequence.id,
                )
            self._set_preview_status(blocked or "preparing", blocked_error)
            return
        request_id = self._context.state.requests.preview_id
        generation = self._context.state.binding.generation
        project_dir = self._context.state.binding.require_current().project_dir
        request_owner = self._context.state.binding.require_current().actor_id
        use_proxies = self._context.state.service_settings.preview.preview_quality != "source"
        prefer_sdr_preview_proxy = (
            state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
            and not self._context.state.presentation.hdr_preview_active
        )
        if (
            self._context.state.requests.preview_future is not None
            and not self._context.state.requests.preview_future.running()
        ):
            self._context.state.requests.preview_future.cancel()
        self._context.state.requests.preview_future = self._context.submit_background(
            "preview",
            (generation, request_id, state.sequence.id),
            lambda: self._context.write_preview_snapshot(
                project_dir,
                state,
                use_proxies=use_proxies,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
                request_owner=request_owner,
                request_generation=request_id,
            ),
            executor=self._context.preview_executor,
        )
