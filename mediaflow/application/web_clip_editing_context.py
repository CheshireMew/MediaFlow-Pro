from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

from mediaflow.application.ports import StructuredFileReader, WebApplicationDocuments
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.project import Asset
from mediaflow.domain.web_manifest import EditableMediaManifest, WebAssetSpec
from mediaflow.domain.web_media_sources import WebMediaSourcesManifest
from mediaflow.domain.web_state import WebClipState


class WebClipEditingContext(Protocol):
    repository: WebApplicationDocuments
    _structured_files: StructuredFileReader

    def _clip_context(
        self,
        sequence_id: str,
        clip_id: str,
        expected_revision: int | None,
    ) -> tuple[TimelineEditor, Asset, WebAssetSpec, WebClipState]: ...

    def _scene_id(
        self,
        state: WebClipState,
        manifest: EditableMediaManifest,
        scene_id: str | None,
    ) -> str: ...

    def _media_sources(
        self,
        package_root: Path,
        manifest: EditableMediaManifest,
    ) -> WebMediaSourcesManifest: ...

    def _save_state(
        self,
        editor: TimelineEditor,
        current: WebClipState,
        candidate: WebClipState,
    ) -> WebClipState: ...


def web_clip_editing_context(service: object) -> WebClipEditingContext:
    return cast(WebClipEditingContext, service)
