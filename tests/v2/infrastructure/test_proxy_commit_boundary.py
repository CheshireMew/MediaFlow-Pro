from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.asset_task_handlers import AssetTaskHandlers
from mediaflow.application.subtitle_acquisition import (
    SubtitleAcquisitionService,
)
from mediaflow.application.subtitle_publication import (
    SubtitlePublicationService,
)
from mediaflow.application.task_service import TaskContext, TaskService
from mediaflow.domain.enums import ColorMode, TaskKind, TaskStatus
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.task_commands import GenerateProxyCommand
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.output_reservation import (
    output_set_transaction,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.task_runtime import (
    InfrastructureAssetTaskRuntime,
)
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


def test_two_phase_output_set_archives_replaced_files_only_after_finalize(
    tmp_path: Path,
) -> None:
    first = (tmp_path / "proxy.mp4").resolve()
    second = (tmp_path / "proxy-sdr.mp4").resolve()
    first.write_bytes(b"old-proxy")
    second.write_bytes(b"old-sdr")
    archive = tmp_path / "archive" / "replaced-proxies"

    with output_set_transaction(
        (first, second),
        overwrite=True,
        runtime_dir=tmp_path / "runtime",
    ) as publication:
        publication.temporary_path(
            first,
            "proxy",
        ).write_bytes(b"new-proxy")
        publication.temporary_path(
            second,
            "proxy-sdr",
        ).write_bytes(b"new-sdr")
        publication.publish()

        assert first.read_bytes() == b"new-proxy"
        assert second.read_bytes() == b"new-sdr"
        assert list(archive.glob("*.mp4")) == []

        replaced = publication.finalize(
            archive_replaced_to=archive,
        )

    assert first.read_bytes() == b"new-proxy"
    assert second.read_bytes() == b"new-sdr"
    assert len(replaced) == 2
    assert {path.read_bytes() for path in replaced} == {
        b"old-proxy",
        b"old-sdr",
    }
    assert all(path.parent == archive.resolve() for path in replaced)
    assert not list(tmp_path.glob(".mf-*"))


def test_proxy_cancel_before_publish_has_no_result_and_after_publish_completes(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "proxy-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    repository = ProjectRepository.create(
        tmp_path / "Proxy Commit Boundary",
        "Proxy Commit Boundary",
    )
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    project = repository.catalog.get_project()
    sequence = repository.catalog.get_sequence(project.main_sequence_id)

    try:
        handlers = AssetTaskHandlers(
            repository,
            assets,
            InfrastructureAssetTaskRuntime(repository, paths),
            SubtitleAcquisitionService(
                repository,
                SubtitlePublicationService(repository),
            ),
        )
        published = threading.Event()
        allow_return = threading.Event()

        def delay_after_publication(context: TaskContext):
            completion = handlers.proxy(context)
            published.set()
            assert allow_return.wait(timeout=10)
            return completion

        tasks = TaskService(
            TaskRepository(repository),
            max_workers=1,
            recover_expired=False,
        )
        tasks.register(TaskKind.PROXY, delay_after_publication)
        try:
            cancellation_requested = threading.Event()

            def cancel_at_registration(event) -> None:
                progress = OperationProgress.model_validate(event.payload["progress"])
                if progress.message_code == "proxy_registering" and not cancellation_requested.is_set():
                    cancellation_requested.set()
                    tasks.cancel(event.task_id)

            subscription = tasks.events.subscribe(cancel_at_registration)
            cancelled_start = tasks.start(
                project_id=project.id,
                sequence_id=sequence.id,
                command=GenerateProxyCommand(asset_id=asset.id),
                input_asset_ids=[asset.id],
            )
            cancelled = tasks.wait(cancelled_start.id, timeout=30)
            tasks.events.unsubscribe(subscription)

            unpublished = repository.catalog.get_asset(asset.id)
            assert cancellation_requested.is_set()
            assert cancelled.status == TaskStatus.CANCELLED
            assert cancelled.artifacts == []
            assert unpublished.proxy_path is None
            assert unpublished.sdr_preview_proxy_path is None
            assert list((repository.project_dir / "proxies").glob("*.mp4")) == []

            started = tasks.start(
                project_id=project.id,
                sequence_id=sequence.id,
                command=GenerateProxyCommand(asset_id=asset.id),
                input_asset_ids=[asset.id],
            )
            assert published.wait(timeout=30)
            tasks.cancel(started.id)
            allow_return.set()
            completed = tasks.wait(started.id, timeout=10)

            assert completed.status == TaskStatus.COMPLETED
            assert len(completed.artifacts) == 1
            artifact = completed.artifacts[0].resolve(repository.project_dir)
            persisted = repository.catalog.get_asset(asset.id)
            assert persisted.proxy_path is not None
            assert artifact == (repository.project_dir / persisted.proxy_path).resolve()
            assert artifact.is_file()
            assert artifact.stat().st_size > 0
            assert (
                MediaProbe(paths)
                .probe(
                    artifact,
                    timeline_profile=sequence.profile,
                )
                .metadata.has_video
                is True
            )
        finally:
            allow_return.set()
            tasks.shutdown()
    finally:
        repository.close()


def test_hdr_proxy_db_commit_failure_restores_both_registered_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "hdr-proxy-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    profile = ProjectProfile(
        width=320,
        height=180,
        fps_numerator=25,
        fps_denominator=1,
        color_mode=ColorMode.HDR10_BT2020_PQ,
        bit_depth=10,
    )
    with ProjectRepository.create(
        tmp_path / "HDR Proxy Rollback",
        "HDR Proxy Rollback",
        profile,
    ) as repository:
        asset = AssetService(
            repository,
            MediaProbe(paths),
        ).import_external(source)
        service = ProxyService(repository, paths)
        first = service.generate(asset, profile)
        assert first.proxy_path is not None
        assert first.sdr_preview_proxy_path is not None
        hdr_path = (repository.project_dir / first.proxy_path).resolve()
        sdr_path = (repository.project_dir / first.sdr_preview_proxy_path).resolve()
        original_stats = {
            hdr_path: hdr_path.stat(),
            sdr_path: sdr_path.stat(),
        }
        original_revision = repository.content_revision()
        original_transaction = repository.transaction
        transaction_depth = 0
        reject_outer_commit = True

        @contextmanager
        def failing_transaction() -> Iterator[object]:
            nonlocal reject_outer_commit
            nonlocal transaction_depth
            transaction_depth += 1
            current_depth = transaction_depth
            try:
                with original_transaction() as connection:
                    yield connection
                    if current_depth == 1 and reject_outer_commit:
                        reject_outer_commit = False
                        raise RuntimeError("injected proxy database commit failure")
            finally:
                transaction_depth -= 1

        monkeypatch.setattr(
            repository,
            "transaction",
            failing_transaction,
        )

        with pytest.raises(
            RuntimeError,
            match="injected proxy database commit failure",
        ):
            service.generate(first, profile)

        persisted = repository.catalog.get_asset(asset.id)
        assert reject_outer_commit is False
        assert persisted.proxy_path == first.proxy_path
        assert persisted.sdr_preview_proxy_path == first.sdr_preview_proxy_path
        assert repository.content_revision() == original_revision
        for path, old_stat in original_stats.items():
            new_stat = path.stat()
            assert new_stat.st_ino == old_stat.st_ino
            assert new_stat.st_mtime_ns == old_stat.st_mtime_ns
            assert new_stat.st_size == old_stat.st_size

        final_outputs = set((repository.project_dir / "proxies").glob("*.mp4"))
        assert final_outputs == {hdr_path, sdr_path}
        withdrawn = list((repository.project_dir / "proxies" / "MediaFlow Failed Exports").glob("*.mp4"))
        assert len(withdrawn) == 2
        assert all(path.stat().st_size > 0 for path in withdrawn)
        assert (
            MediaProbe(paths)
            .probe(
                hdr_path,
                timeline_profile=profile,
            )
            .metadata.pixel_format
            == "yuv420p10le"
        )
        assert (
            MediaProbe(paths)
            .probe(
                sdr_path,
                timeline_profile=profile,
            )
            .metadata.pixel_format
            == "yuv420p"
        )
