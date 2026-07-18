from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    """Keep V2 integration artifacts on D: for inspection; never delete them implicitly."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.name).strip("-")
    path = Path("D:/Tools/MediaFlow/test-runs") / f"{safe_name}-{uuid.uuid4()}"
    path.mkdir(parents=True, exist_ok=False)
    return path


@pytest.fixture(autouse=True)
def isolated_settings_path(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Prevent tests from adding fixture projects to the user's recent-project index."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.name).strip("-")[:60]
    path = (
        Path("D:/Tools/MediaFlow/test-runs/settings")
        / f"{safe_name}-{uuid.uuid4()}"
        / "settings.json"
    )
    monkeypatch.setenv("MEDIAFLOW_SETTINGS_PATH", str(path))
    monkeypatch.setenv("MEDIAFLOW_APP_ROOT", str(path.parent / "app"))
    return path
