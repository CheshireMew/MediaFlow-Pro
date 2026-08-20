from __future__ import annotations

from scripts.documentation_screenshot_contract import ROOT, documentation_ui_sources


def test_documentation_ui_sources_use_platform_independent_order() -> None:
    qml_sources = [
        path.relative_to(ROOT).as_posix()
        for path in documentation_ui_sources()
        if path.suffix == ".qml"
    ]

    assert qml_sources == sorted(qml_sources)
