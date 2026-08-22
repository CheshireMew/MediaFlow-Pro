from __future__ import annotations

import hashlib
import math
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from mediaflow.atomic_file import native_temporary_sibling
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.timebase import source_frame_at_timeline_offset
from mediaflow.infrastructure.cache_manager import CacheManager
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.visual_source_resolver import resolve_visual_source
from mediaflow.infrastructure.web_render_service import WebRenderService

FILMSTRIP_RENDERER_VERSION = "1"
FILMSTRIP_TILE_WIDTH = 78
FILMSTRIP_MEMORY_LIMIT_BYTES = 64 * 1024 * 1024
FILMSTRIP_DISK_LIMIT_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FilmstripFrame:
    clip_id: str
    timeline_frame: int
    source_frame: int
    path: Path

    def document(self) -> dict[str, object]:
        return {
            "clipId": self.clip_id,
            "timelineFrame": self.timeline_frame,
            "sourceFrame": self.source_frame,
            "path": str(self.path),
        }


class _FilmstripMemoryLru:
    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        self._items: OrderedDict[str, tuple[Path, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> Path | None:
        with self._lock:
            item = self._items.pop(key, None)
            if item is None:
                return None
            path, size = item
            if not path.is_file():
                self._bytes -= size
                return None
            self._items[key] = item
            return path

    def put(self, key: str, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            self._items[key] = (path, size)
            self._bytes += size
            while self._bytes > self.maximum_bytes and self._items:
                _old_key, (_old_path, old_size) = self._items.popitem(last=False)
                self._bytes -= old_size


_MEMORY_CACHE = _FilmstripMemoryLru(FILMSTRIP_MEMORY_LIMIT_BYTES)


class _FilmstripRequestCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: dict[tuple[str, str], int] = {}

    @contextmanager
    def request(
        self,
        key: tuple[str, str],
        generation: int,
    ) -> Iterator[Callable[[], None]]:
        with self._lock:
            current = self._current.get(key)
            if current is None or generation > current:
                self._current[key] = generation

        def check_cancelled() -> None:
            with self._lock:
                current = self._current.get(key)
            if current != generation:
                raise CancelledError("Timeline filmstrip request was superseded")

        yield check_cancelled

    def cancel(self, key: tuple[str, str], generation: int) -> None:
        with self._lock:
            current = self._current.get(key)
            if current is None or generation > current:
                self._current[key] = generation


FILMSTRIP_REQUESTS = _FilmstripRequestCoordinator()


class TimelineFilmstripService:
    """Render visible timeline samples through the editor's existing media paths."""

    def __init__(self, repository: ProjectRepository, paths: RuntimePaths) -> None:
        self.repository = repository
        self.paths = paths
        self.ffmpeg = FfmpegRunner(paths.ffmpeg)
        self.cache_root = paths.project_cache_dir(repository.project_dir)

    def render_visible(
        self,
        sequence_id: str,
        *,
        visible_start_frame: int,
        visible_end_frame: int,
        pixels_per_frame: float,
        height: int,
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[dict[str, object]]:
        if pixels_per_frame <= 0 or height <= 0:
            raise ValueError("Filmstrip scale and height must be positive")
        start = max(0, int(visible_start_frame))
        end = max(start + 1, int(visible_end_frame))
        state = self.repository.timeline.load_timeline(sequence_id)
        assets = {asset.id: asset for asset in self.repository.assets.list_assets()}
        video_tracks = {track.id for track in state.tracks if track.kind == TrackKind.VIDEO and track.enabled}
        step = max(1, math.ceil(FILMSTRIP_TILE_WIDTH / pixels_per_frame))
        prefetch = step
        expanded_start = max(0, start - prefetch)
        expanded_end = end + prefetch
        web = WebRenderService(self.repository, self.paths)
        values: list[FilmstripFrame] = []
        occupied_short_clip_buckets: set[tuple[str, int]] = set()
        for clip in state.clips:
            if check_cancelled is not None:
                check_cancelled()
            if (
                clip.track_id not in video_tracks
                or clip.timeline_end <= expanded_start
                or clip.timeline_start >= expanded_end
            ):
                continue
            asset = assets[clip.asset_id]
            if asset.kind not in {AssetKind.VIDEO, AssetKind.IMAGE, AssetKind.WEB}:
                continue
            if clip.duration < step:
                bucket = (
                    clip.track_id,
                    max(0, clip.timeline_start - expanded_start) // step,
                )
                if bucket in occupied_short_clip_buckets:
                    continue
                occupied_short_clip_buckets.add(bucket)
            local_start = max(0, expanded_start - clip.timeline_start)
            local_end = min(clip.duration, expanded_end - clip.timeline_start)
            first = local_start - (local_start % step)
            source_path: Path | None = None
            source_identity = ""
            web_full_cache_ready = False
            web_target = None
            if asset.kind == AssetKind.WEB:
                web_target = web.cache.target(state, clip, asset)
                web_full_cache_ready = WebRenderService._cache_is_ready(web_target)
                if web_full_cache_ready:
                    source_path = web_target.path
                    source_identity = f"web:{web_target.key}:{FILMSTRIP_RENDERER_VERSION}"
            else:
                source_path = self._visual_source(asset)
                if source_path is None:
                    continue
                source_stat = source_path.stat()
                source_identity = ":".join(
                    (
                        asset.fingerprint.edge_sha256 if asset.fingerprint else "",
                        str(source_path),
                        str(source_stat.st_size),
                        str(source_stat.st_mtime_ns),
                        FILMSTRIP_RENDERER_VERSION,
                    )
                )
            samples: list[tuple[int, int]] = []
            for local_frame in range(first, local_end, step):
                if check_cancelled is not None:
                    check_cancelled()
                if local_frame < 0 or local_frame >= clip.duration:
                    continue
                source_frame = (
                    0
                    if asset.kind == AssetKind.IMAGE
                    else source_frame_at_timeline_offset(
                        clip.source_in,
                        local_frame,
                        clip.speed_numerator,
                        clip.speed_denominator,
                        freeze_source_frame=clip.freeze_source_frame,
                    )
                )
                samples.append((local_frame, source_frame))

            def append_batch(
                batch: list[tuple[int, int]],
                batch_source: Path,
                batch_identity: str,
                *,
                clip_id: str = clip.id,
                clip_start: int = clip.timeline_start,
            ) -> None:
                paths = self._render_tiles(
                    batch_source,
                    source_identity=batch_identity,
                    source_frames=[source_frame for _local, source_frame in batch],
                    fps_numerator=state.sequence.profile.fps_numerator,
                    fps_denominator=state.sequence.profile.fps_denominator,
                    width=FILMSTRIP_TILE_WIDTH,
                    height=height,
                    check_cancelled=check_cancelled,
                )
                for local_frame, source_frame in batch:
                    values.append(
                        FilmstripFrame(
                            clip_id=clip_id,
                            timeline_frame=clip_start + local_frame,
                            source_frame=source_frame,
                            path=paths[source_frame],
                        )
                    )

            if asset.kind != AssetKind.WEB or web_full_cache_ready:
                assert source_path is not None
                append_batch(samples, source_path, source_identity)
                continue

            assert web_target is not None
            for sample_index, (local_frame, source_frame) in enumerate(samples):
                if check_cancelled is not None:
                    check_cancelled()
                if WebRenderService._cache_is_ready(web_target):
                    append_batch(
                        samples[sample_index:],
                        web_target.path,
                        f"web:{web_target.key}:{FILMSTRIP_RENDERER_VERSION}",
                    )
                    break
                tile_source_path = web.render_filmstrip_source(
                    state,
                    clip.id,
                    source_frame,
                    check_cancelled=check_cancelled,
                )
                if WebRenderService._cache_is_ready(web_target):
                    append_batch(
                        samples[sample_index:],
                        web_target.path,
                        f"web:{web_target.key}:{FILMSTRIP_RENDERER_VERSION}",
                    )
                    break
                path = self._render_tile(
                    tile_source_path,
                    source_identity=(
                        f"web:{web_target.key}:{source_frame}:"
                        f"{FILMSTRIP_RENDERER_VERSION}"
                    ),
                    source_frame=0,
                    fps_numerator=state.sequence.profile.fps_numerator,
                    fps_denominator=state.sequence.profile.fps_denominator,
                    width=FILMSTRIP_TILE_WIDTH,
                    height=height,
                    check_cancelled=check_cancelled,
                )
                values.append(
                    FilmstripFrame(
                        clip_id=clip.id,
                        timeline_frame=clip.timeline_start + local_frame,
                        source_frame=source_frame,
                        path=path,
                    )
                )
        CacheManager(self.cache_root).prune_directory_to_size_throttled(
            "filmstrip",
            maximum_bytes=FILMSTRIP_DISK_LIMIT_BYTES,
        )
        return [item.document() for item in values]

    def _render_tiles(
        self,
        source: Path,
        *,
        source_identity: str,
        source_frames: list[int],
        fps_numerator: int,
        fps_denominator: int,
        width: int,
        height: int,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[int, Path]:
        unique_frames = list(dict.fromkeys(source_frames))
        if len(unique_frames) <= 1:
            return {
                frame: self._render_tile(
                    source,
                    source_identity=source_identity,
                    source_frame=frame,
                    fps_numerator=fps_numerator,
                    fps_denominator=fps_denominator,
                    width=width,
                    height=height,
                    check_cancelled=check_cancelled,
                )
                for frame in unique_frames
            }
        destinations = {
            frame: self._tile_destination(
                source_identity=source_identity,
                source_frame=frame,
                fps_numerator=fps_numerator,
                fps_denominator=fps_denominator,
                width=width,
                height=height,
            )
            for frame in unique_frames
        }
        missing = []
        for frame, (key, destination) in destinations.items():
            cached = _MEMORY_CACHE.get(key)
            if cached is not None:
                destinations[frame] = (key, cached)
            elif destination.is_file() and destination.stat().st_size > 0:
                destination.touch()
                _MEMORY_CACHE.put(key, destination)
            else:
                missing.append(frame)
        if len(missing) == 1:
            frame = missing[0]
            key, _destination = destinations[frame]
            path = self._render_tile(
                source,
                source_identity=source_identity,
                source_frame=frame,
                fps_numerator=fps_numerator,
                fps_denominator=fps_denominator,
                width=width,
                height=height,
                check_cancelled=check_cancelled,
            )
            destinations[frame] = (key, path)
        elif missing:
            cache_directory = self.cache_root / "filmstrip"
            cache_directory.mkdir(parents=True, exist_ok=True)
            first_frame = min(missing)
            relative_frames = [frame - first_frame for frame in sorted(missing)]
            selection = "+".join(f"eq(n\\,{frame})" for frame in relative_frames)
            with tempfile.TemporaryDirectory(
                prefix=".mf-filmstrip-batch-",
                dir=cache_directory,
            ) as temporary_value:
                temporary = Path(temporary_value)
                pattern = temporary / "%06d.jpg"
                result = self.ffmpeg.run(
                    [
                        "-y",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{first_frame * fps_denominator / fps_numerator:.9f}",
                        "-i",
                        str(source),
                        "-map",
                        "0:v:0",
                        "-an",
                        "-vf",
                        (
                            f"fps={fps_numerator}/{fps_denominator},"
                            f"select='{selection}',"
                            f"scale={width}:{height}:"
                            "force_original_aspect_ratio=increase,"
                            f"crop={width}:{height},setsar=1"
                        ),
                        "-fps_mode",
                        "vfr",
                        "-frames:v",
                        str(len(missing)),
                        "-q:v",
                        "4",
                        str(pattern),
                    ],
                    check_cancelled=check_cancelled,
                    timeout=60,
                )
                generated = sorted(temporary.glob("*.jpg"))
                completed: set[int] = set()
                if result.returncode == 0 and len(generated) == len(missing):
                    for frame, generated_path in zip(
                        sorted(missing),
                        generated,
                        strict=True,
                    ):
                        key, destination = destinations[frame]
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        generated_path.replace(destination)
                        _MEMORY_CACHE.put(key, destination)
                        completed.add(frame)
            for frame in missing:
                if frame in completed:
                    continue
                key, _destination = destinations[frame]
                path = self._render_tile(
                    source,
                    source_identity=source_identity,
                    source_frame=frame,
                    fps_numerator=fps_numerator,
                    fps_denominator=fps_denominator,
                    width=width,
                    height=height,
                    check_cancelled=check_cancelled,
                )
                destinations[frame] = (key, path)
        return {frame: destination for frame, (_key, destination) in destinations.items()}

    def _tile_destination(
        self,
        *,
        source_identity: str,
        source_frame: int,
        fps_numerator: int,
        fps_denominator: int,
        width: int,
        height: int,
    ) -> tuple[str, Path]:
        key = hashlib.sha256(
            "|".join(
                (
                    source_identity,
                    str(source_frame),
                    str(fps_numerator),
                    str(fps_denominator),
                    str(width),
                    str(height),
                )
            ).encode("utf-8")
        ).hexdigest()
        return key, self.cache_root / "filmstrip" / key[:2] / f"{key}.jpg"

    def _render_tile(
        self,
        source: Path,
        *,
        source_identity: str,
        source_frame: int,
        fps_numerator: int,
        fps_denominator: int,
        width: int,
        height: int,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path:
        key, destination = self._tile_destination(
            source_identity=source_identity,
            source_frame=source_frame,
            fps_numerator=fps_numerator,
            fps_denominator=fps_denominator,
            width=width,
            height=height,
        )
        cached = _MEMORY_CACHE.get(key)
        if cached is not None:
            return cached
        if destination.is_file() and destination.stat().st_size > 0:
            destination.touch()
            _MEMORY_CACHE.put(key, destination)
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = native_temporary_sibling(destination, label="filmstrip")
        seconds = source_frame * fps_denominator / fps_numerator
        try:
            result = self.ffmpeg.run(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{seconds:.9f}",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-an",
                    "-vf",
                    (
                        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},setsar=1"
                    ),
                    "-q:v",
                    "4",
                    str(temporary),
                ],
                check_cancelled=check_cancelled,
                timeout=30,
            )
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError(f"Filmstrip frame extraction failed: {result.stderr}")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        _MEMORY_CACHE.put(key, destination)
        return destination

    def _visual_source(self, asset) -> Path | None:
        return resolve_visual_source(self.repository, asset, prefer="proxy")
