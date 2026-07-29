from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtGui import QGuiApplication

from mediaflow.desktop.presentation_catalogs import (
    asr_language_options,
    asr_model_options,
    asr_parallel_options,
    encoder_label,
    export_recovery_configuration_label,
    no_subtitle_burn_label,
    system_name,
    task_message_label,
    task_status_label,
    task_title,
    transcription_configuration_label,
)
from mediaflow.domain.asr import TranscriptionPlan
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.task_commands import ExportSequenceCommand, TranscribeSequenceCommand
from mediaflow.domain.tasks import (
    ArtifactReference,
    ExportFileTaskOutcome,
    ExportTaskOutcome,
    Task,
)


def test_english_and_japanese_catalogs_are_complete_and_loadable() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    i18n = Path(__file__).resolve().parents[3] / "mediaflow" / "resources" / "i18n"
    expected = {
        "en": ("New Project", "Audio", "Export Sequence"),
        "ja": ("新規プロジェクト", "音声", "シーケンスを書き出す"),
    }
    expected_technical_labels = {
        "en": (
            "Cookie JSON",
            "API Key",
            "Mono",
            "Stereo",
            "FPS numerator",
            "FPS denominator",
        ),
        "ja": (
            "Cookie JSON",
            "API キー",
            "モノラル",
            "ステレオ",
            "FPS 分子",
            "FPS 分母",
        ),
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
        required_technical_keys = {
            ("SettingsDialog", "Cookie JSON"),
            ("SettingsDialog", "API 密钥"),
            ("ExportTechnicalSettings", "单声道"),
            ("ExportTechnicalSettings", "立体声"),
            ("ExportTechnicalSettings", "FPS 分子"),
            ("ExportTechnicalSettings", "FPS 分母"),
        }
        assert required_technical_keys <= catalog_keys[language]

        translator = QTranslator(app)
        assert translator.load(str(catalog.with_suffix(".qm")))
        assert app.installTranslator(translator)
        assert QCoreApplication.translate("HomeView", "新建项目") == translations[0]
        assert QCoreApplication.translate("WorkspaceNavigation", "音频") == translations[1]
        assert QCoreApplication.translate("ExportFileDialogs", "导出序列") == translations[2]
        technical_labels = expected_technical_labels[language]
        assert QCoreApplication.translate("SettingsDialog", "Cookie JSON") == technical_labels[0]
        assert QCoreApplication.translate("SettingsDialog", "API 密钥") == technical_labels[1]
        assert (
            QCoreApplication.translate("ExportTechnicalSettings", "单声道")
            == technical_labels[2]
        )
        assert (
            QCoreApplication.translate("ExportTechnicalSettings", "立体声")
            == technical_labels[3]
        )
        assert (
            QCoreApplication.translate("ExportTechnicalSettings", "FPS 分子")
            == technical_labels[4]
        )
        assert (
            QCoreApplication.translate("ExportTechnicalSettings", "FPS 分母")
            == technical_labels[5]
        )
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
        assert task_message_label("export_hardware_encoder_fallback") == (
            "Hardware encoding failed; switching to software encoding"
            if language == "en"
            else "ハードウェアエンコードに失敗したため、ソフトウェアエンコードに切り替えています"
        )
        assert export_recovery_configuration_label(
            ExportTaskOutcome(
                files=[
                    ExportFileTaskOutcome(
                        output=ArtifactReference.external(Path("C:/output.mp4")),
                        requested_video_codec="h264_nvenc",
                        actual_video_codec="libx264",
                        hardware_fallback_reason="failed",
                    )
                ]
            )
        ) == (
            "Hardware encoding failed; switched from h264_nvenc to libx264"
            if language == "en"
            else "ハードウェアエンコードに失敗したため、h264_nvenc から libx264 に切り替えました"
        )
        model_options = asr_model_options(
            "tiny.en",
            installed_models=frozenset({"tiny.en"}),
        )
        tiny_english = next(item for item in model_options if item["value"] == "tiny.en")
        assert tiny_english["installed"] is True
        assert tiny_english["text"].endswith("Downloaded" if language == "en" else "ダウンロード済み")
        custom_language = asr_language_options("it")[-1]
        assert custom_language["value"] == "it"
        assert custom_language["text"] == (
            "Language: Current code it" if language == "en" else "言語：現在のコード it"
        )
        assert asr_parallel_options()[2]["text"] == (
            "Long-audio chunks: 2 at once" if language == "en" else "長時間音声チャンク：2 個を同時処理"
        )
        transcription_config = transcription_configuration_label(
            TranscribeSequenceCommand(
                plan=TranscriptionPlan(
                    sequence_id="sequence",
                    timeline_signature="signature",
                    dialogue_track_id="dialogue",
                    timeline_start_frame=0,
                    timeline_end_frame=0,
                    fps_numerator=25,
                    fps_denominator=1,
                    sources=[],
                    asr=AsrSettings(
                        engine="builtin",
                        model="tiny.en",
                        device="cpu",
                        language="en",
                        parallel_chunks=2,
                    ),
                )
            )
        )
        assert transcription_config == (
            "Built-in faster-whisper · tiny.en · CPU · en · 2 chunks in parallel"
            if language == "en"
            else "内蔵 faster-whisper · tiny.en · CPU · en · 2 チャンク並列"
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
