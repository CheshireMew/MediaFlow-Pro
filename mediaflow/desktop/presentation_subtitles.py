from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mediaflow.domain.exports import SubtitleStyle


def built_in_subtitle_style_presets() -> list[dict]:
    values = (
        (
            "classic-white",
            QCoreApplication.translate("SubtitleStyleCatalog", "经典白字"),
            SubtitleStyle(
                font_family="Arial",
                bold=False,
                background_opacity=0.5,
            ),
        ),
        (
            "yellow-bold",
            QCoreApplication.translate("SubtitleStyleCatalog", "黄色字幕"),
            SubtitleStyle(
                font_family="Arial",
                font_color="#FFFF00",
                shadow_size=1,
                background_opacity=0.5,
            ),
        ),
        (
            "cinematic",
            QCoreApplication.translate("SubtitleStyleCatalog", "电影风"),
            SubtitleStyle(
                font_family="Microsoft YaHei",
                font_size=22,
                bold=False,
                outline_size=1,
                shadow_size=2,
                outline_color="#1A1A2E",
                background_opacity=0.5,
            ),
        ),
        (
            "clean-shadow",
            QCoreApplication.translate("SubtitleStyleCatalog", "纯净阴影"),
            SubtitleStyle(
                font_family="Microsoft YaHei",
                bold=False,
                outline_size=0,
                shadow_size=3,
                background_opacity=0.5,
            ),
        ),
        (
            "background-panel",
            QCoreApplication.translate("SubtitleStyleCatalog", "底板模式"),
            SubtitleStyle(
                font_family="Microsoft YaHei",
                font_size=22,
                bold=False,
                outline_size=0,
                background_enabled=True,
                background_opacity=0.6,
            ),
        ),
    )
    return [
        {
            "id": preset_id,
            "name": name,
            "custom": False,
            "style": style.model_dump(mode="json"),
        }
        for preset_id, name, style in values
    ]
