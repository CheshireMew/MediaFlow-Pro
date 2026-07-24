from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mediaflow.domain.audio import AudioBus
from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    AssetStatus,
    ColorMode,
    SequenceKind,
    TrackKind,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.model_base import new_id, now_ms
from mediaflow.domain.project import (
    Asset,
    AssetFingerprint,
    MediaMetadata,
    Project,
    ProjectProfile,
    Sequence,
    SequenceInOut,
)
from mediaflow.domain.timeline import (
    Clip,
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    TimelineMarker,
    TimelineRange,
    Track,
    Transition,
)

from .audio_repository import AudioRepository
from .highlight_repository import HighlightRepository
from .project_catalog_repository import ProjectCatalogRepository
from .project_lock import ProjectWriteLock
from .project_records_repository import ProjectRecordsRepository
from .project_schema import (
    MANAGED_DIRECTORIES,
    PROJECT_FILE_NAME,
    PROJECT_SCHEMA_VERSION,
    SCHEMA_SQL,
    ProjectSchemaMigrator,
)
from .project_serialization import json_value as _json
from .project_serialization import model_json as _model_json
from .subtitle_repository import SubtitleRepository
from .timeline_repository import TimelineRepository
from .web_media_repository import WebMediaRepository


class ProjectRepository(
    ProjectCatalogRepository,
    TimelineRepository,
    AudioRepository,
    SubtitleRepository,
    HighlightRepository,
    WebMediaRepository,
    ProjectRecordsRepository,
):
    def __init__(
        self,
        project_dir: Path,
        connection: sqlite3.Connection,
        *,
        read_only: bool,
        write_lock: ProjectWriteLock | None,
    ):
        self.project_dir = project_dir
        self.database_path = project_dir / PROJECT_FILE_NAME
        self._connection = connection
        self._connection_lock = threading.RLock()
        self._transaction_depth = 0
        self.read_only = read_only
        self._write_lock = write_lock
        self._known_content_revision: int | None = None

    @classmethod
    def create(
        cls,
        project_dir: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> ProjectRepository:
        root = Path(project_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        database_path = root / PROJECT_FILE_NAME
        if database_path.exists():
            raise FileExistsError(f"Project already exists: {database_path}")
        for directory in MANAGED_DIRECTORIES:
            (root / directory).mkdir(exist_ok=True)

        lock = ProjectWriteLock(root / "cache" / "project.lock")
        if not lock.acquire():
            raise RuntimeError(f"Project directory is already locked: {root}")
        try:
            connection = cls._connect(database_path, read_only=False)
            repository = cls(root, connection, read_only=False, write_lock=lock)
            repository._initialize(
                name=name,
                profile=profile or ProjectProfile(),
                profile_confirmed=profile is not None,
            )
            repository.acknowledge_content_revision()
            return repository
        except Exception:
            lock.release()
            raise

    @classmethod
    def open(
        cls,
        project_dir: str | Path,
        *,
        writable: bool = True,
        cooperative: bool = False,
    ) -> ProjectRepository:
        root = Path(project_dir).resolve(strict=True)
        database_path = root / PROJECT_FILE_NAME
        if not database_path.is_file():
            raise FileNotFoundError(database_path)
        if cooperative and not writable:
            raise ValueError("Cooperative project access is only meaningful for writable opens")

        lock: ProjectWriteLock | None = None
        read_only = not writable
        if writable and not cooperative:
            candidate = ProjectWriteLock(root / "cache" / "project.lock")
            if candidate.acquire():
                lock = candidate
            else:
                read_only = True
        connection = cls._connect(database_path, read_only=read_only)
        repository = cls(root, connection, read_only=read_only, write_lock=lock)
        ProjectSchemaMigrator(repository).validate()
        repository.acknowledge_content_revision()
        if not read_only:
            for directory in MANAGED_DIRECTORIES:
                (root / directory).mkdir(exist_ok=True)
        return repository

    @property
    def owns_project_lock(self) -> bool:
        return self._write_lock is not None

    @property
    def known_content_revision(self) -> int:
        if self._known_content_revision is None:
            return self.acknowledge_content_revision()
        return self._known_content_revision

    def content_revision(self) -> int:
        row = self._fetchone("SELECT content_revision FROM project LIMIT 1")
        if row is None:
            raise RuntimeError("Project record is missing")
        return int(row["content_revision"])

    def acknowledge_content_revision(self) -> int:
        revision = self.content_revision()
        self._known_content_revision = revision
        return revision

    @staticmethod
    def _connect(database_path: Path, *, read_only: bool) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"file:{database_path.as_posix()}?mode=ro",
                uri=True,
                timeout=5.0,
                check_same_thread=False,
            )
        else:
            connection = sqlite3.connect(database_path, timeout=5.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(
        self,
        *,
        name: str,
        profile: ProjectProfile,
        profile_confirmed: bool,
    ) -> None:
        project_id = new_id()
        main_sequence_id = new_id()
        created_at = now_ms()
        project = Project(
            id=project_id,
            name=name,
            root_path=str(self.project_dir),
            main_sequence_id=main_sequence_id,
            created_at=created_at,
            updated_at=created_at,
        )
        sequence = Sequence(
            id=main_sequence_id,
            project_id=project_id,
            name="主序列",
            kind=SequenceKind.MAIN,
            profile=profile,
            profile_confirmed=profile_confirmed,
            created_at=created_at,
        )
        master_bus = AudioBus(sequence_id=main_sequence_id, name="主总线", position=0)
        dialogue_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="对白",
            parent_bus_id=master_bus.id,
            position=1,
        )
        music_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="音乐",
            parent_bus_id=master_bus.id,
            position=2,
        )
        effects_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="效果",
            parent_bus_id=master_bus.id,
            position=3,
        )
        with self.transaction() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT INTO schema_info(component, version) VALUES('project', ?)",
                (PROJECT_SCHEMA_VERSION,),
            )
            connection.execute(
                """INSERT INTO project(
                    id, name, root_path, main_sequence_id, workflow_auto_continue,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project.id,
                    project.name,
                    project.root_path,
                    project.main_sequence_id,
                    -1 if project.workflow_auto_continue is None else int(project.workflow_auto_continue),
                    project.created_at,
                    project.updated_at,
                ),
            )
            self._insert_sequence(connection, sequence)
            for bus in (master_bus, dialogue_bus, music_bus, effects_bus):
                self._insert_audio_bus(connection, bus)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise PermissionError("Project is open read-only")
        with self._connection_lock:
            if self._transaction_depth:
                self._transaction_depth += 1
                try:
                    yield self._connection
                finally:
                    self._transaction_depth -= 1
                return
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._transaction_depth = 1
                if self._known_content_revision is not None:
                    current_revision = self.content_revision()
                    if current_revision != self._known_content_revision:
                        raise RuntimeError(
                            "Project content changed in another process; reload before editing"
                        )
                yield self._connection
                self._connection.commit()
                self._known_content_revision = self._content_revision_if_available()
            except Exception:
                self._connection.rollback()
                raise
            finally:
                self._transaction_depth = 0

    def close(self) -> None:
        with self._connection_lock:
            self._connection.close()
        if self._write_lock is not None:
            self._write_lock.release()
            self._write_lock = None

    def __enter__(self) -> ProjectRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _stored_path(self, path: str, *, managed: bool) -> str:
        candidate = Path(path)
        resolved = (
            (self.project_dir / candidate).resolve()
            if managed and not candidate.is_absolute()
            else candidate.resolve()
        )
        if not managed:
            return str(resolved)
        try:
            relative = resolved.relative_to(self.project_dir)
        except ValueError as error:
            raise ValueError("Managed asset must be inside the project directory") from error
        return relative.as_posix()

    def _stored_optional_path(self, path: str | None) -> str | None:
        if not path:
            return None
        candidate = Path(path)
        resolved = (
            (self.project_dir / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        )
        try:
            return resolved.relative_to(self.project_dir).as_posix()
        except ValueError:
            return str(resolved)

    def _asset_from_row(self, row: sqlite3.Row) -> Asset:
        fingerprint = (
            AssetFingerprint.model_validate_json(row["fingerprint_json"]) if row["fingerprint_json"] else None
        )
        return Asset(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            kind=AssetKind(row["kind"]),
            origin=AssetOrigin(row["origin"]),
            path=row["path"],
            managed=bool(row["managed"]),
            proxy_path=row["proxy_path"],
            sdr_preview_proxy_path=row["sdr_preview_proxy_path"],
            waveform_path=row["waveform_path"],
            status=AssetStatus(row["status"]),
            fingerprint=fingerprint,
            metadata=MediaMetadata.model_validate_json(row["metadata_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _insert_sequence(connection: sqlite3.Connection, sequence: Sequence) -> None:
        profile = sequence.profile
        connection.execute(
            """INSERT INTO sequence(
                id, project_id, name, kind, position, width, height,
                fps_numerator, fps_denominator, color_mode, bit_depth,
                audio_sample_rate, audio_channels, profile_confirmed,
                in_frame, out_frame, archived, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sequence.id,
                sequence.project_id,
                sequence.name,
                sequence.kind.value,
                sequence.position,
                profile.width,
                profile.height,
                profile.fps_numerator,
                profile.fps_denominator,
                profile.color_mode.value,
                profile.bit_depth,
                profile.audio_sample_rate,
                profile.audio_channels,
                int(sequence.profile_confirmed),
                sequence.in_out.in_frame if sequence.in_out else None,
                sequence.in_out.out_frame if sequence.in_out else None,
                int(sequence.archived),
                sequence.created_at,
            ),
        )

    @staticmethod
    def _update_sequence(connection: sqlite3.Connection, sequence: Sequence) -> None:
        profile = sequence.profile
        connection.execute(
            """UPDATE sequence SET
                name=?, kind=?, position=?, width=?, height=?, fps_numerator=?,
                fps_denominator=?, color_mode=?, bit_depth=?, audio_sample_rate=?,
                audio_channels=?, profile_confirmed=?, in_frame=?, out_frame=?,
                archived=? WHERE id=?""",
            (
                sequence.name,
                sequence.kind.value,
                sequence.position,
                profile.width,
                profile.height,
                profile.fps_numerator,
                profile.fps_denominator,
                profile.color_mode.value,
                profile.bit_depth,
                profile.audio_sample_rate,
                profile.audio_channels,
                int(sequence.profile_confirmed),
                sequence.in_out.in_frame if sequence.in_out else None,
                sequence.in_out.out_frame if sequence.in_out else None,
                int(sequence.archived),
                sequence.id,
            ),
        )

    @staticmethod
    def _sequence_from_row(row: sqlite3.Row, preset_json: str | None = None) -> Sequence:
        return Sequence(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            kind=SequenceKind(row["kind"]),
            position=row["position"],
            export_preset=ExportPreset.model_validate_json(preset_json) if preset_json else None,
            in_out=(
                SequenceInOut(in_frame=row["in_frame"], out_frame=row["out_frame"])
                if row["in_frame"] is not None and row["out_frame"] is not None
                else None
            ),
            archived=bool(row["archived"]),
            profile_confirmed=bool(row["profile_confirmed"]),
            profile=ProjectProfile(
                width=row["width"],
                height=row["height"],
                fps_numerator=row["fps_numerator"],
                fps_denominator=row["fps_denominator"],
                color_mode=ColorMode(row["color_mode"]),
                bit_depth=row["bit_depth"],
                audio_sample_rate=row["audio_sample_rate"],
                audio_channels=row["audio_channels"],
            ),
            created_at=row["created_at"],
        )

    @staticmethod
    def _store_sequence_export_preset(
        connection: sqlite3.Connection,
        sequence: Sequence,
    ) -> None:
        if sequence.export_preset is None:
            connection.execute(
                "DELETE FROM sequence_export_setting WHERE sequence_id=?",
                (sequence.id,),
            )
            return
        connection.execute(
            """INSERT INTO sequence_export_setting(sequence_id, preset_json)
               VALUES (?, ?)
               ON CONFLICT(sequence_id) DO UPDATE SET preset_json=excluded.preset_json""",
            (sequence.id, _model_json(sequence.export_preset)),
        )

    @staticmethod
    def _upsert_timeline_marker(
        connection: sqlite3.Connection,
        marker: TimelineMarker,
    ) -> None:
        connection.execute(
            """INSERT INTO timeline_marker(id, sequence_id, frame, name, color)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET frame=excluded.frame,
                   name=excluded.name, color=excluded.color""",
            (marker.id, marker.sequence_id, marker.frame, marker.name, marker.color),
        )

    @staticmethod
    def _upsert_timeline_range(
        connection: sqlite3.Connection,
        item: TimelineRange,
    ) -> None:
        connection.execute(
            """INSERT INTO timeline_range(
                   id, sequence_id, start_frame, end_frame, name, color
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET start_frame=excluded.start_frame,
                   end_frame=excluded.end_frame, name=excluded.name, color=excluded.color""",
            (
                item.id,
                item.sequence_id,
                item.start_frame,
                item.end_frame,
                item.name,
                item.color,
            ),
        )

    @staticmethod
    def _insert_audio_bus(connection: sqlite3.Connection, bus: AudioBus) -> None:
        connection.execute(
            """INSERT INTO audio_bus(
                id, sequence_id, name, parent_bus_id, position, gain_db,
                muted, solo, channel_layout
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bus.id,
                bus.sequence_id,
                bus.name,
                bus.parent_bus_id,
                bus.position,
                bus.gain_db,
                int(bus.muted),
                int(bus.solo),
                bus.channel_layout,
            ),
        )

    @staticmethod
    def _insert_track(connection: sqlite3.Connection, track: Track) -> None:
        connection.execute(
            """INSERT INTO track(
                id, sequence_id, name, kind, position, enabled, locked,
                muted, solo, audio_bus_id, linked_audio_track_id, primary_dialogue
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                track.id,
                track.sequence_id,
                track.name,
                track.kind.value,
                track.position,
                int(track.enabled),
                int(track.locked),
                int(track.muted),
                int(track.solo),
                track.audio_bus_id,
                track.linked_audio_track_id,
                int(track.primary_dialogue),
            ),
        )

    @staticmethod
    def _upsert_track(connection: sqlite3.Connection, track: Track) -> None:
        connection.execute(
            """INSERT INTO track(
                id, sequence_id, name, kind, position, enabled, locked,
                muted, solo, audio_bus_id, linked_audio_track_id, primary_dialogue
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, kind=excluded.kind, position=excluded.position,
                enabled=excluded.enabled, locked=excluded.locked, muted=excluded.muted,
                solo=excluded.solo, audio_bus_id=excluded.audio_bus_id,
                linked_audio_track_id=excluded.linked_audio_track_id,
                primary_dialogue=excluded.primary_dialogue""",
            (
                track.id,
                track.sequence_id,
                track.name,
                track.kind.value,
                track.position,
                int(track.enabled),
                int(track.locked),
                int(track.muted),
                int(track.solo),
                track.audio_bus_id,
                track.linked_audio_track_id,
                int(track.primary_dialogue),
            ),
        )

    @staticmethod
    def _track_from_row(row: sqlite3.Row) -> Track:
        return Track(
            id=row["id"],
            sequence_id=row["sequence_id"],
            name=row["name"],
            kind=TrackKind(row["kind"]),
            position=row["position"],
            enabled=bool(row["enabled"]),
            locked=bool(row["locked"]),
            muted=bool(row["muted"]),
            solo=bool(row["solo"]),
            audio_bus_id=row["audio_bus_id"],
            linked_audio_track_id=row["linked_audio_track_id"],
            primary_dialogue=bool(row["primary_dialogue"]),
        )

    @staticmethod
    def _upsert_clip(connection: sqlite3.Connection, clip: Clip) -> None:
        connection.execute(
            """INSERT INTO clip(
                id, track_id, asset_id, timeline_start, source_in, duration, media_kind,
                speed_numerator, speed_denominator, pitch_compensation,
                transform_json, transform_keyframes_json, audio_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id, asset_id=excluded.asset_id,
                timeline_start=excluded.timeline_start, source_in=excluded.source_in,
                duration=excluded.duration, media_kind=excluded.media_kind,
                speed_numerator=excluded.speed_numerator,
                speed_denominator=excluded.speed_denominator,
                pitch_compensation=excluded.pitch_compensation,
                transform_json=excluded.transform_json,
                transform_keyframes_json=excluded.transform_keyframes_json,
                audio_json=excluded.audio_json""",
            (
                clip.id,
                clip.track_id,
                clip.asset_id,
                clip.timeline_start,
                clip.source_in,
                clip.duration,
                clip.media_kind.value,
                clip.speed_numerator,
                clip.speed_denominator,
                int(clip.pitch_compensation),
                _model_json(clip.transform),
                _json([item.model_dump(mode="json") for item in clip.transform_keyframes]),
                _model_json(clip.audio),
            ),
        )

    @staticmethod
    def _clip_from_row(row: sqlite3.Row) -> Clip:
        return Clip(
            id=row["id"],
            track_id=row["track_id"],
            asset_id=row["asset_id"],
            timeline_start=row["timeline_start"],
            source_in=row["source_in"],
            duration=row["duration"],
            media_kind=row["media_kind"],
            speed_numerator=row["speed_numerator"],
            speed_denominator=row["speed_denominator"],
            pitch_compensation=bool(row["pitch_compensation"]),
            transform=ClipTransform.model_validate_json(row["transform_json"]),
            transform_keyframes=[
                ClipTransformKeyframe.model_validate(item)
                for item in json.loads(row["transform_keyframes_json"])
            ],
            audio=ClipAudio.model_validate_json(row["audio_json"]),
        )

    @staticmethod
    def _upsert_transition(connection: sqlite3.Connection, transition: Transition) -> None:
        connection.execute(
            """INSERT INTO transition(
                id, track_id, left_clip_id, right_clip_id, kind, duration, parameters_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id, left_clip_id=excluded.left_clip_id,
                right_clip_id=excluded.right_clip_id, kind=excluded.kind,
                duration=excluded.duration, parameters_json=excluded.parameters_json""",
            (
                transition.id,
                transition.track_id,
                transition.left_clip_id,
                transition.right_clip_id,
                transition.kind.value,
                transition.duration,
                _json(transition.parameters),
            ),
        )

    @staticmethod
    def _transition_from_row(row: sqlite3.Row) -> Transition:
        return Transition(
            id=row["id"],
            track_id=row["track_id"],
            left_clip_id=row["left_clip_id"],
            right_clip_id=row["right_clip_id"],
            kind=row["kind"],
            duration=row["duration"],
            parameters=json.loads(row["parameters_json"]),
        )

    def _ids_for_sequence_transitions(self, sequence_id: str) -> set[str]:
        rows = self._fetchall(
            """SELECT transition.id FROM transition
            JOIN track ON track.id=transition.track_id
            WHERE track.sequence_id=?""",
            (sequence_id,),
        )
        return {row["id"] for row in rows}

    def _fetchone(self, sql: str, parameters: tuple | list = ()) -> sqlite3.Row | None:
        with self._connection_lock:
            return self._connection.execute(sql, parameters).fetchone()

    def _fetchall(self, sql: str, parameters: tuple | list = ()) -> list[sqlite3.Row]:
        with self._connection_lock:
            return self._connection.execute(sql, parameters).fetchall()

    def _content_revision_if_available(self) -> int | None:
        try:
            row = self._connection.execute(
                "SELECT content_revision FROM project LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError as error:
            if "content_revision" in str(error) or "no such table" in str(error):
                return None
            raise
        return int(row["content_revision"]) if row is not None else None

    @staticmethod
    def _delete_missing(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        existing_ids: set[str],
        retained_ids: set[str],
    ) -> None:
        for item_id in existing_ids - retained_ids:
            connection.execute(f"DELETE FROM {table} WHERE {column}=?", (item_id,))

    @staticmethod
    def _touch_project(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE project SET updated_at=?, content_revision=content_revision+1",
            (now_ms(),),
        )
