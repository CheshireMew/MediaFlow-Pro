from __future__ import annotations

from mediaflow.automation.operation_context import (
    OperationContext,
    project_snapshot,
)

_OPERATION_LABELS = {
    "timeline.clip.add": "添加片段",
    "timeline.clip.batch.add": "批量添加片段",
    "timeline.clip.freeze.add": "添加定格片段",
    "timeline.clip.move": "移动片段",
    "timeline.clip.copy": "复制片段",
    "timeline.clip.split": "拆分片段",
    "timeline.clip.delete": "删除片段",
    "timeline.clip.transform": "调整片段画面",
    "timeline.clip.audio": "调整片段声音",
    "timeline.clip.source.replace": "替换片段素材",
    "timeline.transition.add": "添加转场",
    "timeline.transition.update": "调整转场",
    "timeline.transition.remove": "删除转场",
    "timeline.marker.add": "添加语义标记",
    "timeline.marker.update": "调整语义标记",
    "timeline.marker.remove": "删除语义标记",
    "subtitle.track.style.update": "调整字幕轨样式",
    "subtitle.segment.update": "调整字幕",
    "script.segment.update": "修改脚本文字或说话人",
    "script.segment.split": "拆分脚本段落",
    "script.segment.merge": "合并脚本段落",
    "script.segment.move": "重排脚本段落",
    "script.gap.close": "收起脚本静音间隙",
    "transcript.edit.apply": "按文字修改时间线",
    "web.clip.update": "调整网页片段",
    "web.clip.data.update": "调整网页内容",
    "web.clip.theme.update": "调整网页主题",
    "project.version.create": "创建工程版本",
    "project.version.restore": "恢复工程版本",
}


def _change_payload(context: OperationContext, since_revision: int) -> dict:
    current_revision = context.project.content_revision()
    actor_kind = context.arguments.get("actor_kind")
    events = [
        event
        for event in context.project.list_project_events_after_revision(since_revision)
        if actor_kind is None or event.actor.kind == actor_kind
    ]
    summaries = []
    for event in events:
        label = _OPERATION_LABELS.get(event.operation, event.operation)
        paths = list(dict.fromkeys(change.path for change in event.changes)) or list(event.write_set)
        actor_name = event.actor.name or event.actor.id
        summaries.append(
            {
                "cursor": event.cursor,
                "project_revision": event.project_revision,
                "actor_kind": event.actor.kind,
                "actor_name": actor_name,
                "operation": event.operation,
                "summary": f"{actor_name}执行了{label}",
                "paths": paths,
            }
        )
    return {
        "since_revision": since_revision,
        "current_revision": current_revision,
        "events": events,
        "summaries": summaries,
    }


def create_project(context: OperationContext) -> dict:
    return project_snapshot(context.project)


def inspect_project(context: OperationContext) -> dict:
    return project_snapshot(context.project)


def upgrade_project(context: OperationContext) -> dict:
    if not context.project.has_pending_project_upgrade():
        raise ValueError("The project already uses the current schema")
    return {
        "upgraded": True,
        **project_snapshot(context.project),
    }


def list_versions(context: OperationContext) -> dict:
    return {"versions": context.project.list_versions()}


def create_version(context: OperationContext) -> dict:
    record = context.project.create_version(str(context.required("name")))
    return {"version": record}


def restore_version(context: OperationContext) -> dict:
    record = context.project.restore_version(str(context.required("version_id")))
    return {
        "restored_version": record,
        **project_snapshot(context.project),
    }


def list_changes(context: OperationContext) -> dict:
    return _change_payload(
        context,
        int(context.required("since_revision")),
    )


def inspect_handoff(context: OperationContext) -> dict:
    versions = context.project.list_versions()
    version_id = context.arguments.get("version_id")
    if version_id is not None:
        anchor = next(
            (version for version in versions if version.id == str(version_id)),
            None,
        )
        if anchor is None:
            raise KeyError(str(version_id))
    else:
        anchor = versions[0] if versions else None
    since_revision = anchor.content_revision if anchor is not None else 0
    changes = _change_payload(context, since_revision)
    offline_asset_ids = []
    for asset in context.project.list_assets():
        try:
            available = context.project.resolve_asset_path(asset).is_file()
        except (FileNotFoundError, OSError, ValueError):
            available = False
        if not available:
            offline_asset_ids.append(asset.id)
    sequence_id = context.arguments.get("sequence_id")
    history = context.project.list_export_history(str(sequence_id) if sequence_id else None)
    latest_export = history[0] if history else None
    export_matches = (
        latest_export is not None and latest_export.content_revision == changes["current_revision"]
    )
    project = context.project.get_project()
    return {
        **changes,
        "project_id": project.id,
        "project_path": str(context.project.project_dir),
        "anchor_version": anchor,
        "offline_asset_ids": offline_asset_ids,
        "latest_export": latest_export,
        "export_matches_current_revision": export_matches,
        "ready_for_handoff": (anchor is not None and not offline_asset_ids and export_matches),
    }


def inspect_context(context: OperationContext) -> dict:
    project = context.project.get_project()
    sequence_id = str(
        context.arguments.get("sequence_id") or project.main_sequence_id
    )
    sequence = context.project.get_sequence(sequence_id)
    timeline = context.project.timeline(sequence_id).state
    transcript = None
    transcript_error = None
    if bool(context.arguments.get("include_transcript", True)):
        try:
            transcript = context.project.inspect_transcript(
                sequence_id,
                document_id=(
                    str(context.arguments["document_id"])
                    if context.arguments.get("document_id")
                    else None
                ),
            )
        except (KeyError, RuntimeError, ValueError) as error:
            transcript_error = str(error)
    return {
        "content_revision": context.project.known_content_revision,
        "project": project,
        "path": str(context.project.project_dir),
        "read_only": context.project.read_only,
        "sequence": sequence,
        "timeline": timeline,
        "transcript": transcript,
        "transcript_error": transcript_error,
        "handoff": inspect_handoff(context),
    }


def list_assets(context: OperationContext) -> dict:
    return {"assets": context.project.list_assets()}


def import_asset(context: OperationContext) -> dict:
    task = context.project.import_asset(
        str(context.required("source")),
        idempotency_key=context.task_idempotency(),
    )
    return context.task_receipt(task)


def create_short_sequence(context: OperationContext) -> dict:
    sequence = context.project.create_short_from_bounds(
        str(context.required("source_sequence_id")),
        int(context.required("start_frame")),
        int(context.required("end_frame")),
        name=str(context.arguments.get("name") or "短视频"),
    )
    return {"sequence": sequence}
