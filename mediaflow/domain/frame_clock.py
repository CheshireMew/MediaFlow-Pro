from __future__ import annotations

from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.project import MediaMetadata
from mediaflow.domain.subtitles import SubtitlePlacement, SubtitleSegment, SubtitleWord
from mediaflow.domain.timeline import TimelineState


class AssetFrameClockState(DomainModel):
    asset_id: str
    metadata: MediaMetadata
    proxy_path: str | None = None
    sdr_preview_proxy_path: str | None = None


class SubtitleTrackLinkFrameClockState(DomainModel):
    track_id: str
    document_id: str
    offset_frames: int
    source_start_frame: int | None = None
    source_end_frame: int | None = None


class MainFrameClockSnapshot(DomainModel):
    timeline: TimelineState
    assets: list[AssetFrameClockState]
    subtitle_segments: list[SubtitleSegment]
    subtitle_words: list[SubtitleWord]
    highlights: list[HighlightCandidate]
    subtitle_links: list[SubtitleTrackLinkFrameClockState]
    subtitle_placements: list[SubtitlePlacement]
