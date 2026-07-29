from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mediaflow.application.asset_service import (
    AssetService,
    PreparedAssetRegistration,
)
from mediaflow.application.ports import (
    AssetTaskDocuments,
    AssetTaskRuntime,
    DownloadTaskRuntime,
    WebTaskDocuments,
    WebTaskRuntime,
)
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.timeline_clock import asset_in_timeline_clock
from mediaflow.domain.enums import AssetKind, AssetOrigin
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import GlobalSettings
from mediaflow.domain.task_commands import (
    DownloadMediaCommand,
    ExportWebClipCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    ImportAssetCommand,
    RenderWebClipCommand,
)
from mediaflow.domain.tasks import ImportedAssetTaskOutcome


class WebRenderTaskHandler(ProjectTaskHandler):
    def __init__(
        self,
        documents: WebTaskDocuments,
        runtime: WebTaskRuntime,
    ):
        super().__init__(documents.project_dir)
        self.documents = documents
        self.runtime = runtime

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = context.task.command
        if not isinstance(command, (RenderWebClipCommand, ExportWebClipCommand)):
            raise TypeError(f"Unexpected web render command: {type(command).__name__}")
        state = self.documents.timeline.load_timeline(command.sequence_id)
        context.report(OperationProgress.indeterminate("web_render_preparing"))
        if isinstance(command, ExportWebClipCommand):
            output = Path(
                self.runtime.render_web_export(
                    state,
                    command.clip_id,
                    command.output_path,
                    command.format,
                    time_ms=command.time_ms,
                    background=command.background,
                    overwrite=command.overwrite or context.recovered,
                    progress=context.report,
                    check_cancelled=context.cancellation.raise_if_requested,
                ).output_path
            )
        else:
            output = self.runtime.render_web_clip(
                state,
                command.clip_id,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
            )
        return self.completion(output)


class AssetTaskHandlers(ProjectTaskHandler):
    def __init__(
        self,
        documents: AssetTaskDocuments,
        assets: AssetService,
        runtime: AssetTaskRuntime,
        subtitle_acquisition: SubtitleAcquisitionService,
    ):
        super().__init__(documents.project_dir)
        self.documents = documents
        self.assets = assets
        self.runtime = runtime
        self.subtitle_acquisition = subtitle_acquisition

    def import_asset(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, ImportAssetCommand)
        context.report(OperationProgress.indeterminate("import_probing"))
        document_id: str | None = None
        if command.purpose == "subtitle":
            document = self.subtitle_acquisition.import_subtitle_file(
                command.source_path,
                self.assets,
                language=command.language,
                media_asset_id=command.media_asset_id,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            asset = self.documents.catalog.get_asset(document.asset_id)
            document_id = document.id
        else:
            asset = self.assets.import_external(
                command.source_path,
                expected_kind=(AssetKind.IMAGE if command.purpose == "watermark" else None),
                check_cancelled=context.cancellation.raise_if_requested,
            )
        return self.completion(
            asset.path,
            outcome=ImportedAssetTaskOutcome(
                asset_id=asset.id,
                document_id=document_id,
                purpose=command.purpose,
            ),
        )

    def proxy(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, GenerateProxyCommand)
        asset = self.documents.catalog.get_asset(command.asset_id)
        sequence_id = context.task.sequence_id or self.documents.catalog.get_project().main_sequence_id
        sequence = self.documents.catalog.get_sequence(sequence_id)
        asset = asset_in_timeline_clock(self.documents.catalog, asset, sequence)
        updated = self.runtime.generate_proxy(
            asset,
            sequence.profile,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        return self.completion(
            updated.proxy_path,
            updated.sdr_preview_proxy_path,
        )

    def waveform(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, GenerateWaveformCommand)
        asset = self.documents.catalog.get_asset(command.asset_id)
        sequence_id = context.task.sequence_id or self.documents.catalog.get_project().main_sequence_id
        sequence = self.documents.catalog.get_sequence(sequence_id)
        asset = asset_in_timeline_clock(self.documents.catalog, asset, sequence)
        updated = self.runtime.generate_waveform(
            asset,
            duration_seconds=asset.metadata.duration_frames / sequence.profile.fps,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        return self.completion(updated.waveform_path)


class DownloadTaskHandler(ProjectTaskHandler):
    def __init__(
        self,
        documents: AssetTaskDocuments,
        assets: AssetService,
        runtime: DownloadTaskRuntime,
        subtitle_acquisition: SubtitleAcquisitionService,
        settings: Callable[[], GlobalSettings],
    ):
        super().__init__(documents.project_dir)
        self.documents = documents
        self.assets = assets
        self.runtime = runtime
        self.subtitle_acquisition = subtitle_acquisition
        self.settings = settings

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, DownloadMediaCommand)
        paths = self.runtime.download_media(
            command.request,
            self.settings().download,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        try:
            return self._register_downloads(
                paths,
                context,
            )
        except BaseException as error:
            try:
                archived = (
                    self.runtime.archive_unrecorded_downloads(
                        [Path(path).resolve() for path in paths]
                    )
                )
            except BaseException as archive_error:
                error.add_note(
                    "下载文件登记失败后无法完整撤回已发布文件："
                    f"{archive_error}"
                )
            else:
                if archived:
                    error.add_note(
                        "未登记的下载文件已移至失败归档："
                        + ", ".join(
                            str(path) for path in archived
                        )
                    )
            raise

    def _register_downloads(
        self,
        paths: list[Path],
        context: TaskContext,
    ) -> TaskCompletion:
        resolved_paths = [Path(path).resolve(strict=True) for path in paths]
        existing = {
            self.documents.catalog.resolve_asset_path(asset).resolve(): asset
            for asset in self.documents.catalog.list_assets()
        }
        prepared: dict[Path, PreparedAssetRegistration] = {}
        candidate_assets = []
        for path in resolved_paths:
            asset = existing.get(path)
            if asset is None:
                plan = prepared.get(path)
                if plan is None:
                    plan = self.assets.prepare_output(
                        path,
                        AssetOrigin.DOWNLOAD,
                    )
                    prepared[path] = plan
                asset = plan.asset
            candidate_assets.append(asset)

        publications = []
        prepared_document_assets: set[str] = set()
        for asset in candidate_assets:
            if (
                asset.id in prepared_document_assets
                or asset.kind != AssetKind.SUBTITLE
                or Path(asset.path).suffix.lower()
                not in {".srt", ".vtt", ".ass", ".ssa"}
            ):
                continue
            prepared_document_assets.add(asset.id)
            _document, publication = (
                self.subtitle_acquisition.prepare_document_publication(
                    asset,
                    available_assets=candidate_assets,
                )
            )
            if publication is not None:
                publications.append(publication)
        context.cancellation.raise_if_requested()

        def commit_assets():
            committed = dict(existing)
            for path, plan in prepared.items():
                committed[path] = self.assets.commit_prepared(plan)
            return [committed[path] for path in resolved_paths]

        assets, _outputs = (
            self.subtitle_acquisition.publication.commit_prepared_documents(
                commit_assets,
                publications,
            )
        )
        return self.completion(*(asset.path for asset in assets))
