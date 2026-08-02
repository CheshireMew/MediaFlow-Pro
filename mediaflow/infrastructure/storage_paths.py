from __future__ import annotations

import os
from pathlib import Path

APPLICATION_ROOT_ENVIRONMENT_VARIABLE = "MEDIAFLOW_APP_ROOT"
PROJECT_ROOT_ENVIRONMENT_VARIABLE = "MEDIAFLOW_PROJECT_ROOT"
DEFAULT_PROJECT_ROOT = Path("E:/Work/Video")
MEDIA_DIRECTORY_NAME = "WorkSpace"


def application_root() -> Path:
    configured = os.environ.get(APPLICATION_ROOT_ENVIRONMENT_VARIABLE)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def default_project_root() -> str:
    configured = os.environ.get(PROJECT_ROOT_ENVIRONMENT_VARIABLE)
    if configured:
        return str(Path(configured).expanduser().resolve())
    return str(DEFAULT_PROJECT_ROOT.resolve())


def default_media_root() -> str:
    return str((application_root() / MEDIA_DIRECTORY_NAME).resolve())
