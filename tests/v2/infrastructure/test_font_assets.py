from __future__ import annotations

import os
import subprocess
import sys

from mediaflow.infrastructure.font_assets import apply_bundled_font_environment


def test_bundled_subtitle_font_is_visible_to_the_real_child_process() -> None:
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    apply_bundled_font_environment("LXGW WenKai", environment)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from PySide6.QtGui import QGuiApplication,QFontDatabase; "
                "app=QGuiApplication([]); "
                "print(QFontDatabase.hasFamily('LXGW WenKai'))"
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
