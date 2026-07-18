from __future__ import annotations

import sqlite3
from bisect import bisect_left, bisect_right
from fractions import Fraction
from typing import Any

from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.subtitles import (
    SubtitleDocument,
    SubtitlePlacement,
    SubtitleSegment,
)
from mediaflow.domain.timebase import reframe_rate


class SubtitleRepository:
    def create_subtitle_document(
        self,
        document: SubtitleDocument,
        segments: list[SubtitleSegment],
    ) -> SubtitleDocument:
        project = self.get_project()
        if document.project_id != project.id:
            raise ValueError("Subtitle document belongs to another project")
        self.get_asset(document.asset_id)
        if document.media_asset_id:
            media_asset = self.get_asset(document.media_asset_id)
            if media_asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
                raise ValueError("Subtitle media must be a video or audio asset")
        if any(segment.document_id != document.id for segment in segments):
            raise ValueError("Subtitle segment belongs to another document")
        if document.source_document_id:
            source = self.get_subtitle_document(document.source_document_id)
            if source.asset_id != document.asset_id or source.media_asset_id != document.media_asset_id:
                raise ValueError("Translation source must keep the same source and media assets")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO subtitle_document(
                    id, project_id, asset_id, media_asset_id, language,
                    source_document_id, is_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document.id,
                    document.project_id,
                    document.asset_id,
                    document.media_asset_id,
                    document.language,
                    document.source_document_id,
                    int(document.is_source),
                    document.created_at,
                ),
            )
            for segment in segments:
                self._insert_subtitle_segment(connection, segment)
            self._touch_project(connection)
        return document

    def get_subtitle_document(self, document_id: str) -> SubtitleDocument:
        row = self._fetchone("SELECT * FROM subtitle_document WHERE id=?", (document_id,))
        if row is None:
            raise KeyError(document_id)
        return self._subtitle_document_from_row(row)

    def list_subtitle_documents(self, asset_id: str | None = None) -> list[SubtitleDocument]:
        if asset_id:
            rows = self._fetchall(
                """SELECT * FROM subtitle_document
                   WHERE asset_id=? OR media_asset_id=?
                   ORDER BY created_at, id""",
                (asset_id, asset_id),
            )
        else:
            rows = self._fetchall("SELECT * FROM subtitle_document ORDER BY created_at, id")
        return [self._subtitle_document_from_row(row) for row in rows]

    def list_subtitle_segments(self, document_id: str) -> list[SubtitleSegment]:
        rows = self._fetchall(
            "SELECT * FROM subtitle_segment WHERE document_id=? ORDER BY start_frame, id",
            (document_id,),
        )
        return [self._subtitle_segment_from_row(row) for row in rows]

    def save_subtitle_segments(
        self,
        document_id: str,
        segments: list[SubtitleSegment],
    ) -> None:
        self.get_subtitle_document(document_id)
        if any(segment.document_id != document_id for segment in segments):
            raise ValueError("Subtitle segment belongs to another document")
        segment_ids = [segment.id for segment in segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("Subtitle segment ids must be unique within a document")
        with self.transaction() as connection:
            if segment_ids:
                placeholders = ",".join("?" for _ in segment_ids)
                conflicts = connection.execute(
                    f"SELECT id, document_id FROM subtitle_segment WHERE id IN ({placeholders})",
                    tuple(segment_ids),
                ).fetchall()
                if any(row["document_id"] != document_id for row in conflicts):
                    raise ValueError("Subtitle segment id belongs to another document")
            existing_ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM subtitle_segment WHERE document_id=?",
                    (document_id,),
                ).fetchall()
            }
            for segment in segments:
                connection.execute(
                    """INSERT INTO subtitle_segment(
                           id, document_id, source_segment_id, start_frame, end_frame,
                           text, speaker, confidence
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           source_segment_id=excluded.source_segment_id,
                           start_frame=excluded.start_frame,
                           end_frame=excluded.end_frame,
                           text=excluded.text,
                           speaker=excluded.speaker,
                           confidence=excluded.confidence""",
                    (
                        segment.id,
                        segment.document_id,
                        segment.source_segment_id,
                        segment.start_frame,
                        segment.end_frame,
                        segment.text,
                        segment.speaker,
                        segment.confidence,
                    ),
                )
            removed_ids = existing_ids.difference(segment_ids)
            if removed_ids:
                placeholders = ",".join("?" for _ in removed_ids)
                connection.execute(
                    f"UPDATE subtitle_segment SET source_segment_id=NULL "
                    f"WHERE source_segment_id IN ({placeholders})",
                    tuple(sorted(removed_ids)),
                )
                connection.execute(
                    f"DELETE FROM subtitle_segment WHERE document_id=? AND id IN ({placeholders})",
                    (document_id, *sorted(removed_ids)),
                )
            sequence_ids = [
                row["sequence_id"]
                for row in connection.execute(
                    """SELECT DISTINCT track.sequence_id
                       FROM subtitle_track_document link
                       JOIN track ON track.id=link.track_id
                       WHERE link.document_id=?""",
                    (document_id,),
                ).fetchall()
            ]
            for sequence_id in sequence_ids:
                self._sync_subtitle_placements(connection, sequence_id)
            self._touch_project(connection)

    def place_subtitle_document(
        self,
        document_id: str,
        track_id: str,
        *,
        offset_frames: int = 0,
        source_start_frame: int | None = None,
        source_end_frame: int | None = None,
        follow_clips: bool | None = None,
    ) -> list[SubtitlePlacement]:
        track_row = self._fetchone("SELECT * FROM track WHERE id=?", (track_id,))
        if track_row is None:
            raise KeyError(track_id)
        if track_row["kind"] != TrackKind.SUBTITLE.value:
            raise ValueError("Subtitle documents can only be placed on subtitle tracks")
        self.get_subtitle_document(document_id)
        if follow_clips is None:
            matching = self._fetchone(
                """SELECT clip.id
                   FROM clip
                   JOIN track clip_track ON clip_track.id=clip.track_id
                   JOIN subtitle_document document ON document.id=?
                   WHERE clip_track.sequence_id=? AND clip.asset_id=COALESCE(
                       document.media_asset_id,
                       document.asset_id
                   )
                   LIMIT 1""",
                (document_id, track_row["sequence_id"]),
            )
            follow_clips = matching is not None
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO subtitle_track_document(
                       track_id, document_id, follow_clips, offset_frames,
                       source_start_frame, source_end_frame
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id, document_id) DO UPDATE SET
                       follow_clips=excluded.follow_clips,
                       offset_frames=excluded.offset_frames,
                       source_start_frame=excluded.source_start_frame,
                       source_end_frame=excluded.source_end_frame""",
                (
                    track_id,
                    document_id,
                    int(follow_clips),
                    offset_frames,
                    source_start_frame,
                    source_end_frame,
                ),
            )
            self._sync_subtitle_placements(connection, track_row["sequence_id"])
            self._touch_project(connection)
        segment_ids = {item.id for item in self.list_subtitle_segments(document_id)}
        return [item for item in self.list_subtitle_placements(track_id) if item.segment_id in segment_ids]

    def list_subtitle_placements(self, track_id: str) -> list[SubtitlePlacement]:
        rows = self._fetchall(
            "SELECT * FROM subtitle_placement WHERE track_id=? ORDER BY start_frame, id",
            (track_id,),
        )
        return [
            SubtitlePlacement(
                id=row["id"],
                track_id=row["track_id"],
                segment_id=row["segment_id"],
                clip_id=row["clip_id"],
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                text_override=row["text_override"],
            )
            for row in rows
        ]

    def update_subtitle_placement_text(
        self,
        placement_id: str,
        text_override: str | None,
    ) -> SubtitlePlacement:
        row = self._fetchone("SELECT * FROM subtitle_placement WHERE id=?", (placement_id,))
        if row is None:
            raise KeyError(placement_id)
        normalized = None if text_override is None else text_override.strip()
        if normalized == "":
            raise ValueError("Subtitle text cannot be empty")
        with self.transaction() as connection:
            connection.execute(
                "UPDATE subtitle_placement SET text_override=? WHERE id=?",
                (normalized, placement_id),
            )
            self._touch_project(connection)
        return next(
            item for item in self.list_subtitle_placements(row["track_id"]) if item.id == placement_id
        )

    def add_subtitle_placements(
        self,
        placements: list[SubtitlePlacement],
    ) -> list[SubtitlePlacement]:
        if not placements:
            return []
        track_ids = {item.track_id for item in placements}
        rows = self._fetchall(
            f"SELECT id, kind FROM track WHERE id IN ({','.join('?' for _ in track_ids)})",
            tuple(track_ids),
        )
        if len(rows) != len(track_ids) or any(row["kind"] != TrackKind.SUBTITLE.value for row in rows):
            raise ValueError("Subtitle placements require subtitle tracks")
        with self.transaction() as connection:
            for item in placements:
                connection.execute(
                    """INSERT INTO subtitle_placement(
                           id, track_id, segment_id, clip_id, start_frame, end_frame,
                           text_override
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        item.track_id,
                        item.segment_id,
                        item.clip_id,
                        item.start_frame,
                        item.end_frame,
                        item.text_override,
                    ),
                )
            self._touch_project(connection)
        by_track = {
            item.id: item for track_id in track_ids for item in self.list_subtitle_placements(track_id)
        }
        return [by_track[item.id] for item in placements]

    def apply_subtitle_placement_to_document(
        self,
        placement_id: str,
        text: str,
    ) -> SubtitleSegment:
        value = text.strip()
        if not value:
            raise ValueError("Subtitle text cannot be empty")
        row = self._fetchone("SELECT * FROM subtitle_placement WHERE id=?", (placement_id,))
        if row is None:
            raise KeyError(placement_id)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE subtitle_segment SET text=? WHERE id=?",
                (value, row["segment_id"]),
            )
            connection.execute(
                "UPDATE subtitle_placement SET text_override=NULL WHERE id=?",
                (placement_id,),
            )
            self._touch_project(connection)
        segment_row = self._fetchone("SELECT * FROM subtitle_segment WHERE id=?", (row["segment_id"],))
        if segment_row is None:
            raise RuntimeError("Subtitle segment disappeared during update")
        return self._subtitle_segment_from_row(segment_row)

    @staticmethod
    def _round_fraction(value: Fraction) -> int:
        quotient, remainder = divmod(value.numerator, value.denominator)
        return quotient + (1 if remainder * 2 >= value.denominator else 0)

    def _sync_subtitle_placements(
        self,
        connection: sqlite3.Connection,
        sequence_id: str,
        *,
        clip_ids: set[str] | None = None,
    ) -> None:
        """Compile track/document links into clip-aware, editable placements."""
        project_row = connection.execute("SELECT main_sequence_id FROM project LIMIT 1").fetchone()
        if project_row is None:
            return
        main_profile = connection.execute(
            "SELECT fps_numerator, fps_denominator FROM sequence WHERE id=?",
            (project_row["main_sequence_id"],),
        ).fetchone()
        target_profile = connection.execute(
            "SELECT fps_numerator, fps_denominator FROM sequence WHERE id=?",
            (sequence_id,),
        ).fetchone()
        if main_profile is None or target_profile is None:
            return

        links = connection.execute(
            """SELECT link.*, COALESCE(
                       document.media_asset_id,
                       document.asset_id
                   ) AS media_asset_id
               FROM subtitle_track_document link
               JOIN track ON track.id=link.track_id
               JOIN subtitle_document document ON document.id=link.document_id
               WHERE track.sequence_id=?""",
            (sequence_id,),
        ).fetchall()
        for link in links:
            if clip_ids is not None and not bool(link["follow_clips"]):
                continue
            clips = []
            if bool(link["follow_clips"]):
                clip_filter = ""
                parameters: list[Any] = [sequence_id, link["media_asset_id"]]
                if clip_ids is not None:
                    placeholders = ",".join("?" for _ in clip_ids)
                    clip_filter = f" AND clip.id IN ({placeholders})"
                    parameters.extend(sorted(clip_ids))
                clips = connection.execute(
                    """SELECT clip.*
                       FROM clip
                       JOIN track ON track.id=clip.track_id
                       WHERE track.sequence_id=? AND clip.asset_id=?"""
                    + clip_filter
                    + " ORDER BY clip.timeline_start, clip.id",
                    parameters,
                ).fetchall()
                if clip_ids is not None and not clips:
                    continue

            segment_sql = "SELECT * FROM subtitle_segment WHERE document_id=?"
            segment_parameters: list[Any] = [link["document_id"]]
            if clip_ids is not None and clips:
                target_to_main = Fraction(
                    main_profile["fps_numerator"] * target_profile["fps_denominator"],
                    main_profile["fps_denominator"] * target_profile["fps_numerator"],
                )
                ranges = [self._clip_source_range(clip) for clip in clips]
                main_start = min(item[0] for item in ranges) * target_to_main
                main_end = max(item[1] for item in ranges) * target_to_main
                lower = main_start.numerator // main_start.denominator - 1
                upper = -(-main_end.numerator // main_end.denominator) + 1
                segment_sql += " AND end_frame>? AND start_frame<?"
                segment_parameters.extend([lower, upper])
            segments = connection.execute(
                segment_sql + " ORDER BY start_frame, id",
                segment_parameters,
            ).fetchall()

            converted_segments: list[tuple[sqlite3.Row, int, int]] = []
            for segment in segments:
                source_start = reframe_rate(
                    segment["start_frame"],
                    main_profile["fps_numerator"],
                    main_profile["fps_denominator"],
                    target_profile["fps_numerator"],
                    target_profile["fps_denominator"],
                )
                source_end = reframe_rate(
                    segment["end_frame"],
                    main_profile["fps_numerator"],
                    main_profile["fps_denominator"],
                    target_profile["fps_numerator"],
                    target_profile["fps_denominator"],
                )
                converted_segments.append((segment, source_start, source_end))

            desired: dict[tuple[str, str | None], tuple[int, int]] = {}
            if bool(link["follow_clips"]):
                starts = [item[1] for item in converted_segments]
                maximum_end = -1
                prefix_maximum_ends: list[int] = []
                for _, _, source_end in converted_segments:
                    maximum_end = max(maximum_end, source_end)
                    prefix_maximum_ends.append(maximum_end)
                for clip in clips:
                    clip_start, clip_end = self._clip_source_range(clip)
                    first = bisect_right(prefix_maximum_ends, clip_start)
                    last = bisect_left(starts, clip_end)
                    for segment, source_start, source_end in converted_segments[first:last]:
                        mapped = self._map_segment_to_clip(source_start, source_end, clip)
                        if mapped is not None:
                            desired[(segment["id"], clip["id"])] = mapped
            else:
                for segment, source_start, source_end in converted_segments:
                    start = source_start
                    end = source_end
                    if link["source_start_frame"] is not None:
                        if end <= link["source_start_frame"]:
                            continue
                        start = max(start, link["source_start_frame"])
                    if link["source_end_frame"] is not None:
                        if start >= link["source_end_frame"]:
                            continue
                        end = min(end, link["source_end_frame"])
                    start += link["offset_frames"]
                    end += link["offset_frames"]
                    if end > 0:
                        desired[(segment["id"], None)] = (max(0, start), max(1, end))

            placement_filter = ""
            placement_parameters: list[Any] = [link["track_id"], link["document_id"]]
            if clip_ids is not None:
                placeholders = ",".join("?" for _ in clip_ids)
                placement_filter = f" AND placement.clip_id IN ({placeholders})"
                placement_parameters.extend(sorted(clip_ids))
            existing_rows = connection.execute(
                """SELECT placement.*
                   FROM subtitle_placement placement
                   JOIN subtitle_segment segment ON segment.id=placement.segment_id
                   WHERE placement.track_id=? AND segment.document_id=?"""
                + placement_filter
                + " ORDER BY placement.id",
                placement_parameters,
            ).fetchall()
            existing: dict[tuple[str, str | None], sqlite3.Row] = {}
            duplicate_ids: list[str] = []
            for row in existing_rows:
                key = (row["segment_id"], row["clip_id"])
                if key in existing:
                    duplicate_ids.append(row["id"])
                else:
                    existing[key] = row
            stale_ids = duplicate_ids + [row["id"] for key, row in existing.items() if key not in desired]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                connection.execute(
                    f"DELETE FROM subtitle_placement WHERE id IN ({placeholders})",
                    stale_ids,
                )
            for key, (start, end) in desired.items():
                row = existing.get(key)
                if row is not None:
                    if row["start_frame"] != start or row["end_frame"] != end:
                        connection.execute(
                            "UPDATE subtitle_placement SET start_frame=?, end_frame=? WHERE id=?",
                            (start, end, row["id"]),
                        )
                else:
                    connection.execute(
                        """INSERT INTO subtitle_placement(
                               id, track_id, segment_id, clip_id,
                               start_frame, end_frame, text_override
                           ) VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                        (new_id(), link["track_id"], key[0], key[1], start, end),
                    )

    @staticmethod
    def _clip_source_range(clip: sqlite3.Row) -> tuple[Fraction, Fraction]:
        speed = Fraction(abs(clip["speed_numerator"]), clip["speed_denominator"])
        source_in = Fraction(clip["source_in"])
        consumed = Fraction(clip["duration"]) * speed
        if clip["speed_numerator"] > 0:
            return source_in, source_in + consumed
        return source_in - consumed, source_in

    def _map_segment_to_clip(
        self,
        segment_start: int,
        segment_end: int,
        clip: sqlite3.Row,
    ) -> tuple[int, int] | None:
        speed = Fraction(abs(clip["speed_numerator"]), clip["speed_denominator"])
        source_in = Fraction(clip["source_in"])
        consumed = Fraction(clip["duration"]) * speed
        if clip["speed_numerator"] > 0:
            start = max(Fraction(segment_start), source_in)
            end = min(Fraction(segment_end), source_in + consumed)
            if end <= start:
                return None
            timeline_start = clip["timeline_start"] + self._round_fraction((start - source_in) / speed)
            timeline_end = clip["timeline_start"] + self._round_fraction((end - source_in) / speed)
        else:
            start = max(Fraction(segment_start), source_in - consumed)
            end = min(Fraction(segment_end), source_in)
            if end <= start:
                return None
            timeline_start = clip["timeline_start"] + self._round_fraction((source_in - end) / speed)
            timeline_end = clip["timeline_start"] + self._round_fraction((source_in - start) / speed)
        timeline_start = max(
            clip["timeline_start"],
            min(clip["timeline_start"] + clip["duration"] - 1, timeline_start),
        )
        timeline_end = max(
            timeline_start + 1,
            min(clip["timeline_start"] + clip["duration"], timeline_end),
        )
        return timeline_start, timeline_end

    @staticmethod
    def _insert_subtitle_segment(
        connection: sqlite3.Connection,
        segment: SubtitleSegment,
    ) -> None:
        connection.execute(
            """INSERT INTO subtitle_segment(
                id, document_id, source_segment_id, start_frame, end_frame,
                text, speaker, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                segment.id,
                segment.document_id,
                segment.source_segment_id,
                segment.start_frame,
                segment.end_frame,
                segment.text,
                segment.speaker,
                segment.confidence,
            ),
        )

    @staticmethod
    def _subtitle_document_from_row(row: sqlite3.Row) -> SubtitleDocument:
        return SubtitleDocument(
            id=row["id"],
            project_id=row["project_id"],
            asset_id=row["asset_id"],
            media_asset_id=row["media_asset_id"],
            language=row["language"],
            source_document_id=row["source_document_id"],
            is_source=bool(row["is_source"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _subtitle_segment_from_row(row: sqlite3.Row) -> SubtitleSegment:
        return SubtitleSegment(
            id=row["id"],
            document_id=row["document_id"],
            source_segment_id=row["source_segment_id"],
            start_frame=row["start_frame"],
            end_frame=row["end_frame"],
            text=row["text"],
            speaker=row["speaker"],
            confidence=row["confidence"],
        )
