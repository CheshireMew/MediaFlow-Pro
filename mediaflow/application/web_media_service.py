from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.ports import WebApplicationDocuments, WebPackageValidatorPort
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_batch_service import WebBatchService
from mediaflow.application.web_clip_editing_service import WebClipEditingService
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.application.web_rebind_service import WebRebindService


class WebMediaServices:
    """The four focused editable-media application boundaries."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        timeline: Callable[[str], TimelineEditor],
        runtime_validator: WebPackageValidatorPort,
    ) -> None:
        self.packages = WebPackageService(repository, runtime_validator)
        self.clips = WebClipEditingService(repository, timeline, self.packages)
        self.batches = WebBatchService(repository, timeline, self.clips)
        self.rebind = WebRebindService(
            repository,
            timeline,
            runtime_validator,
            self.packages,
            self.clips,
        )
