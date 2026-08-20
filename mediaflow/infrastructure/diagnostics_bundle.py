from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mediaflow import __version__
from mediaflow.automation.contracts import describe_contract
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.task_commands import DiagnosticsBundleCommand
from mediaflow.infrastructure.ffprobe_runner import FfprobeRunner
from mediaflow.infrastructure.project_schema_definition import PROJECT_FILE_NAME
from mediaflow.infrastructure.runtime_capabilities import RuntimeCapabilityInspector
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_capture_engine import web_capture_diagnostics

LOG_LIMIT_BYTES = 20 * 1024 * 1024
FAILED_ARTIFACT_LIMIT_BYTES = 25 * 1024 * 1024
FAILED_ARTIFACT_TOTAL_BYTES = 250 * 1024 * 1024
MLT_GRAPH_LIMIT_BYTES = 5 * 1024 * 1024
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)\S+"),
)
_NEVER_COPY_PARTS = {"cookies", "models", "credentials", "secrets"}
_MEDIA_SUFFIXES = {
    ".3g2",
    ".3gp",
    ".aac",
    ".aiff",
    ".avif",
    ".avi",
    ".bmp",
    ".flac",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogg",
    ".opus",
    ".png",
    ".tif",
    ".tiff",
    ".wav",
    ".webm",
    ".webp",
}


class DiagnosticsBundleService:
    def __init__(
        self,
        project_dir: Path,
        paths: RuntimePaths,
        settings: ServiceSettings,
    ):
        self.project_dir = project_dir.resolve(strict=True)
        self.paths = paths
        self.settings = settings
        self.database_path = self.project_dir / PROJECT_FILE_NAME
        self.ffprobe = FfprobeRunner(self.paths.ffprobe)

    def create(
        self,
        command: DiagnosticsBundleCommand,
        *,
        check_cancelled: Callable[[], None],
        report: Callable[[OperationProgress], None],
    ) -> tuple[Path, str, int, int]:
        command.validate_for_execution()
        output = Path(command.output_path).resolve()
        if output.exists() and not command.overwrite:
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix="diagnostics-staging-",
                dir=self.project_dir / "cache",
            )
        )
        partial = output.with_name(f".{output.name}.{uuid4().hex}.part")
        included: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        try:
            report(OperationProgress.indeterminate("diagnostics_collecting_project"))
            check_cancelled()
            snapshot = staging / "project" / PROJECT_FILE_NAME
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            self._backup_database(snapshot)
            self._record_file(snapshot, staging, included, "sqlite_consistent_backup")
            project_summary, asset_rows, selected_tasks = self._collect_project_documents(
                staging,
                command.task_ids,
                included,
                skipped,
            )

            report(OperationProgress.indeterminate("diagnostics_inspecting_runtime"))
            check_cancelled()
            self._collect_environment(staging, included, skipped)
            self._collect_web_evidence(staging, included, skipped)
            self._collect_mlt_graphs(staging, included, skipped)

            report(OperationProgress.indeterminate("diagnostics_probing_media"))
            self._collect_media_probes(
                staging,
                asset_rows,
                included,
                skipped,
                check_cancelled,
            )
            self._collect_logs(staging, included, skipped)
            self._collect_failed_artifacts(
                staging,
                selected_tasks,
                asset_rows,
                included,
                skipped,
            )

            manifest = {
                "schema": "mediaflow-diagnostics-bundle/v1",
                "application_version": __version__,
                "collected_at": datetime.now(UTC).isoformat(),
                "project": project_summary,
                "requested_task_ids": list(command.task_ids),
                "limits": {
                    "logs_bytes": LOG_LIMIT_BYTES,
                    "failed_artifact_bytes_each": FAILED_ARTIFACT_LIMIT_BYTES,
                    "failed_artifacts_bytes_total": FAILED_ARTIFACT_TOTAL_BYTES,
                },
                "included": included,
                "skipped": skipped,
                "privacy": {
                    "raw_media_copied": False,
                    "cookies_copied": False,
                    "models_copied": False,
                    "environment_files_copied": False,
                    "service_credentials_copied": False,
                },
            }
            self._write_json(staging / "bundle-manifest.json", manifest)
            self._record_file(
                staging / "bundle-manifest.json",
                staging,
                included,
                "bundle_manifest",
            )

            report(OperationProgress.indeterminate("diagnostics_writing_bundle"))
            check_cancelled()
            self._write_zip(staging, partial)
            if output.exists() and not command.overwrite:
                raise FileExistsError(output)
            os.replace(partial, output)
            bundle_sha = self._sha256(output)
            shutil.rmtree(staging)
            return output, bundle_sha, len(included), len(skipped)
        except BaseException:
            if partial.exists():
                partial.unlink()
            self._archive_failed_staging(staging)
            raise

    def _backup_database(self, destination: Path) -> None:
        with sqlite3.connect(self.database_path) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
                target.execute("PRAGMA integrity_check")

    def _collect_project_documents(
        self,
        staging: Path,
        task_ids: list[str],
        included: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            project = self._query_rows(connection, "project")
            if not project:
                raise RuntimeError("项目数据库中没有 project 记录")
            summary = dict(project[0])
            summary["database_snapshot_sha256"] = self._sha256(
                staging / "project" / PROJECT_FILE_NAME
            )
            summary["database_path"] = str(self.database_path)
            tables = (
                "project",
                "sequence",
                "track",
                "clip",
                "transition",
                "timeline_marker",
                "timeline_range",
                "export_preset",
                "export_history",
                "web_asset",
                "web_clip_state",
            )
            document: dict[str, Any] = {}
            for table in tables:
                try:
                    document[table] = self._redact(self._query_rows(connection, table))
                except sqlite3.OperationalError as error:
                    skipped.append({"item": f"database:{table}", "reason": str(error)})
            assets = self._query_rows(connection, "asset")
            document["asset"] = self._redact(assets)
            tasks = self._select_tasks(connection, task_ids)
            document["tasks"] = self._redact(tasks)
            destination = staging / "project" / "documents.json"
            self._write_json(destination, document)
            self._record_file(destination, staging, included, "project_documents")
            return self._redact(summary), assets, tasks

    @staticmethod
    def _query_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]

    def _select_tasks(
        self,
        connection: sqlite3.Connection,
        task_ids: list[str],
    ) -> list[dict[str, Any]]:
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            rows = connection.execute(
                f"SELECT * FROM task WHERE id IN ({placeholders}) ORDER BY created_at DESC",
                task_ids,
            ).fetchall()
            found = {str(row["id"]) for row in rows}
            missing = sorted(set(task_ids) - found)
            if missing:
                raise ValueError(f"诊断任务不存在：{', '.join(missing)}")
            return [dict(row) for row in rows]
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM task ORDER BY created_at DESC LIMIT 50"
            )
        ]

    def _collect_environment(
        self,
        staging: Path,
        included: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        system = {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "process_architecture": platform.architecture()[0],
        }
        self._write_record(staging, "environment/system.json", system, included, "system")
        self._write_record(
            staging,
            "environment/mediaflow-cli-describe.json",
            describe_contract(),
            included,
            "automation_contract",
        )
        try:
            inspection = RuntimeCapabilityInspector(
                settings=self.settings,
                runtime=RuntimeContext.discover(),
            ).inspect()
            self._write_record(
                staging,
                "environment/runtime-inspection.json",
                inspection.model_dump(mode="json"),
                included,
                "runtime_inspection",
            )
        except Exception as error:
            skipped.append({"item": "runtime-inspection", "reason": str(error)})
        summaries = []
        for name in ("runtime.lock.json", "requirements.lock"):
            path = REPOSITORY_ROOT / name
            if path.is_file():
                summaries.append(self._identity(path))
            else:
                skipped.append({"item": name, "reason": "not found"})
        self._write_record(
            staging,
            "environment/lock-summaries.json",
            summaries,
            included,
            "lock_summaries",
        )
        chromium = self.paths.chromium
        if chromium and chromium.is_file():
            try:
                diagnostics = asdict(web_capture_diagnostics(chromium))
                self._write_record(
                    staging,
                    "environment/web-capture-diagnostics.json",
                    diagnostics,
                    included,
                    "web_capture_diagnostics",
                )
            except Exception as error:
                skipped.append({"item": "web-capture-diagnostics", "reason": str(error)})

    def _collect_web_evidence(
        self,
        staging: Path,
        included: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        candidates: list[Path] = []
        web_root = self.project_dir / "sources" / "web"
        if web_root.is_dir():
            for name in (
                "editable-media.json",
                "media-sources.json",
                "build-info.json",
                "fixture-origin.json",
            ):
                candidates.extend(web_root.rglob(name))
            candidates.extend((web_root / "receipts").glob("r-*.json"))
        cache_root = self.paths.project_cache_dir(self.project_dir)
        if cache_root.is_dir():
            candidates.extend(cache_root.rglob("*.manifest.json"))
        for index, source in enumerate(sorted(set(candidates))):
            if source.stat().st_size > 2 * 1024 * 1024:
                skipped.append({"item": str(source), "reason": "web evidence exceeds 2 MiB"})
                continue
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                skipped.append({"item": str(source), "reason": str(error)})
                continue
            self._write_record(
                staging,
                f"web/{index:04d}-{source.name}",
                self._redact(payload),
                included,
                "web_evidence",
                source=str(source),
            )

    def _collect_mlt_graphs(
        self,
        staging: Path,
        included: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        cache_root = self.paths.project_cache_dir(self.project_dir)
        if not cache_root.is_dir():
            return
        graphs = sorted(
            cache_root.rglob("*.mlt"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )[:5]
        for index, graph in enumerate(graphs):
            size = graph.stat().st_size
            if size > MLT_GRAPH_LIMIT_BYTES:
                skipped.append({"item": str(graph), "reason": "MLT graph exceeds 5 MiB"})
                continue
            destination = staging / "mlt" / f"{index:02d}-{graph.name}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                self._redact_text(graph.read_text(encoding="utf-8", errors="replace")),
                encoding="utf-8",
            )
            self._record_file(destination, staging, included, "mlt_graph", source=str(graph))

    def _collect_media_probes(
        self,
        staging: Path,
        assets: list[dict[str, Any]],
        included: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
        check_cancelled: Callable[[], None],
    ) -> None:
        records: list[dict[str, Any]] = []
        for asset in assets:
            check_cancelled()
            path = self._asset_path(asset)
            identity = {
                "asset_id": asset.get("id"),
                "kind": asset.get("kind"),
                "path": str(path),
                "exists": path.is_file(),
            }
            if path.is_file():
                identity.update(self._identity(path))
            probe: Any = None
            if path.is_file() and str(asset.get("kind")) != "web":
                try:
                    completed = self.ffprobe.run(
                        [
                            "-v",
                            "error",
                            "-show_format",
                            "-show_streams",
                            "-of",
                            "json",
                            str(path),
                        ],
                        timeout=60,
                        check_cancelled=check_cancelled,
                    )
                    if completed.returncode == 0:
                        probe = json.loads(completed.stdout)
                    else:
                        identity["ffprobe_error"] = completed.stderr.strip()
                except Exception as error:
                    identity["ffprobe_error"] = str(error)
            records.append({"identity": identity, "ffprobe": self._redact(probe)})
        self._write_record(
            staging,
            "media/assets-and-ffprobe.json",
            records,
            included,
            "media_identity_and_probe",
        )

    def _collect_logs(
        self,
        staging: Path,
        included: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        roots = [self.paths.runtime_dir / "logs"]
        files = sorted(
            {path for root in roots if root.is_dir() for path in root.rglob("*") if path.is_file()},
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        remaining = LOG_LIMIT_BYTES
        for index, source in enumerate(files):
            if remaining <= 0:
                skipped.append({"item": str(source), "reason": "combined log limit reached"})
                continue
            size = source.stat().st_size
            take = min(size, remaining)
            with source.open("rb") as stream:
                stream.seek(max(0, size - take))
                raw = stream.read(take)
            destination = staging / "logs" / f"{index:04d}-{source.name}.tail.txt"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                self._redact_text(raw.decode("utf-8", errors="replace")),
                encoding="utf-8",
            )
            self._record_file(destination, staging, included, "log_tail", source=str(source))
            remaining -= take

    def _collect_failed_artifacts(
        self,
        staging: Path,
        tasks: list[dict[str, Any]],
        assets: list[dict[str, Any]],
        included: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        protected = {
            self._asset_path(asset).resolve()
            for asset in assets
            if self._asset_path(asset).exists()
        }
        total = 0
        serial = 0
        for task in tasks:
            if str(task.get("status")) not in {"failed", "cancelled"}:
                continue
            try:
                artifact_values = json.loads(str(task.get("artifacts_json") or "[]"))
            except json.JSONDecodeError:
                continue
            for artifact in artifact_values:
                source = self._artifact_path(artifact)
                if source is None or not source.is_file():
                    continue
                reason = self._forbidden_copy_reason(source, protected)
                if reason:
                    skipped.append({"item": str(source), "reason": reason})
                    continue
                size = source.stat().st_size
                if size > FAILED_ARTIFACT_LIMIT_BYTES:
                    skipped.append({"item": str(source), "reason": "artifact exceeds 25 MiB"})
                    continue
                if total + size > FAILED_ARTIFACT_TOTAL_BYTES:
                    skipped.append({"item": str(source), "reason": "artifact total exceeds 250 MiB"})
                    continue
                destination = staging / "failed-artifacts" / f"{serial:04d}-{source.name}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                self._record_file(
                    destination,
                    staging,
                    included,
                    "failed_artifact",
                    source=str(source),
                )
                total += size
                serial += 1

    def _forbidden_copy_reason(self, path: Path, protected: set[Path]) -> str | None:
        resolved = path.resolve()
        if resolved in protected:
            return "original or managed media is never copied"
        if path.name.lower().startswith(".env"):
            return "environment files are never copied"
        if any(part.casefold() in _NEVER_COPY_PARTS for part in path.parts):
            return "credential, cookie, or model files are never copied"
        if path.suffix.casefold() in _MEDIA_SUFFIXES:
            return "media files are never copied"
        return None

    def _asset_path(self, asset: dict[str, Any]) -> Path:
        value = Path(str(asset.get("path") or ""))
        return value if value.is_absolute() else self.project_dir / value

    def _artifact_path(self, artifact: Any) -> Path | None:
        if not isinstance(artifact, dict):
            return None
        value = artifact.get("path")
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        if artifact.get("scope") == "project":
            path = self.project_dir / path
        return path.resolve() if path.is_absolute() else None

    def _archive_failed_staging(self, staging: Path) -> None:
        if not staging.exists():
            return
        archive = self.project_dir / "archive" / "diagnostics"
        archive.mkdir(parents=True, exist_ok=True)
        destination = archive / (
            f"failed-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        )
        shutil.move(str(staging), destination)

    @staticmethod
    def _write_zip(source: Path, destination: Path) -> None:
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                archive.write(path, path.relative_to(source).as_posix())

    def _write_record(
        self,
        staging: Path,
        relative: str,
        value: Any,
        included: list[dict[str, Any]],
        kind: str,
        *,
        source: str | None = None,
    ) -> None:
        destination = staging / relative
        self._write_json(destination, value)
        self._record_file(destination, staging, included, kind, source=source)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _record_file(
        self,
        path: Path,
        staging: Path,
        included: list[dict[str, Any]],
        kind: str,
        *,
        source: str | None = None,
    ) -> None:
        record = {
            "path": path.relative_to(staging).as_posix(),
            "kind": kind,
            "size": path.stat().st_size,
            "sha256": self._sha256(path),
        }
        if source:
            record["source"] = source
        included.append(record)

    @staticmethod
    def _identity(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "path": str(path),
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
            "sha256": DiagnosticsBundleService._sha256(path),
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): (
                    "[REDACTED]"
                    if _SENSITIVE_KEY.search(str(key))
                    else cls._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, tuple):
            return [cls._redact(item) for item in value]
        if isinstance(value, str):
            return cls._redact_text(value)
        return value

    @staticmethod
    def _redact_text(value: str) -> str:
        result = value
        for pattern in _SENSITIVE_TEXT:
            result = pattern.sub(r"\1[REDACTED]", result)
        return result


def summarize_paths(paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [DiagnosticsBundleService._identity(path) for path in paths if path.is_file()]
