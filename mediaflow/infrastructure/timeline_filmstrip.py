from __future__ import annotations

import hashlib
import math
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
        assets = {asset.id: asset for asset in self.repository.catalog.list_assets()}
        video_tracks = {
            track.id for track in state.tracks if track.kind == TrackKind.VIDEO and track.enabled
        }
        step = max(1, math.ceil(FILMSTRIP_TILE_WIDTH / pixels_per_frame))
        prefetch = step
        expanded_start = max(0, start - prefetch)
        expanded_end = end + prefetch
        web = WebRenderService(self.repository, self.paths)
        values: list[FilmstripFrame] = []
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
                    source_identity = (
                        f"web:{web_target.key}:{FILMSTRIP_RENDERER_VERSION}"
                    )
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
                tile_source_frame = source_frame
                tile_source_identity = source_identity
                tile_source_path = source_path
                if asset.kind == AssetKind.WEB and not web_full_cache_ready:
                    assert web_target is not None
                    tile_source_path = web.render_filmstrip_source(
                        state,
                        clip.id,
                        source_frame,
                        check_cancelled=check_cancelled,
                    )
                    tile_source_frame = 0
                    tile_source_identity = (
                        f"web:{web_target.key}:{source_frame}:"
                        f"{FILMSTRIP_RENDERER_VERSION}"
                    )
                assert tile_source_path is not None
                path = self._render_tile(
                    tile_source_path,
                    source_identity=tile_source_identity,
                    source_frame=tile_source_frame,
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
        CacheManager(self.cache_root).prune_directory_to_size(
            "filmstrip",
            maximum_bytes=FILMSTRIP_DISK_LIMIT_BYTES,
        )
        return [item.document() for item in values]

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
        cached = _MEMORY_CACHE.get(key)
        if cached is not None:
            return cached
        destination = self.cache_root / "filmstrip" / key[:2] / f"{key}.jpg"
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
        original = self.repository.catalog.resolve_asset_path(asset)
        candidates: list[Path] = []
        if asset.kind == AssetKind.VIDEO:
            for value in (asset.sdr_preview_proxy_path, asset.proxy_path):
                if not value:
                    continue
                path = Path(value)
                candidates.append(
                    (self.repository.project_dir / path).resolve()
                    if not path.is_absolute()
                    else path.resolve(),
                )
        candidates.append(original)
        return next((path for path in candidates if path.is_file()), None)
