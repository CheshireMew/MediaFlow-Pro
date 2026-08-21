from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from mediaflow.atomic_file import native_temporary_sibling
from mediaflow.infrastructure.font_assets import apply_bundled_font_environment
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable_streaming

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_RENDERER_VERSION = "timeline-proof-frame-v1"


class TimelineProofFrameService:
    """Render evidence images from the same compiled MLT graph used by preview."""

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def render(
        self,
        project_dir: str | Path,
        graph_path: str | Path,
        frames: list[int],
        *,
        expected_width: int,
        expected_height: int,
    ) -> list[dict[str, object]]:
        graph = Path(graph_path).resolve()
        if not graph.is_file():
            raise FileNotFoundError(graph)
        if self.paths.melt is None or not self.paths.melt.is_file():
            raise FileNotFoundError("MLT melt runtime is not installed")
        graph_payload = graph.read_bytes()
        render_identity = (
            self.paths.render_identity.model_dump_json()
            if self.paths.render_identity is not None
            else str(self.paths.melt)
        )
        cache_root = self.paths.project_cache_dir(project_dir) / "proof-frames"
        results: list[dict[str, object]] = []
        for frame in frames:
            digest = hashlib.sha256()
            digest.update(_RENDERER_VERSION.encode("utf-8"))
            digest.update(render_identity.encode("utf-8"))
            digest.update(graph_payload)
            digest.update(str(frame).encode("ascii"))
            key = digest.hexdigest()
            destination = cache_root / key[:2] / f"{key}.png"
            if not destination.is_file() or destination.stat().st_size == 0:
                self._render_one(graph, frame, destination)
            payload = destination.read_bytes()
            width, height = self._png_dimensions(payload)
            if (width, height) != (expected_width, expected_height):
                raise RuntimeError(
                    "Rendered proof frame has the wrong dimensions: "
                    f"expected {expected_width}x{expected_height}, got {width}x{height}"
                )
            results.append(
                {
                    "frame": frame,
                    "path": str(destination),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "width": width,
                    "height": height,
                    "byte_count": len(payload),
                }
            )
        return results

    def _render_one(self, graph: Path, frame: int, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = native_temporary_sibling(destination, label="proof-frame")
        environment = self.paths.mlt_environment()
        apply_bundled_font_environment(None, environment)
        command = [
            str(self.paths.melt),
            str(graph),
            f"in={frame}",
            f"out={frame}",
            "-consumer",
            f"avformat:{temporary}",
            "f=image2",
            "vcodec=png",
            "pix_fmt=rgba",
            "update=1",
            "real_time=-1",
            "terminate_on_pause=1",
        ]
        result = run_cancellable_streaming(
            command,
            cwd=self.paths.require_mlt_root(),
            env=environment,
            timeout=120,
        )
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            diagnostic = "\n".join(
                part for part in (result.stdout, result.stderr) if part
            ).strip()
            raise RuntimeError(
                f"Timeline proof frame render failed at frame {frame}"
                + (f":\n{diagnostic}" if diagnostic else "")
            )
        self._png_dimensions(temporary.read_bytes())
        temporary.replace(destination)

    @staticmethod
    def _png_dimensions(payload: bytes) -> tuple[int, int]:
        if len(payload) < 24 or payload[:8] != _PNG_SIGNATURE or payload[12:16] != b"IHDR":
            raise RuntimeError("Timeline proof frame renderer returned an invalid PNG")
        return struct.unpack(">II", payload[16:24])
