from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

GIB = 1024**3

MIB = 1024**2

PROJECT_CACHE_OWNER_FILENAME = ".mediaflow-storage-owner.json"

PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS = 15.0

PROJECT_CACHE_RESERVATION_LEDGER_SCHEMA = "mediaflow-project-cache-reservations/v5"

PROJECT_CACHE_RESERVATION_LEDGER_FILENAME = ".mediaflow-project-cache-reservations.json"

PROJECT_CACHE_DEFERRED_RESERVATION_MAX_BYTES = 64 * MIB

PROJECT_CACHE_DEFERRED_RESERVATION_LIMIT_DIVISOR = 1024

PROJECT_CACHE_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_CACHE_MAX_BYTES"

PROJECT_CACHES_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_CACHES_MAX_BYTES"

TEST_ARTIFACT_MAX_BYTES_VARIABLE = "MEDIAFLOW_TEST_ARTIFACT_MAX_BYTES"

QUALITY_RUN_MAX_BYTES_VARIABLE = "MEDIAFLOW_QUALITY_RUN_MAX_BYTES"

RUNTIME_MAX_BYTES_VARIABLE = "MEDIAFLOW_RUNTIME_MAX_BYTES"

PROJECT_ARTIFACT_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_ARTIFACT_MAX_BYTES"

PROJECT_ARTIFACTS_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_ARTIFACTS_MAX_BYTES"

DOWNLOAD_OPERATION_MAX_BYTES_VARIABLE = "MEDIAFLOW_DOWNLOAD_OPERATION_MAX_BYTES"

DELIVERY_OPERATION_MAX_BYTES_VARIABLE = "MEDIAFLOW_DELIVERY_OPERATION_MAX_BYTES"

MINIMUM_FREE_BYTES_VARIABLE = "MEDIAFLOW_MINIMUM_FREE_BYTES"

STORAGE_RECEIPT_SCHEMA = "mediaflow-storage-receipt/v2"

STORAGE_RECEIPT_DIRECTORY = "storage-receipts"

PROXY_VIDEO_MAX_BITRATE = 12_000_000

PROXY_AUDIO_BITRATE = 128_000


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    project_cache_max_bytes: int = 32 * GIB
    project_caches_max_bytes: int = 64 * GIB
    test_artifact_max_bytes: int = 32 * GIB
    quality_run_max_bytes: int = 16 * GIB
    runtime_max_bytes: int = 96 * GIB
    project_artifact_max_bytes: int = 64 * GIB
    project_artifacts_max_bytes: int = 128 * GIB
    download_operation_max_bytes: int = 64 * GIB
    delivery_operation_max_bytes: int = 256 * GIB
    minimum_free_bytes: int = 80 * GIB


def _positive_environment_integer(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer byte count") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer byte count")
    return value


def load_storage_policy() -> StoragePolicy:
    defaults = StoragePolicy()
    return StoragePolicy(
        project_cache_max_bytes=_positive_environment_integer(
            PROJECT_CACHE_MAX_BYTES_VARIABLE,
            defaults.project_cache_max_bytes,
        ),
        project_caches_max_bytes=_positive_environment_integer(
            PROJECT_CACHES_MAX_BYTES_VARIABLE,
            defaults.project_caches_max_bytes,
        ),
        test_artifact_max_bytes=_positive_environment_integer(
            TEST_ARTIFACT_MAX_BYTES_VARIABLE,
            defaults.test_artifact_max_bytes,
        ),
        quality_run_max_bytes=_positive_environment_integer(
            QUALITY_RUN_MAX_BYTES_VARIABLE,
            defaults.quality_run_max_bytes,
        ),
        runtime_max_bytes=_positive_environment_integer(
            RUNTIME_MAX_BYTES_VARIABLE,
            defaults.runtime_max_bytes,
        ),
        project_artifact_max_bytes=_positive_environment_integer(
            PROJECT_ARTIFACT_MAX_BYTES_VARIABLE,
            defaults.project_artifact_max_bytes,
        ),
        project_artifacts_max_bytes=_positive_environment_integer(
            PROJECT_ARTIFACTS_MAX_BYTES_VARIABLE,
            defaults.project_artifacts_max_bytes,
        ),
        download_operation_max_bytes=_positive_environment_integer(
            DOWNLOAD_OPERATION_MAX_BYTES_VARIABLE,
            defaults.download_operation_max_bytes,
        ),
        delivery_operation_max_bytes=_positive_environment_integer(
            DELIVERY_OPERATION_MAX_BYTES_VARIABLE,
            defaults.delivery_operation_max_bytes,
        ),
        minimum_free_bytes=_positive_environment_integer(
            MINIMUM_FREE_BYTES_VARIABLE,
            defaults.minimum_free_bytes,
        ),
    )


def storage_policy_report() -> dict[str, Any]:
    return {
        "schema": "mediaflow-storage-policy/v1",
        **asdict(load_storage_policy()),
        "over_budget": "block-before-large-write",
        "cleanup": "report-only-until-authorized",
    }
