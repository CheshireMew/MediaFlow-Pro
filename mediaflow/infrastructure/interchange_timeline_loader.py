from __future__ import annotations

from pathlib import Path

from mediaflow.domain.interchange import LoadedInterchangeTimeline
from mediaflow.domain.project import ProjectProfile
from mediaflow.infrastructure.edl_timeline_reader import EdlTimelineReader
from mediaflow.infrastructure.fcpxml_timeline_reader import FcpxmlTimelineReader


class InterchangeTimelineLoader:
    """Dispatch supported interchange formats into their focused readers."""

    def load(
        self,
        path: str | Path,
        *,
        default_profile: ProjectProfile,
        frame_rate: float | None = None,
        media_mappings: dict[str, str] | None = None,
    ) -> LoadedInterchangeTimeline:
        source = Path(path).expanduser().resolve(strict=True)
        suffix = source.suffix.casefold()
        if suffix in {".fcpxml", ".xml"}:
            if frame_rate is not None:
                raise ValueError("FCPXML 已包含精确帧率，不能再指定 EDL 帧率")
            return FcpxmlTimelineReader().load(source, media_mappings or {})
        if suffix == ".edl":
            return EdlTimelineReader().load(
                source,
                default_profile=default_profile,
                frame_rate=frame_rate,
                media_mappings=media_mappings or {},
            )
        raise ValueError("只支持 .fcpxml、.xml 和 CMX 3600 .edl 时间线")
