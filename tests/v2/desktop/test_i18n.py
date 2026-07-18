from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtGui import QGuiApplication

from mediaflow.desktop.presentation_catalogs import (
    encoder_label,
    no_subtitle_burn_label,
    system_name,
    task_message_label,
    task_status_label,
    task_title,
)
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.domain.tasks import Task


def test_english_and_japanese_catalogs_are_complete_and_loadable() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    i18n = Path(__file__).resolve().parents[3] / "mediaflow" / "resources" / "i18n"
    expected = {
        "en": ("New Project", "Professional Audio", "Export Sequence"),
        "ja": ("新規プロジェクト", "プロオーディオ", "シーケンスを書き出す"),
    }
    catalog_keys: dict[str, set[tuple[str, str]]] = {}
    for language, translations in expected.items():
        catalog = i18n / f"mediaflow_{language}.ts"
        tree = ET.parse(catalog)
        messages = tree.findall("./context/message")
        catalog_keys[language] = {
            (context.findtext("name") or "", message.findtext("source") or "")
            for context in tree.findall("./context")
            for message in context.findall("message")
        }
        assert len(catalog_keys[language]) == len(messages)
        assert all(message.find("translation") is not None for message in messages)
        assert all((message.find("translation").text or "").strip() for message in messages)
        assert all(message.find("translation").get("type") != "unfinished" for message in messages)

        translator = QTranslator(app)
        assert translator.load(str(catalog.with_suffix(".qm")))
        assert app.installTranslator(translator)
        assert QCoreApplication.translate("HomeView", "新建项目") == translations[0]
        assert QCoreApplication.translate("AudioPanel", "专业音频") == translations[1]
        assert QCoreApplication.translate("ExportPanel", "导出序列") == translations[2]
        expected_system = ("Video 2", "ビデオ 2")[language == "ja"]
        expected_export = ("Export H264", "H264 を書き出し")[language == "ja"]
        assert system_name("视频 2") == expected_system
        assert encoder_label("h264_software") == (
            "H.264 Software" if language == "en" else "H.264 ソフトウェア"
        )
        assert no_subtitle_burn_label() == ("Do not burn in" if language == "en" else "焼き付けない")
        assert task_status_label("failed") == ("Failed" if language == "en" else "失敗")
        assert task_message_label("export_verifying") == (
            "Verifying export" if language == "en" else "書き出しを検証中"
        )
        assert (
            task_title(
                Task(
                    project_id="project",
                    command=ExportSequenceCommand(
                        sequence_id="sequence",
                        output_path="output.mp4",
                    ),
                )
            )
            == expected_export
        )
        assert app.removeTranslator(translator)
    assert catalog_keys["en"] == catalog_keys["ja"]
