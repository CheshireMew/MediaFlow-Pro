from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING

from mediaflow.domain.frame_clock import (
    AssetFrameClockState,
    MainFrameClockSnapshot,
    SubtitleTrackLinkFrameClockState,
)
from mediaflow.domain.project import Asset, ProjectProfile
from mediaflow.domain.timebase import reframe_frames, reframe_interval
from mediaflow.domain.timeline import TimelineMergeConflict, TimelineState

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import model_json as _model_json

if TYPE_CHECKING:
    from .asset_catalog_repository import AssetCatalogRepository
    from .highlight_repository import HighlightRepository
    from .project_database_session import ProjectDatabaseSession
    from .project_metadata_repository import ProjectMetadataRepository
    from .sequence_catalog_repository import SequenceCatalogRepository
    from .subtitle_repository import SubtitleRepository
    from .timeline_repository import TimelineRepository


class MainFrameClockRepository(ProjectRepositoryComponent):
    """Own the atomic project-wide main frame-clock snapshot and migration."""

    def __init__(
        self,
        database: ProjectDatabaseSession,
        *,
        projects: Callable[[], ProjectMetadataRepository],
        sequences: Callable[[], SequenceCatalogRepository],
        assets: Callable[[], AssetCatalogRepository],
        subtitles: Callable[[], SubtitleRepository],
        highlights: Callable[[], HighlightRepository],
        timeline: Callable[[], TimelineRepository],
    ) -> None:
        super().__init__(database)
        self._projects = projects
        self._sequences = sequences
        self._assets = assets
        self._subtitles = subtitles
        self._highlights = highlights
        self._timeline = timeline

    def capture_main_frame_clock(
        self,
        sequence_id: str,
    ) -> MainFrameClockSnapshot:
        project = self._projects().get_project()
        if sequence_id != project.main_sequence_id:
            raise ValueError("Only the main sequence owns the shared project frame clock")
        timeline = self._timeline().load_timeline(sequence_id)
        timeline.sequence = timeline.sequence.model_copy(update={"timeline_revision": 0})
        assets = [
            AssetFrameClockState(
                asset_id=asset.id,
                metadata=asset.metadata,
                proxy_path=asset.proxy_path,
                sdr_preview_proxy_path=asset.sdr_preview_proxy_path,
            )
            for asset in self._assets().list_assets()
        ]
        segment_rows = self._fetchall("SELECT * FROM subtitle_segment ORDER BY id")
        word_rows = self._fetchall("SELECT * FROM subtitle_word ORDER BY id")
        link_rows = self._fetchall(
            """SELECT link.*
               FROM subtitle_track_document link
               JOIN track ON track.id=link.track_id
               WHERE track.sequence_id=?
               ORDER BY link.track_id, link.document_id""",
            (sequence_id,),
        )
        placement_rows = self._fetchall(
            """SELECT placement.*
               FROM subtitle_placement placement
               JOIN track ON track.id=placement.track_id
               WHERE track.sequence_id=?
               ORDER BY placement.id""",
            (sequence_id,),
        )
        return MainFrameClockSnapshot(
            timeline=timeline,
            assets=assets,
            subtitle_segments=[
                self._subtitles().subtitle_segment_from_row(row)
                for row in segment_rows
            ],
            subtitle_words=[
                self._subtitles().subtitle_word_from_row(row)
                for row in word_rows
            ],
            highlights=sorted(
                self._highlights().list_highlights(),
                key=lambda item: item.id,
            ),
            subtitle_links=[
                SubtitleTrackLinkFrameClockState(
                    track_id=row["track_id"],
                    document_id=row["document_id"],
                    offset_frames=row["offset_frames"],
                    source_start_frame=row["source_start_frame"],
                    source_end_frame=row["source_end_frame"],
                )
                for row in link_rows
            ],
            subtitle_placements=[
                self._subtitles().subtitle_placement_from_row(row)
                for row in placement_rows
            ],
        )

    def change_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        state: TimelineState,
        assets: list[Asset],
        *,
        old_profile: ProjectProfile,
    ) -> MainFrameClockSnapshot:
        project = self._projects().get_project()
        if (
            source.timeline.sequence.id != project.main_sequence_id
            or state.sequence.id != project.main_sequence_id
        ):
            raise ValueError("Only the main sequence owns the shared project frame clock")
        if source.timeline.sequence.profile != old_profile:
            raise ValueError("Frame-clock source profile does not match the captured snapshot")
        stored_asset_ids = {item.id for item in self._assets().list_assets()}
        if {item.id for item in assets} != stored_asset_ids:
            raise ValueError("Frame-clock changes must include every project asset")

        with self.transaction() as connection:
            current = self.capture_main_frame_clock(project.main_sequence_id)
            self._require_snapshot_source(current, source)
            validation_assets = {asset.id: asset for asset in assets}
            current_sequence = self._sequences().get_sequence(project.main_sequence_id)
            state = state.model_copy(
                update={
                    "sequence": state.sequence.model_copy(
                        update={"timeline_revision": current_sequence.timeline_revision}
                    )
                }
            )
            self._timeline().save_frame_clock_timeline(
                state,
                validation_assets=validation_assets,
            )
            self._write_asset_clock_states(
                connection,
                [
                    AssetFrameClockState(
                        asset_id=asset.id,
                        metadata=asset.metadata,
                        proxy_path=asset.proxy_path,
                        sdr_preview_proxy_path=asset.sdr_preview_proxy_path,
                    )
                    for asset in assets
                ],
            )
            if (
                old_profile.fps_numerator != state.sequence.profile.fps_numerator
                or old_profile.fps_denominator != state.sequence.profile.fps_denominator
            ):
                self._reframe_shared_main_clock_records(
                    connection,
                    source,
                    old_profile,
                    state.sequence.profile,
                )
            self._sync_all_subtitle_placements(
                connection,
            )
            self._touch_project(connection)
            return self.capture_main_frame_clock(project.main_sequence_id)

    def restore_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        destination: MainFrameClockSnapshot,
    ) -> MainFrameClockSnapshot:
        project = self._projects().get_project()
        if (
            source.timeline.sequence.id != project.main_sequence_id
            or destination.timeline.sequence.id != project.main_sequence_id
        ):
            raise ValueError("Frame-clock snapshot belongs to another sequence")
        with self.transaction() as connection:
            current = self.capture_main_frame_clock(project.main_sequence_id)
            if current == destination:
                return current
            self._require_snapshot_source(current, source)
            current_assets = {asset.id: asset for asset in self._assets().list_assets()}
            destination_assets = {
                item.asset_id: current_assets[item.asset_id].model_copy(
                    update={
                        "metadata": item.metadata,
                        "proxy_path": item.proxy_path,
                        "sdr_preview_proxy_path": item.sdr_preview_proxy_path,
                    }
                )
                for item in destination.assets
            }
            if set(destination_assets) != set(current_assets):
                raise TimelineMergeConflict(
                    "main frame clock asset set",
                    project.main_sequence_id,
                )
            current_sequence = self._sequences().get_sequence(project.main_sequence_id)
            destination_timeline = destination.timeline.model_copy(
                update={
                    "sequence": destination.timeline.sequence.model_copy(
                        update={"timeline_revision": current_sequence.timeline_revision}
                    )
                }
            )
            self._timeline().save_frame_clock_timeline(
                destination_timeline,
                validation_assets=destination_assets,
            )
            self._write_asset_clock_states(
                connection,
                destination.assets,
            )
            self._write_shared_main_clock_records(
                connection,
                destination,
            )
            self._sync_all_subtitle_placements(
                connection,
                excluded_sequence_ids={project.main_sequence_id},
            )
            self._touch_project(connection)
            return self.capture_main_frame_clock(project.main_sequence_id)

    @staticmethod
    def _require_snapshot_source(
        current: MainFrameClockSnapshot,
        source: MainFrameClockSnapshot,
    ) -> None:
        if current != source:
            raise TimelineMergeConflict(
                "main frame clock snapshot",
                current.timeline.sequence.id,
            )

    def _write_asset_clock_states(
        self,
        connection: sqlite3.Connection,
        assets: list[AssetFrameClockState],
    ) -> None:
        for asset in assets:
            cursor = connection.execute(
                """UPDATE asset SET proxy_path=?, sdr_preview_proxy_path=?,
                   metadata_json=? WHERE id=?""",
                (
                    self._assets().store_optional_path(asset.proxy_path),
                    self._assets().store_optional_path(asset.sdr_preview_proxy_path),
                    _model_json(asset.metadata),
                    asset.asset_id,
                ),
            )
            if cursor.rowcount != 1:
                raise TimelineMergeConflict("asset", asset.asset_id)

    def _sync_all_subtitle_placements(
        self,
        connection: sqlite3.Connection,
        *,
        excluded_sequence_ids: set[str] | None = None,
    ) -> None:
        excluded = excluded_sequence_ids or set()
        sequence_ids = [
            str(row["id"])
            for row in connection.execute("SELECT id FROM sequence ORDER BY position, id").fetchall()
            if str(row["id"]) not in excluded
        ]
        for sequence_id in sequence_ids:
            self._subtitles().sync_subtitle_placements(
                connection,
                sequence_id,
            )

    def _reframe_shared_main_clock_records(
        self,
        connection: sqlite3.Connection,
        source: MainFrameClockSnapshot,
        old_profile: ProjectProfile,
        new_profile: ProjectProfile,
    ) -> None:
        def reframe(value: int) -> int:
            return reframe_frames(value, old_profile, new_profile)

        def reframe_range(start: int, end: int) -> tuple[int, int]:
            return reframe_interval(
                start,
                end,
                old_profile,
                new_profile,
            )

        for segment in source.subtitle_segments:
            start, end = reframe_range(segment.start_frame, segment.end_frame)
            connection.execute(
                "UPDATE subtitle_segment SET start_frame=?, end_frame=? WHERE id=?",
                (start, end, segment.id),
            )
        for word in source.subtitle_words:
            start, end = reframe_range(word.start_frame, word.end_frame)
            connection.execute(
                "UPDATE subtitle_word SET start_frame=?, end_frame=? WHERE id=?",
                (start, end, word.id),
            )
        for candidate in source.highlights:
            start, end = reframe_range(
                candidate.start_frame,
                candidate.end_frame,
            )
            connection.execute(
                "UPDATE highlight_candidate SET start_frame=?, end_frame=? WHERE id=?",
                (start, end, candidate.id),
            )
        for link in source.subtitle_links:
            source_start: int | None
            source_end: int | None
            if link.source_start_frame is not None and link.source_end_frame is not None:
                source_start, source_end = reframe_range(
                    link.source_start_frame,
                    link.source_end_frame,
                )
            else:
                source_start = (
                    reframe(link.source_start_frame) if link.source_start_frame is not None else None
                )
                source_end = reframe(link.source_end_frame) if link.source_end_frame is not None else None
            connection.execute(
                """UPDATE subtitle_track_document
                   SET offset_frames=?, source_start_frame=?, source_end_frame=?
                   WHERE track_id=? AND document_id=?""",
                (
                    reframe(link.offset_frames),
                    source_start,
                    source_end,
                    link.track_id,
                    link.document_id,
                ),
            )
        for placement in source.subtitle_placements:
            if not placement.timing_overridden:
                continue
            start, end = reframe_range(
                placement.start_frame,
                placement.end_frame,
            )
            connection.execute(
                """UPDATE subtitle_placement
                   SET start_frame=?, end_frame=?
                   WHERE id=? AND timing_overridden=1""",
                (start, end, placement.id),
            )

    def _write_shared_main_clock_records(
        self,
        connection: sqlite3.Connection,
        snapshot: MainFrameClockSnapshot,
    ) -> None:
        for segment in snapshot.subtitle_segments:
            connection.execute(
                """UPDATE subtitle_segment
                   SET source_segment_id=?, start_frame=?, end_frame=?, text=?,
                       speaker=?, confidence=? WHERE id=?""",
                (
                    segment.source_segment_id,
                    segment.start_frame,
                    segment.end_frame,
                    segment.text,
                    segment.speaker,
                    segment.confidence,
                    segment.id,
                ),
            )
        for word in snapshot.subtitle_words:
            connection.execute(
                """UPDATE subtitle_word
                   SET segment_id=?, position=?, start_frame=?, end_frame=?,
                       text=?, confidence=?, timing_source=?, excluded=?
                   WHERE id=?""",
                (
                    word.segment_id,
                    word.position,
                    word.start_frame,
                    word.end_frame,
                    word.text,
                    word.confidence,
                    word.timing_source,
                    int(word.excluded),
                    word.id,
                ),
            )
        for candidate in snapshot.highlights:
            connection.execute(
                """UPDATE highlight_candidate
                   SET project_id=?, asset_id=?, document_id=?, sequence_id=?,
                       start_frame=?, end_frame=?, title=?, reason=?, score=?,
                       selected=? WHERE id=?""",
                (
                    candidate.project_id,
                    candidate.asset_id,
                    candidate.document_id,
                    candidate.sequence_id,
                    candidate.start_frame,
                    candidate.end_frame,
                    candidate.title,
                    candidate.reason,
                    candidate.score,
                    int(candidate.selected),
                    candidate.id,
                ),
            )
        for link in snapshot.subtitle_links:
            connection.execute(
                """UPDATE subtitle_track_document
                   SET offset_frames=?, source_start_frame=?, source_end_frame=?
                   WHERE track_id=? AND document_id=?""",
                (
                    link.offset_frames,
                    link.source_start_frame,
                    link.source_end_frame,
                    link.track_id,
                    link.document_id,
                ),
            )
        sequence_id = snapshot.timeline.sequence.id
        connection.execute(
            """DELETE FROM subtitle_placement
               WHERE track_id IN (
                   SELECT id FROM track WHERE sequence_id=?
               )""",
            (sequence_id,),
        )
        for placement in snapshot.subtitle_placements:
            connection.execute(
                """INSERT INTO subtitle_placement(
                       id, track_id, segment_id, clip_id, start_frame, end_frame,
                       text_override, timing_overridden
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    placement.id,
                    placement.track_id,
                    placement.segment_id,
                    placement.clip_id,
                    placement.start_frame,
                    placement.end_frame,
                    placement.text_override,
                    int(placement.timing_overridden),
                ),
            )
