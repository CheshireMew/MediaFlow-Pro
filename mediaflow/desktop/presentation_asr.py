from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mediaflow.domain.task_commands import TranscribeSequenceCommand


def asr_model_options(
    current: str = "",
    *,
    installed_models: frozenset[str] = frozenset(),
) -> list[dict]:
    catalog = (
        (
            "large-v3-turbo",
            QCoreApplication.translate("AsrModelCatalog", "推荐，质量与速度均衡"),
        ),
        (
            "large-v3",
            QCoreApplication.translate("AsrModelCatalog", "最高质量，资源占用最高"),
        ),
        (
            "medium",
            QCoreApplication.translate("AsrModelCatalog", "高质量，中等资源占用"),
        ),
        (
            "medium.en",
            QCoreApplication.translate("AsrModelCatalog", "英语专用，中等资源占用"),
        ),
        (
            "small",
            QCoreApplication.translate("AsrModelCatalog", "较快，适合普通电脑"),
        ),
        (
            "small.en",
            QCoreApplication.translate("AsrModelCatalog", "英语专用，速度较快"),
        ),
        (
            "base",
            QCoreApplication.translate("AsrModelCatalog", "快速，准确率较低"),
        ),
        (
            "base.en",
            QCoreApplication.translate("AsrModelCatalog", "英语专用，资源占用较低"),
        ),
        (
            "tiny",
            QCoreApplication.translate("AsrModelCatalog", "最快，准确率最低"),
        ),
        (
            "tiny.en",
            QCoreApplication.translate("AsrModelCatalog", "英语专用，速度最快"),
        ),
    )
    options = []
    for value, description in catalog:
        status = (
            QCoreApplication.translate("AsrModelCatalog", "已下载")
            if value in installed_models
            else QCoreApplication.translate("AsrModelCatalog", "首次使用时下载")
        )
        options.append(
            {
                "text": f"{value} · {description} · {status}",
                "value": value,
                "installed": value in installed_models,
            }
        )
    known = {str(item["value"]) for item in options}
    normalized = current.strip()
    if normalized and normalized not in known:
        status = (
            QCoreApplication.translate("AsrModelCatalog", "已下载")
            if normalized in installed_models
            else QCoreApplication.translate("AsrModelCatalog", "自定义模型")
        )
        options.append(
            {
                "text": QCoreApplication.translate(
                    "AsrModelCatalog",
                    "当前模型：%1 · %2",
                )
                .replace("%1", normalized)
                .replace("%2", status),
                "value": normalized,
                "installed": normalized in installed_models,
            }
        )
    return options


def asr_language_options(current: str = "") -> list[dict]:
    catalog = (
        ("auto", QCoreApplication.translate("AsrLanguageCatalog", "语言：自动识别")),
        ("zh", QCoreApplication.translate("AsrLanguageCatalog", "语言：中文")),
        ("en", QCoreApplication.translate("AsrLanguageCatalog", "语言：英语")),
        ("ja", QCoreApplication.translate("AsrLanguageCatalog", "语言：日语")),
        ("ko", QCoreApplication.translate("AsrLanguageCatalog", "语言：韩语")),
        ("fr", QCoreApplication.translate("AsrLanguageCatalog", "语言：法语")),
        ("de", QCoreApplication.translate("AsrLanguageCatalog", "语言：德语")),
        ("es", QCoreApplication.translate("AsrLanguageCatalog", "语言：西班牙语")),
        ("ru", QCoreApplication.translate("AsrLanguageCatalog", "语言：俄语")),
    )
    options = [
        {
            "text": label,
            "value": value,
        }
        for value, label in catalog
    ]
    known = {str(item["value"]) for item in options}
    normalized = current.strip()
    if normalized and normalized not in known:
        options.append(
            {
                "text": QCoreApplication.translate(
                    "AsrLanguageCatalog",
                    "语言：当前代码 %1",
                ).replace("%1", normalized),
                "value": normalized,
            }
        )
    return options


def asr_parallel_options() -> list[dict]:
    catalog = (
        (
            0,
            QCoreApplication.translate(
                "AsrParallelCatalog",
                "长音频分块：自动（根据内存和显存）",
            ),
        ),
        (
            1,
            QCoreApplication.translate("AsrParallelCatalog", "长音频分块：顺序转录"),
        ),
        (
            2,
            QCoreApplication.translate("AsrParallelCatalog", "长音频分块：同时转录 2 块"),
        ),
        (
            3,
            QCoreApplication.translate("AsrParallelCatalog", "长音频分块：同时转录 3 块"),
        ),
        (
            4,
            QCoreApplication.translate("AsrParallelCatalog", "长音频分块：同时转录 4 块"),
        ),
    )
    return [
        {
            "text": label,
            "value": value,
        }
        for value, label in catalog
    ]


def transcription_configuration_label(
    command: TranscribeSequenceCommand,
) -> str:
    settings = command.plan.asr
    engine = (
        "Faster-Whisper XXL CLI"
        if settings.engine == "faster_whisper_cli"
        else QCoreApplication.translate("TaskCatalog", "内置 faster-whisper")
    )
    if settings.parallel_chunks == 0:
        parallel = QCoreApplication.translate("TaskCatalog", "自动并行")
    else:
        parallel = QCoreApplication.translate(
            "TaskCatalog",
            "%1 块并行",
        ).replace("%1", str(settings.parallel_chunks))
    return f"{engine} · {settings.model} · {settings.device.upper()} · {settings.language} · {parallel}"
