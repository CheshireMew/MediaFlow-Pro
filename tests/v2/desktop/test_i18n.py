from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtGui import QGuiApplication


def test_english_and_japanese_catalogs_are_complete_and_loadable() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    i18n = Path(__file__).resolve().parents[3] / "mediaflow" / "resources" / "i18n"
    expected = {
        "en": ("New Project", "Professional Audio", "Export Sequence"),
        "ja": ("新規プロジェクト", "プロオーディオ", "シーケンスを書き出す"),
    }
    for language, translations in expected.items():
        catalog = i18n / f"mediaflow_{language}.ts"
        tree = ET.parse(catalog)
        messages = tree.findall("./context/message")
        assert len(messages) == 385
        assert all(message.find("translation") is not None for message in messages)
        assert all((message.find("translation").text or "").strip() for message in messages)
        assert all(message.find("translation").get("type") != "unfinished" for message in messages)

        translator = QTranslator(app)
        assert translator.load(str(catalog.with_suffix(".qm")))
        assert app.installTranslator(translator)
        assert QCoreApplication.translate("HomeView", "新建项目") == translations[0]
        assert QCoreApplication.translate("AudioPanel", "专业音频") == translations[1]
        assert QCoreApplication.translate("ExportPanel", "导出序列") == translations[2]
        assert app.removeTranslator(translator)
