from __future__ import annotations

from typing import cast

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.review import ReviewPriority


def list_threads(context: OperationContext) -> dict:
    threads = context.project.timeline(context.sequence_id()).state.review_threads
    status = str(context.arguments.get("status", "open"))
    if status != "all":
        threads = [item for item in threads if item.status == status]
    return {"threads": threads}


def summarize(context: OperationContext) -> dict:
    threads = context.project.timeline(context.sequence_id()).state.review_threads
    return {
        "open": sum(item.status == "open" for item in threads),
        "resolved": sum(item.status == "resolved" for item in threads),
        "archived": sum(item.status == "archived" for item in threads),
        "blocking_open": sum(
            item.status == "open" and item.priority == "blocking"
            for item in threads
        ),
    }


def create_thread(context: OperationContext) -> dict:
    thread = context.project.timeline(context.sequence_id()).add_review_thread(
        int(context.required("start_frame")),
        str(context.required("body")),
        context.envelope.actor,
        end_frame=(
            int(context.arguments["end_frame"])
            if context.arguments.get("end_frame") is not None
            else None
        ),
        clip_id=(str(context.arguments["clip_id"]) if context.arguments.get("clip_id") else None),
        subject=str(context.arguments.get("subject", "")),
        priority=cast(ReviewPriority, str(context.arguments.get("priority", "normal"))),
    )
    return {"thread": thread}


def reply_thread(context: OperationContext) -> dict:
    thread = context.project.timeline(context.sequence_id()).reply_review_thread(
        str(context.required("thread_id")),
        str(context.required("body")),
        context.envelope.actor,
    )
    return {"thread": thread}


def edit_message(context: OperationContext) -> dict:
    thread = context.project.timeline(context.sequence_id()).edit_review_message(
        str(context.required("thread_id")),
        str(context.required("message_id")),
        str(context.required("body")),
        context.envelope.actor,
    )
    return {"thread": thread}


def update_thread(context: OperationContext) -> dict:
    thread = context.project.timeline(context.sequence_id()).update_review_thread(
        str(context.required("thread_id")),
        start_frame=int(context.required("start_frame")),
        end_frame=(
            int(context.arguments["end_frame"])
            if context.arguments.get("end_frame") is not None
            else None
        ),
        clip_id=(str(context.arguments["clip_id"]) if context.arguments.get("clip_id") else None),
        subject=str(context.arguments.get("subject", "")),
        priority=cast(ReviewPriority, str(context.arguments.get("priority", "normal"))),
    )
    return {"thread": thread}


def resolve_thread(context: OperationContext) -> dict:
    return {
        "thread": context.project.timeline(context.sequence_id()).resolve_review_thread(
            str(context.required("thread_id")),
            context.envelope.actor,
        )
    }


def reopen_thread(context: OperationContext) -> dict:
    return {
        "thread": context.project.timeline(context.sequence_id()).reopen_review_thread(
            str(context.required("thread_id"))
        )
    }


def archive_thread(context: OperationContext) -> dict:
    return {
        "thread": context.project.timeline(context.sequence_id()).archive_review_thread(
            str(context.required("thread_id"))
        )
    }


def restore_thread(context: OperationContext) -> dict:
    return {
        "thread": context.project.timeline(context.sequence_id()).restore_review_thread(
            str(context.required("thread_id"))
        )
    }


def capture_snapshot(context: OperationContext) -> dict:
    sequence_id = context.sequence_id()
    frame = int(context.required("frame"))
    state = context.project.timeline(sequence_id).state
    if not 0 <= frame < state.duration_frames:
        raise ValueError("审阅截图帧必须位于序列范围内")
    context.project.prepare_web_sequence(state)
    _graph, rendered = context.application.render_preview_frames(
        context.project.project_dir,
        state,
        [frame],
        use_proxies=bool(context.arguments.get("use_proxies", True)),
        prefer_sdr_preview_proxy=True,
    )
    proof = rendered[0]
    snapshot = context.project.attach_review_snapshot(
        sequence_id,
        str(context.required("thread_id")),
        frame=frame,
        rendered_path=str(proof["path"]),
        rendered_sha256=str(proof["sha256"]),
        width=cast(int, proof["width"]),
        height=cast(int, proof["height"]),
    )
    thread = next(
        item
        for item in context.project.timeline(sequence_id).state.review_threads
        if item.id == str(context.required("thread_id"))
    )
    return {"snapshot": snapshot, "thread": thread}


def set_snapshot_markup(context: OperationContext) -> dict:
    thread = context.project.set_review_snapshot_markup(
        context.sequence_id(),
        str(context.required("thread_id")),
        str(context.required("snapshot_id")),
        list(context.required("markup")),
    )
    snapshot = next(
        item for item in thread.snapshots if item.id == str(context.required("snapshot_id"))
    )
    return {"snapshot": snapshot, "thread": thread}


def export_package(context: OperationContext):
    return context.project.export_review_package(
        context.sequence_id(),
        str(context.required("destination")),
        thread_ids=(
            [str(item) for item in context.arguments["thread_ids"]]
            if context.arguments.get("thread_ids") is not None
            else None
        ),
        overwrite=bool(context.arguments.get("overwrite", False)),
    )


def import_package(context: OperationContext):
    return context.project.import_review_package(
        context.sequence_id(), str(context.required("source"))
    )
