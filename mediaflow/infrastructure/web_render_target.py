from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.application.web_package_files import web_package_root
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import Asset
from mediaflow.domain.timebase import source_interval_for_timeline_interval
from mediaflow.domain.timeline import Clip, TimelineState
from mediaflow.domain.web_media_sources import (
    WebMediaSourcesManifest,
    web_media_sources_have_audio,
)
from mediaflow.domain.web_state import web_runtime_state
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_package_storage import read_publication_receipt

from .web_native_media import WebNativeMediaPlan, build_web_native_media_plan

WEB_RENDERER_VERSION = "8"
WEB_CACHE_MANIFEST_SCHEMA = "mediaflow-web-render-cache/v5"


@dataclass(frozen=True, slots=True)
class WebRenderTarget:
    key: str
    segment_namespace: str
    path: Path
    animated: bool
    frame_count: int
    width: int
    height: int
    fps_numerator: int
    fps_denominator: int
    has_audio: bool
    audio_sample_rate: int
    audio_channels: int
    native_media_plan: WebNativeMediaPlan

    @property
    def manifest_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.manifest.json")


class WebRenderCache:
    def __init__(self, documents: TimelineCompilationDocuments, paths: RuntimePaths):
        render_identity = paths.render_identity
        if render_identity is None:
            raise RuntimeError("Web rendering requires a pinned render runtime identity")
        self.documents = documents
        self.paths = paths
        self.render_identity = render_identity

    def target(
        self,
        state: TimelineState,
        clip: Clip,
        asset: Asset | None = None,
    ) -> WebRenderTarget:
        asset = asset or self.documents.assets.get_asset(clip.asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Web render cache only accepts web clips")
        spec = self.documents.web.get_web_asset_spec(asset.id)
        clip_state = state.web_states.get(clip.id)
        if clip_state is None:
            raise ValueError(f"Web clip has no editable state: {clip.id}")
        variant = spec.manifest.variant_for(clip_state.variant.id if clip_state.variant is not None else None)
        animated = spec.manifest.duration_ms > 0 or any(
            scene.animations for scene in clip_state.scenes.values()
        )
        _, source_end = source_interval_for_timeline_interval(
            clip.source_in,
            0,
            clip.duration,
            clip.speed_numerator,
            clip.speed_denominator,
            freeze_source_frame=clip.freeze_source_frame,
        )
        frame_count = max(1, source_end)
        package_root = web_package_root(
            self.documents.assets.resolve_asset_path(asset),
            spec.manifest,
        )
        source_hash = spec.source_hash
        require_committed_web_publication(
            project_dir=self.documents.project_dir,
            package_root=package_root,
            asset_id=asset.id,
            source_hash=source_hash,
        )
        if clip_state.source_hash != source_hash:
            raise RuntimeError(
                "Editable media clip state does not match its immutable package publication; "
                "rebind the package"
            )
        media_sources = WebMediaSourcesManifest.model_validate_json(
            (package_root / spec.manifest.media_sources).read_text(encoding="utf-8")
        )
        if web_media_sources_have_audio(media_sources) != asset.metadata.has_audio:
            raise RuntimeError(
                "Editable media audio metadata no longer matches its media-sources v4 bindings; "
                "reimport the package"
            )
        native_media_plan = build_web_native_media_plan(
            package_root=package_root,
            manifest=spec.manifest,
            media_sources=media_sources,
            clip_state=clip_state,
            target_duration_ms=Fraction(
                frame_count * state.sequence.profile.fps_denominator * 1000,
                state.sequence.profile.fps_numerator,
            ),
        )
        render_state = web_runtime_state(clip_state, spec.manifest)
        render_state.pop("revision", None)
        common_payload = {
            "renderer_version": WEB_RENDERER_VERSION,
            "render_runtime": self.render_identity.model_dump(mode="json"),
            "source_hash": source_hash,
            "state": render_state,
            "sequence": state.sequence.profile.model_dump(mode="json"),
            "variant": {
                "id": variant.id,
                "width": variant.canvas.width,
                "height": variant.canvas.height,
            },
            "audio": {
                "enabled": asset.metadata.has_audio,
                "sample_rate": state.sequence.profile.audio_sample_rate,
                "channels": state.sequence.profile.audio_channels,
            },
        }
        segment_namespace = hashlib.sha256(
            json.dumps(
                common_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        payload = {
            **common_payload,
            "clip_range": {
                "source_in": clip.source_in,
                "duration": clip.duration,
                "speed_numerator": clip.speed_numerator,
                "speed_denominator": clip.speed_denominator,
            },
            "frame_count": frame_count,
            "native_media": native_media_plan.cache_payload(),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        suffix = ".mkv" if animated else ".png"
        return WebRenderTarget(
            key=digest,
            segment_namespace=segment_namespace,
            path=self.paths.project_cache_dir(self.documents.project_dir) / "web" / f"{digest[:32]}{suffix}",
            animated=animated,
            frame_count=frame_count,
            width=variant.canvas.width,
            height=variant.canvas.height,
            fps_numerator=state.sequence.profile.fps_numerator,
            fps_denominator=state.sequence.profile.fps_denominator,
            has_audio=asset.metadata.has_audio,
            audio_sample_rate=state.sequence.profile.audio_sample_rate,
            audio_channels=state.sequence.profile.audio_channels,
            native_media_plan=native_media_plan,
        )


def require_committed_web_publication(
    *,
    project_dir: Path,
    package_root: Path,
    asset_id: str,
    source_hash: str,
) -> None:
    publication_root = project_dir.resolve() / "sources" / "web"
    package_root = package_root.resolve()
    match = re.fullmatch(r"p-([0-9a-f]{24})", package_root.name)
    if package_root.parent != publication_root or match is None:
        raise RuntimeError("Editable media rendering requires an immutable managed publication")
    token = match.group(1)
    receipt = read_publication_receipt(
        publication_root / "receipts" / f"r-{token}.json"
    ).as_dict()
    expected = {
        "schema_version": 1,
        "asset_id": asset_id,
        "source_hash": source_hash,
        "token": token,
        "directory": package_root.name,
        "status": "committed",
    }
    if receipt != expected:
        raise RuntimeError("Editable media publication receipt does not match its immutable package")
