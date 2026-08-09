from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import mediaflow.infrastructure.diagnostics_bundle as diagnostics_module
from mediaflow.application.diagnostics_task_handler import DiagnosticsBundleTaskHandler
from mediaflow.application.task_service import TaskService
from mediaflow.domain.enums import TaskKind
from mediaflow.domain.runtime_capabilities import RuntimeInspection
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.task_commands import DiagnosticsBundleCommand
from mediaflow.domain.tasks import DiagnosticsBundleTaskOutcome
from mediaflow.infrastructure.diagnostics_bundle import DiagnosticsBundleService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_capabilities import RuntimeCapabilityInspector
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.task_runtime import InfrastructureDiagnosticsTaskRuntime


def test_diagnostics_bundle_is_a_persistent_task_with_consistent_project_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = ProjectRepository.create(tmp_path / "project", "Diagnostics")
    paths = RuntimeContext.discover().paths
    monkeypatch.setattr(
        RuntimeCapabilityInspector,
        "inspect",
        lambda self: RuntimeInspection(
            checked_at=1,
            runtime_root=str(paths.runtime_dir),
            capabilities=[],
        ),
    )
    tasks = TaskService(TaskRepository(repository), max_workers=1)
    handler = DiagnosticsBundleTaskHandler(
        repository.project_dir,
        InfrastructureDiagnosticsTaskRuntime(
            repository,
            paths,
            ServiceSettings(),
        ),
    )
    tasks.register(TaskKind.DIAGNOSTICS, handler.handle)
    output = tmp_path / "diagnostics.zip"
    try:
        project = repository.catalog.get_project()
        receipt = tasks.start(
            project_id=project.id,
            sequence_id=project.main_sequence_id,
            command=DiagnosticsBundleCommand(output_path=str(output)),
        )
        completed = tasks.wait(receipt.id, timeout=60)
    finally:
        tasks.shutdown(timeout=10)
        repository.close()

    assert completed.status.value == "completed", completed.error
    assert isinstance(completed.outcome, DiagnosticsBundleTaskOutcome)
    assert completed.outcome.output.resolve(tmp_path / "project") == output
    assert completed.outcome.bundle_sha256
    assert completed.outcome.included_file_count > 4
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "bundle-manifest.json" in names
        assert "project/project.mfp" in names
        assert "project/documents.json" in names
        assert "environment/mediaflow-cli-describe.json" in names
        manifest = json.loads(archive.read("bundle-manifest.json"))
        assert manifest["schema"] == "mediaflow-diagnostics-bundle/v1"
        assert manifest["privacy"]["raw_media_copied"] is False
        assert manifest["project"]["content_revision"] >= 0
        assert len(manifest["project"]["database_snapshot_sha256"]) == 64


def test_diagnostics_never_copies_media_or_environment_files(tmp_path: Path) -> None:
    repository = ProjectRepository.create(tmp_path / "project", "Privacy")
    service = DiagnosticsBundleService(
        repository.project_dir,
        RuntimeContext.discover().paths,
        ServiceSettings(),
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    environment = tmp_path / ".env"
    environment.write_text("API_KEY=secret", encoding="utf-8")
    try:
        assert service._forbidden_copy_reason(source, set()) == "media files are never copied"
        assert (
            service._forbidden_copy_reason(environment, set())
            == "environment files are never copied"
        )
    finally:
        repository.close()


def test_failed_artifact_limits_and_privacy_are_enforced_by_the_collector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(tmp_path / "project", "Artifact limits")
    service = DiagnosticsBundleService(
        repository.project_dir,
        RuntimeContext.discover().paths,
        ServiceSettings(),
    )
    monkeypatch.setattr(diagnostics_module, "FAILED_ARTIFACT_LIMIT_BYTES", 10)
    monkeypatch.setattr(diagnostics_module, "FAILED_ARTIFACT_TOTAL_BYTES", 12)
    allowed = tmp_path / "first.txt"
    total_limited = tmp_path / "second.txt"
    oversized = tmp_path / "oversized.txt"
    original_media = tmp_path / "source.mp4"
    other_media = tmp_path / "failed.wav"
    environment = tmp_path / ".env.local"
    cookie = tmp_path / "cookies" / "session.txt"
    allowed.write_bytes(b"1234567")
    total_limited.write_bytes(b"7654321")
    oversized.write_bytes(b"12345678901")
    original_media.write_bytes(b"source")
    other_media.write_bytes(b"audio")
    environment.write_text("API_KEY=secret", encoding="utf-8")
    cookie.parent.mkdir()
    cookie.write_text("session=secret", encoding="utf-8")
    artifacts = [
        {"path": str(allowed)},
        {"path": str(total_limited)},
        {"path": str(oversized)},
        {"path": str(original_media)},
        {"path": str(other_media)},
        {"path": str(environment)},
        {"path": str(cookie)},
    ]
    staging = tmp_path / "staging"
    staging.mkdir()
    included: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    try:
        service._collect_failed_artifacts(
            staging,
            [{"status": "failed", "artifacts_json": json.dumps(artifacts)}],
            [{"path": str(original_media)}],
            included,
            skipped,
        )
    finally:
        repository.close()

    copied = list((staging / "failed-artifacts").glob("*"))
    assert len(copied) == 1
    assert copied[0].read_bytes() == allowed.read_bytes()
    assert [item["kind"] for item in included] == ["failed_artifact"]
    reasons = {str(item["reason"]) for item in skipped}
    assert "artifact total exceeds 250 MiB" in reasons
    assert "artifact exceeds 25 MiB" in reasons
    assert "original or managed media is never copied" in reasons
    assert "media files are never copied" in reasons
    assert "environment files are never copied" in reasons
    assert "credential, cookie, or model files are never copied" in reasons


def test_failed_diagnostics_collection_archives_staging_without_partial_zip(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "project", "Failed diagnostics")
    service = DiagnosticsBundleService(
        repository.project_dir,
        RuntimeContext.discover().paths,
        ServiceSettings(),
    )
    output = tmp_path / "failed-diagnostics.zip"

    def cancelled() -> None:
        raise RuntimeError("intentional diagnostics cancellation")

    try:
        with pytest.raises(RuntimeError, match="intentional diagnostics cancellation"):
            service.create(
                DiagnosticsBundleCommand(output_path=str(output)),
                check_cancelled=cancelled,
                report=lambda _progress: None,
            )
    finally:
        repository.close()

    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.part"))
    archived = list(
        (tmp_path / "project" / "archive" / "diagnostics").glob("failed-*")
    )
    assert len(archived) == 1
    assert archived[0].is_dir()
