from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QUrl

from mediaflow.domain.timebase import reframe_interval

from .base import Projector


class AssetProjector(Projector):
    def refresh_assets(self) -> None:
        if not self._session.binding.current:
            self._session.models.assets.set_items([])
            self._session.models.asset_bins.set_items([])
            self._session.models.asset_moments.set_items([])
            return
        assets = self._session.binding.current.list_assets()
        bins = self._session.binding.current.list_asset_bins()
        available_ids = {asset.id for asset in assets}
        self._session.asset_state.thumbnail_paths = {
            asset_id: path
            for asset_id, path in self._session.asset_state.thumbnail_paths.items()
            if asset_id in available_ids
        }
        transcript_terms: dict[str, list[str]] = {}
        for document in self._session.binding.current.list_subtitle_documents():
            text = " ".join(
                segment.text for segment in self._session.binding.current.list_subtitle_segments(document.id)
            )
            for asset_id in {document.asset_id, document.media_asset_id} - {None}:
                transcript_terms.setdefault(asset_id, []).append(text)
        rows = [
            {
                "assetId": asset.id,
                "name": asset.name,
                "kind": asset.kind.value,
                "path": asset.path,
                "status": asset.status.value,
                "managed": asset.managed,
                "binId": asset.bin_id or "",
                "durationFrames": asset.metadata.duration_frames,
                "width": asset.metadata.width or 0,
                "height": asset.metadata.height or 0,
                "previewUrl": (
                    QUrl.fromLocalFile(self._session.asset_state.thumbnail_paths[asset.id]).toString()
                    if asset.id in self._session.asset_state.thumbnail_paths
                    else ""
                ),
                "proxyReady": bool(asset.proxy_path),
                "waveformReady": bool(asset.waveform_path),
                "searchText": self._asset_search_text(
                    asset,
                    transcript_terms.get(asset.id, []),
                ),
            }
            for asset in assets
        ]
        self._session.models.assets.set_items(rows)
        bins_by_id = {item.id: item for item in bins}

        def bin_depth(item) -> int:
            value = 0
            parent_id = item.parent_id
            visited = {item.id}
            while parent_id and parent_id not in visited and parent_id in bins_by_id:
                visited.add(parent_id)
                value += 1
                parent_id = bins_by_id[parent_id].parent_id
            return value

        self._session.models.asset_bins.set_items(
            [
                {
                    "binId": item.id,
                    "name": item.name,
                    "parentId": item.parent_id or "",
                    "position": item.position,
                    "depth": bin_depth(item),
                    "displayName": "　" * bin_depth(item) + item.name,
                    "assetCount": sum(asset.bin_id == item.id for asset in assets),
                }
                for item in bins
            ]
        )
        self.refresh_asset_moments(assets)
        self._session.projectors.timeline.refresh_timeline()
        for asset in assets:
            self.request_waveform_data(asset.id, asset.waveform_path)
        self._session.selection.asset_ids = [
            asset_id for asset_id in self._session.selection.asset_ids if asset_id in available_ids
        ]
        self.request_asset_thumbnails(assets)

    @staticmethod
    def _asset_search_text(asset, transcript_terms: list[str]) -> str:
        terms = [
            asset.name,
            asset.kind.value,
            asset.metadata.video_codec or "",
            asset.metadata.audio_codec or "",
            f"{asset.metadata.width or 0}x{asset.metadata.height or 0}",
        ]
        if asset.metadata.width and asset.metadata.height:
            terms.append(
                "横屏 landscape" if asset.metadata.width >= asset.metadata.height else "竖屏 portrait"
            )
        terms.extend(transcript_terms)
        return " ".join(term for term in terms if term).casefold()

    def request_asset_thumbnails(self, assets) -> None:
        if not self._session.binding.current or not any(
            asset.status.value == "online" and asset.kind.value in {"video", "image"} for asset in assets
        ):
            return
        if self._session.asset_state.thumbnail_pending_request is not None:
            self._session.asset_state.thumbnail_refresh_requested = True
            return
        self._session.asset_state.thumbnail_request_id += 1
        request_id = (
            self._session.binding.generation,
            self._session.asset_state.thumbnail_request_id,
            str(self._session.binding.current.project_dir),
        )
        project_dir = self._session.binding.current.project_dir
        self._session.asset_state.thumbnail_pending_request = request_id
        self._session.background.submit(
            "asset_thumbnails",
            request_id,
            lambda: self._session._api.asset_thumbnail_paths(project_dir),
        )

    def apply_asset_thumbnails(self, paths: dict[str, str]) -> None:
        self._session.asset_state.thumbnail_paths = dict(paths)
        if not self._session.binding.current:
            return
        assets = self._session.binding.current.list_assets()
        rows = []
        for asset in assets:
            current = self._session.models.assets.get(
                self._session.models.assets.findRow("assetId", asset.id)
            )
            if not current:
                continue
            current["previewUrl"] = (
                QUrl.fromLocalFile(self._session.asset_state.thumbnail_paths[asset.id]).toString()
                if asset.id in self._session.asset_state.thumbnail_paths
                else ""
            )
            rows.append(current)
        self._session.models.assets.set_items(rows)
        self.refresh_asset_moments(assets)

    def refresh_asset_moments(self, assets) -> None:
        if not self._session.binding.current or not self._session.binding.timeline:
            self._session.models.asset_moments.set_items([])
            return
        project = self._session.binding.current.get_project()
        main_profile = self._session.binding.current.get_sequence(
            project.main_sequence_id
        ).profile
        active_profile = self._session.binding.timeline.state.sequence.profile
        assets_by_id = {asset.id: asset for asset in assets}
        rows: list[dict] = []
        for document in self._session.binding.current.list_subtitle_documents():
            asset_id = document.media_asset_id or document.asset_id
            asset = assets_by_id.get(asset_id)
            if asset is None:
                continue
            source_profile = (
                self._session.binding.current.get_sequence(document.sequence_id).profile
                if document.sequence_id else main_profile
            )
            for segment in self._session.binding.current.list_subtitle_segments(document.id):
                start, end = reframe_interval(
                    segment.start_frame,
                    segment.end_frame,
                    source_profile,
                    active_profile,
                )
                rows.append(
                    {
                        "momentId": f"spoken:{segment.id}",
                        "assetId": asset.id,
                        "assetName": asset.name,
                        "momentType": "spoken",
                        "label": segment.text,
                        "detail": "口述内容",
                        "startFrame": start,
                        "endFrame": end,
                        "previewUrl": self._preview_url(asset.id),
                        "searchText": f"{asset.name} {segment.text} spoken speech 口述 台词",
                    }
                )
        for highlight in self._session.binding.current.list_highlights():
            asset = assets_by_id.get(highlight.asset_id)
            if asset is None:
                continue
            source_profile = (
                self._session.binding.current.get_sequence(highlight.sequence_id).profile
                if highlight.sequence_id else main_profile
            )
            start, end = reframe_interval(
                highlight.start_frame,
                highlight.end_frame,
                source_profile,
                active_profile,
            )
            rows.append(
                {
                    "momentId": f"visual:{highlight.id}",
                    "assetId": asset.id,
                    "assetName": asset.name,
                    "momentType": "visual",
                    "label": highlight.title,
                    "detail": highlight.reason or "画面时刻",
                    "startFrame": start,
                    "endFrame": end,
                    "previewUrl": self._preview_url(asset.id),
                    "searchText": (
                        f"{asset.name} {highlight.title} {highlight.reason} "
                        "visual moment 画面 时刻"
                    ),
                }
            )
        rows.sort(key=lambda row: (row["assetName"], row["startFrame"], row["momentId"]))
        self._session.models.asset_moments.set_items(rows)

    def _preview_url(self, asset_id: str) -> str:
        path = self._session.asset_state.thumbnail_paths.get(asset_id)
        return QUrl.fromLocalFile(path).toString() if path else ""

    def request_waveform_data(self, asset_id: str, waveform_path: str | None) -> None:
        if not waveform_path or not self._session.binding.current:
            self._session.asset_state.waveform_cache.pop(asset_id, None)
            return
        path = Path(waveform_path)
        if not path.is_absolute():
            path = self._session.binding.current.project_dir / path
        path_value = str(path.resolve())
        cached = self._session.asset_state.waveform_cache.get(asset_id)
        if cached and cached[0] == path_value:
            return
        request_id = (self._session.binding.generation, asset_id, path_value)
        if request_id in self._session.asset_state.waveform_pending:
            return
        self._session.asset_state.waveform_pending.add(request_id)

        def load() -> tuple[int, dict]:
            modified = path.stat().st_mtime_ns
            return modified, json.loads(path.read_text(encoding="utf-8"))

        self._session.background.submit("waveform", request_id, load)
