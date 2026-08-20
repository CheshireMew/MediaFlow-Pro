from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.ports import (
    StructuredFileReader,
    WebApplicationDocuments,
    WebPackageValidatorPort,
)
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_batch_service import WebBatchService
from mediaflow.application.web_clip_editing_service import WebClipEditingService
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.application.web_package_storage import WebPackageStorage
from mediaflow.application.web_rebind_service import WebRebindService
from mediaflow.domain.editable_media_contract import EditableMediaContract


class WebMediaServices:
    """The four focused editable-media application boundaries."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        timeline: Callable[[str], TimelineEditor],
        runtime_validator: WebPackageValidatorPort,
        structured_files: StructuredFileReader,
        package_storage: WebPackageStorage,
        contract: EditableMediaContract,
    ) -> None:
        self.packages = WebPackageService(
            repository,
            runtime_validator,
            package_storage,
            contract,
        )
        self.clips = WebClipEditingService(
            repository,
            timeline,
            self.packages,
            structured_files,
        )
        self.batches = WebBatchService(
            repository,
            timeline,
            self.clips,
            structured_files,
        )
        self.rebind = WebRebindService(
            repository,
            timeline,
            runtime_validator,
            self.packages,
            self.clips,
        )
