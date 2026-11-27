from __future__ import annotations

from pathlib import Path
from typing import Any

from mediaflow.environment import test_run_root
from mediaflow.infrastructure.storage_policy import (
    load_storage_policy,
)
from mediaflow.infrastructure.storage_preflight import (
    require_operation_storage_budget,
    require_storage_budget,
)
from mediaflow.infrastructure.storage_roots import (
    delivery_root_ownership,
    managed_root_ownership,
    project_root_ownership,
    registered_producer_roots,
)


def require_runtime_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    ownership = managed_root_ownership(root)
    return {
        **require_storage_budget(
            root,
            expected_new_bytes=expected_new_bytes,
            maximum_managed_bytes=policy.runtime_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label=label,
            registered_roots=registered_producer_roots({"runtime-root"}, Path(root)),
        ),
        "ownership": ownership,
    }


def require_project_artifact_budget(
    project_dir: str | Path,
    owned_root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    project = Path(project_dir).expanduser().resolve()
    selected = Path(owned_root).expanduser().resolve()
    ownership = project_root_ownership(project, selected)
    return {
        "ownership": ownership,
        "artifact_root": require_storage_budget(
            selected,
            expected_new_bytes=expected_new_bytes,
            maximum_managed_bytes=policy.project_artifact_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label=label,
        ),
        "project": require_storage_budget(
            project,
            expected_new_bytes=expected_new_bytes,
            maximum_managed_bytes=policy.project_artifacts_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label="MediaFlow project artifacts",
        ),
    }


def require_delivery_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    ownership = delivery_root_ownership(root)
    return {
        **require_operation_storage_budget(
            root,
            expected_new_bytes=expected_new_bytes,
            maximum_operation_bytes=policy.delivery_operation_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label=label,
        ),
        "ownership": ownership,
    }


def require_download_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    ownership = delivery_root_ownership(root)
    return {
        **require_operation_storage_budget(
            root,
            expected_new_bytes=expected_new_bytes,
            maximum_operation_bytes=policy.download_operation_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label=label,
        ),
        "ownership": ownership,
    }


def require_test_artifact_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    ownership = managed_root_ownership(root)
    selected = Path(root).expanduser().resolve()
    configured = test_run_root()
    managed_root_ownership(configured)
    aggregate = configured if selected.is_relative_to(configured) else selected
    return {
        **require_storage_budget(
            aggregate,
            expected_new_bytes=expected_new_bytes,
            maximum_managed_bytes=policy.test_artifact_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label=label,
            registered_roots=registered_producer_roots(
                {"pytest-and-verification-evidence", "local-quality-run"}, aggregate
            ),
        ),
        "ownership": ownership,
    }
