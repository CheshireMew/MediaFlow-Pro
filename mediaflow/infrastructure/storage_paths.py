from __future__ import annotations

from mediaflow.environment import (
    MEDIA_ROOT_VARIABLE,
    PROJECT_ROOT_VARIABLE,
    required_path,
)


def default_project_root() -> str:
    return str(required_path(PROJECT_ROOT_VARIABLE, "new project creation"))


def default_media_root() -> str:
    return str(required_path(MEDIA_ROOT_VARIABLE, "source media storage"))
