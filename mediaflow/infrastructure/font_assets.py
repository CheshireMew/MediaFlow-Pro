from __future__ import annotations

from pathlib import Path

BUNDLED_SUBTITLE_FONTS = {
    "LXGW WenKai": Path(__file__).resolve().parents[1] / "resources" / "fonts" / "LXGWWenKai-Regular.ttf",
}

SUBTITLE_FONT_CATALOG = (
    ("Arial", "Arial"),
    ("Microsoft YaHei", "微软雅黑"),
    ("SimHei", "黑体"),
    ("SimSun", "宋体"),
    ("KaiTi", "楷体"),
    ("Noto Sans SC", "Noto Sans SC"),
    ("LXGW WenKai", "霞鹜文楷"),
)


def register_application_fonts() -> list[str]:
    from PySide6.QtGui import QFontDatabase

    registered: list[str] = []
    for path in BUNDLED_SUBTITLE_FONTS.values():
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            registered.extend(QFontDatabase.applicationFontFamilies(font_id))
    return registered


def subtitle_font_options() -> list[dict[str, str | bool]]:
    from PySide6.QtGui import QFontDatabase

    return [
        {
            "value": family,
            "label": label,
            "available": QFontDatabase.hasFamily(family),
        }
        for family, label in SUBTITLE_FONT_CATALOG
    ]


def apply_bundled_font_environment(
    font_family: str | None,
    environment: dict[str, str],
) -> None:
    path = BUNDLED_SUBTITLE_FONTS.get(str(font_family or "").strip())
    if path is None or not path.is_file():
        return
    environment["QT_QPA_FONTDIR"] = str(path.parent)
