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
