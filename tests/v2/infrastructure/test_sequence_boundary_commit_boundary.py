from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import MediaMetadata
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.mlt.boundary_service import SequenceBoundaryAnalysisService
from mediaflow.infrastructure.mlt.compiler import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths


class _CancellationRequested(RuntimeError):
    pass


class _DeterministicBoundaryAnalysisService(SequenceBoundaryAnalysisService):
    def _detect_edge_black(
        self,
        state: TimelineState,
        graph_path: Path,
        cache_dir: Path,
        *,
        edge: str,
        check_cancelled: Callable[[], None] | None,
        progress: Callable[[OperationProgress], None] | None,
    ) -> int:
        del graph_path, cache_dir, progress
        if check_cancelled is not None:
            check_cancelled()
        return 4 if edge == "leading" else state.duration_frames - 6


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    unused_tool = tmp_path / "unused-media-tool.exe"
    unused_tool.write_bytes(b"not executed by this deterministic test")
    return RuntimePaths(
        runtime_dir=tmp_path / "runtime",
        ffmpeg=unused_tool,
        ffprobe=unused_tool,
        melt=unused_tool,
    )


def _create_timeline(repository: ProjectRepository, source: Path) -> TimelineState:
    source.write_bytes(b"deterministic boundary commit fixture")
    asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
    repository.assets.update_asset(
        asset.model_copy(
            update={
                "metadata": MediaMetadata(
                    duration_frames=100,
                    width=320,
                    height=180,
                    fps_numerator=30,
                    fps_denominator=1,
                    has_video=True,
                )
            }
        )
    )
    sequence_id = repository.projects.get_project().main_sequence_id
    editor = TimelineEditor(repository, sequence_id)
    video_track = editor.add_track(TrackKind.VIDEO)
    editor.add_clip(
        track_id=video_track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=100,
    )
    return editor.state


def test_sequence_boundary_cancels_before_publication_then_publishes_readable_result(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "Boundary Commit Project"
    paths = _runtime_paths(tmp_path)

    with ProjectRepository.create(project_dir, "Boundary Commit Project") as repository:
        state = _create_timeline(repository, tmp_path / "boundary-source.mp4")
        service = _DeterministicBoundaryAnalysisService(
            TimelineCompiler(repository, paths), paths
        )
        expected_snapshot_hash = service.snapshot_hash(state)
        cancellation_requested = False

        def request_cancellation(update: OperationProgress) -> None:
            nonlocal cancellation_requested
            if update.message_code == "sequence_boundary_saving":
                cancellation_requested = True

        def check_cancelled() -> None:
            if cancellation_requested:
                raise _CancellationRequested("cancelled before boundary result publication")

        with pytest.raises(_CancellationRequested, match="before boundary result publication"):
            service.analyze(
                state,
                expected_snapshot_hash=expected_snapshot_hash,
                check_cancelled=check_cancelled,
                progress=request_cancellation,
            )

        assert cancellation_requested is True
        assert not list((project_dir / "generated" / "a").glob("*.json"))
        assert repository.timeline.load_timeline(state.sequence.id) == state

    with ProjectRepository.open(project_dir) as reopened:
        persisted_state = reopened.timeline.load_timeline(state.sequence.id)
        assert persisted_state == state
        compiler = TimelineCompiler(reopened, RuntimeContext.discover().paths)
        compiled = compiler.compile(persisted_state)
        assert compiled.duration_frames == persisted_state.duration_frames

        service = _DeterministicBoundaryAnalysisService(compiler, paths)
        analysis, artifact = service.analyze(
            persisted_state,
            expected_snapshot_hash=service.snapshot_hash(persisted_state),
        )

        assert artifact.is_file()
        consumed = SequenceBoundaryAnalysis.model_validate_json(artifact.read_text(encoding="utf-8"))
        assert consumed == analysis
        assert consumed.black_in_frame == 4
        assert consumed.black_out_frame == 94
        assert consumed.suggested.in_frame == 4
        assert consumed.suggested.out_frame == 94
        assert reopened.timeline.load_timeline(persisted_state.sequence.id) == persisted_state
