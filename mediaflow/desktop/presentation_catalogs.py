from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from mediaflow.application.export_catalog import available_export_variants
from mediaflow.domain.audio import audio_effect_parameter_schema
from mediaflow.domain.audio_effect_presets import audio_effect_preset_ids
from mediaflow.domain.effect_registry import TRANSITION_CAPABILITIES, transition_is_available
from mediaflow.domain.enums import AudioEffectKind, ColorMode
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    DownloadMediaCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    ImportAssetCommand,
    RenderWebClipCommand,
    TrackSubjectCommand,
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.domain.tasks import ExportTaskOutcome, Task
from mediaflow.domain.translation import TRANSLATION_LANGUAGES, TRANSLATION_MODES

_EXPORT_LABELS = {
    "h264": "H.264",
    "hevc": "HEVC",
    "av1": "AV1",
    "prores_proxy": "ProRes Proxy",
    "prores_lt": "ProRes LT",
    "prores_standard": "ProRes Standard",
    "prores_hq": "ProRes HQ",
    "prores_4444": "ProRes 4444",
    "audio_aac": "AAC / M4A",
    "audio_opus": "Opus / OGG",
    "audio_pcm": "PCM / WAV",
    "audio_flac": "FLAC",
}


@dataclass(frozen=True, slots=True)
class WorkspaceModeDefinition:
    key: str
    label_source: str
    panel_object_name: str
    icon: str
    navigation_visible: bool = True


WORKSPACE_MODES = (
    WorkspaceModeDefinition(
        "media",
        QT_TRANSLATE_NOOP("WorkspaceNavigation", "素材"),
        "mediaPanel",
        "media",
    ),
    WorkspaceModeDefinition(
        "transcript",
        QT_TRANSLATE_NOOP("WorkspaceNavigation", "文本与字幕"),
        "transcriptWorkspace",
        "transcript",
    ),
    WorkspaceModeDefinition(
        "highlight",
        QT_TRANSLATE_NOOP("WorkspaceNavigation", "AI 高光"),
        "highlightPanel",
        "highlight",
    ),
    WorkspaceModeDefinition(
        "audio",
        QT_TRANSLATE_NOOP("WorkspaceNavigation", "音频"),
        "audioScroll",
        "audio",
    ),
    WorkspaceModeDefinition(
        "export",
        QT_TRANSLATE_NOOP("WorkspaceNavigation", "导出"),
        "exportPanel",
        "export",
        False,
    ),
    WorkspaceModeDefinition(
        "tasks",
        QT_TRANSLATE_NOOP("WorkspaceNavigation", "任务"),
        "taskCenterPanel",
        "tasks",
    ),
)
WORKSPACE_MODE_KEYS = tuple(mode.key for mode in WORKSPACE_MODES)
WORKSPACE_NAVIGATION_MODE_KEYS = tuple(mode.key for mode in WORKSPACE_MODES if mode.navigation_visible)


def workspace_mode_catalog() -> list[dict[str, str]]:
    return [
        {
            "key": mode.key,
            "label": QCoreApplication.translate("WorkspaceNavigation", mode.label_source),
            "panelObjectName": mode.panel_object_name,
            "icon": mode.icon,
            "navigationVisible": mode.navigation_visible,
        }
        for mode in WORKSPACE_MODES
    ]


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


def system_name(name: str) -> str:
    exact = {
        "主序列": QCoreApplication.translate("SystemNameCatalog", "主序列"),
        "主总线": QCoreApplication.translate("SystemNameCatalog", "主总线"),
        "对白": QCoreApplication.translate("SystemNameCatalog", "对白"),
        "音乐": QCoreApplication.translate("SystemNameCatalog", "音乐"),
        "效果": QCoreApplication.translate("SystemNameCatalog", "效果"),
    }
    if name in exact:
        return exact[name]
    prefixes = {
        "短视频 ": QCoreApplication.translate("SystemNameCatalog", "短视频 %1"),
        "视频 ": QCoreApplication.translate("SystemNameCatalog", "视频 %1"),
        "音频 ": QCoreApplication.translate("SystemNameCatalog", "音频 %1"),
        "字幕 ": QCoreApplication.translate("SystemNameCatalog", "字幕 %1"),
    }
    for prefix, template in prefixes.items():
        suffix = name[len(prefix) :] if name.startswith(prefix) else ""
        if suffix.isdigit():
            return template.replace("%1", suffix)
    return name


def status_message(source: str, *arguments: object) -> str:
    templates = {
        "%1 连接测试成功": QCoreApplication.translate("StatusMessageCatalog", "%1 连接测试成功"),
        "Cookie 已保存到 %1": QCoreApplication.translate("StatusMessageCatalog", "Cookie 已保存到 %1"),
        "Cookie 已清除": QCoreApplication.translate("StatusMessageCatalog", "Cookie 已清除"),
        "LLM 提供商已保存": QCoreApplication.translate("StatusMessageCatalog", "LLM 提供商已保存"),
        "LLM 提供商已移除": QCoreApplication.translate("StatusMessageCatalog", "LLM 提供商已移除"),
        "修改已应用到字幕文档": QCoreApplication.translate("StatusMessageCatalog", "修改已应用到字幕文档"),
        "分析期间时间线已修改，请重新运行智能入出点": QCoreApplication.translate(
            "StatusMessageCatalog", "分析期间时间线已修改，请重新运行智能入出点"
        ),
        "场景切点已写入时间线": QCoreApplication.translate("StatusMessageCatalog", "场景切点已写入时间线"),
        "当前 LLM 提供商已切换": QCoreApplication.translate("StatusMessageCatalog", "当前 LLM 提供商已切换"),
        "当前工作流阶段正在运行": QCoreApplication.translate(
            "StatusMessageCatalog", "当前工作流阶段正在运行"
        ),
        "短视频序列已创建": QCoreApplication.translate("StatusMessageCatalog", "短视频序列已创建"),
        "短视频序列已移除；可使用撤销恢复": QCoreApplication.translate(
            "StatusMessageCatalog", "短视频序列已移除；可使用撤销恢复"
        ),
        "工作流任务失败：%1": QCoreApplication.translate("StatusMessageCatalog", "工作流任务失败：%1"),
        "高光候选已保存": QCoreApplication.translate("StatusMessageCatalog", "高光候选已保存"),
        "高光候选已删除": QCoreApplication.translate("StatusMessageCatalog", "高光候选已删除"),
        "高光区间已添加到主序列": QCoreApplication.translate(
            "StatusMessageCatalog", "高光区间已添加到主序列"
        ),
        "默认下载目录已更新": QCoreApplication.translate("StatusMessageCatalog", "默认下载目录已更新"),
        "默认项目保存目录已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "默认项目保存目录已更新"
        ),
        "外部修改与当前输入冲突，已保护未提交内容": QCoreApplication.translate(
            "StatusMessageCatalog", "外部修改与当前输入冲突，已保护未提交内容"
        ),
        "字幕已保存": QCoreApplication.translate("StatusMessageCatalog", "字幕已保存"),
        "字幕已合并": QCoreApplication.translate("StatusMessageCatalog", "字幕已合并"),
        "字幕已导出到 %1": QCoreApplication.translate("StatusMessageCatalog", "字幕已导出到 %1"),
        "字幕已拆分": QCoreApplication.translate("StatusMessageCatalog", "字幕已拆分"),
        "字幕样式预设已移除": QCoreApplication.translate("StatusMessageCatalog", "字幕样式预设已移除"),
        "已从时间线选区创建短视频序列": QCoreApplication.translate(
            "StatusMessageCatalog", "已从时间线选区创建短视频序列"
        ),
        "已从最近项目中移除": QCoreApplication.translate("StatusMessageCatalog", "已从最近项目中移除"),
        "已从高光创建短视频序列": QCoreApplication.translate(
            "StatusMessageCatalog", "已从高光创建短视频序列"
        ),
        "已保存字幕样式预设：%1": QCoreApplication.translate(
            "StatusMessageCatalog", "已保存字幕样式预设：%1"
        ),
        "已保存序列字幕覆盖": QCoreApplication.translate("StatusMessageCatalog", "已保存序列字幕覆盖"),
        "已保留你的修改": QCoreApplication.translate("StatusMessageCatalog", "已保留你的修改"),
        "已修复 %1 条重叠字幕": QCoreApplication.translate("StatusMessageCatalog", "已修复 %1 条重叠字幕"),
        "已创建 %1 个短视频草稿": QCoreApplication.translate(
            "StatusMessageCatalog", "已创建 %1 个短视频草稿"
        ),
        "已创建命名版本“%1”": QCoreApplication.translate("StatusMessageCatalog", "已创建命名版本“%1”"),
        "已创建复合片段": QCoreApplication.translate("StatusMessageCatalog", "已创建复合片段"),
        "已创建素材文件夹：%1": QCoreApplication.translate("StatusMessageCatalog", "已创建素材文件夹：%1"),
        "已删除 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已删除 %1 条字幕"),
        "已复制 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已复制 %1 条字幕"),
        "已实时同步 %1 的修改": QCoreApplication.translate("StatusMessageCatalog", "已实时同步 %1 的修改"),
        "已导入 %1": QCoreApplication.translate("StatusMessageCatalog", "已导入 %1"),
        "已导入 %1 个素材": QCoreApplication.translate("StatusMessageCatalog", "已导入 %1 个素材"),
        "已导入 %1，共 %2 条字幕": QCoreApplication.translate(
            "StatusMessageCatalog", "已导入 %1，共 %2 条字幕"
        ),
        "已导出 FCPXML：%1": QCoreApplication.translate("StatusMessageCatalog", "已导出 FCPXML：%1"),
        "已将 %1 放入时间轴": QCoreApplication.translate("StatusMessageCatalog", "已将 %1 放入时间轴"),
        "已将当前画面保存为素材：%1": QCoreApplication.translate(
            "StatusMessageCatalog", "已将当前画面保存为素材：%1"
        ),
        "已恢复命名版本“%1”": QCoreApplication.translate("StatusMessageCatalog", "已恢复命名版本“%1”"),
        "已恢复字幕文档时间": QCoreApplication.translate("StatusMessageCatalog", "已恢复字幕文档时间"),
        "已放入 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已放入 %1 条字幕"),
        "已更新 %1 总线": QCoreApplication.translate("StatusMessageCatalog", "已更新 %1 总线"),
        "已替换 %1 处文本": QCoreApplication.translate("StatusMessageCatalog", "已替换 %1 处文本"),
        "已替换 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已替换 %1 条字幕"),
        "已替换当前匹配": QCoreApplication.translate("StatusMessageCatalog", "已替换当前匹配"),
        "已替换素材内容，预览缓存和音频波形将重新生成": QCoreApplication.translate(
            "StatusMessageCatalog", "已替换素材内容，预览缓存和音频波形将重新生成"
        ),
        "已添加字幕": QCoreApplication.translate("StatusMessageCatalog", "已添加字幕"),
        "已添加手动高光候选": QCoreApplication.translate("StatusMessageCatalog", "已添加手动高光候选"),
        "已采用最新项目内容": QCoreApplication.translate("StatusMessageCatalog", "已采用最新项目内容"),
        "已清理 %1 条任务记录，任务产物仍保留": QCoreApplication.translate(
            "StatusMessageCatalog", "已清理 %1 条任务记录，任务产物仍保留"
        ),
        "已设置序列入出点：%1–%2 帧": QCoreApplication.translate(
            "StatusMessageCatalog", "已设置序列入出点：%1–%2 帧"
        ),
        "已设置序列入出点：%1–%2 帧；未发现启用的字幕，只处理了黑屏": QCoreApplication.translate(
            "StatusMessageCatalog", "已设置序列入出点：%1–%2 帧；未发现启用的字幕，只处理了黑屏"
        ),
        "已设置序列入出点：%1–%2 帧；结果已应用到原序列": QCoreApplication.translate(
            "StatusMessageCatalog", "已设置序列入出点：%1–%2 帧；结果已应用到原序列"
        ),
        (
            "已设置序列入出点：%1–%2 帧；未发现启用的字幕，"
            "只处理了黑屏；结果已应用到原序列"
        ): QCoreApplication.translate(
            "StatusMessageCatalog",
            "已设置序列入出点：%1–%2 帧；未发现启用的字幕，"
            "只处理了黑屏；结果已应用到原序列",
        ),
        "已设置序列入点": QCoreApplication.translate("StatusMessageCatalog", "已设置序列入点"),
        "已设置序列出点": QCoreApplication.translate("StatusMessageCatalog", "已设置序列出点"),
        "已移动序列字幕": QCoreApplication.translate("StatusMessageCatalog", "已移动序列字幕"),
        "已调整序列字幕时间": QCoreApplication.translate("StatusMessageCatalog", "已调整序列字幕时间"),
        "已调整序列入出点": QCoreApplication.translate("StatusMessageCatalog", "已调整序列入出点"),
        "已清除序列入出点": QCoreApplication.translate("StatusMessageCatalog", "已清除序列入出点"),
        "已移除任务记录，任务产物仍保留": QCoreApplication.translate(
            "StatusMessageCatalog", "已移除任务记录，任务产物仍保留"
        ),
        "已解除复合片段": QCoreApplication.translate("StatusMessageCatalog", "已解除复合片段"),
        "已解除视音频绑定；当前仅选中视频。点击空白处或按 Esc 可清除选择": QCoreApplication.translate(
            "StatusMessageCatalog", "已解除视音频绑定；当前仅选中视频。点击空白处或按 Esc 可清除选择"
        ),
        "已请求取消 %1 个任务": QCoreApplication.translate("StatusMessageCatalog", "已请求取消 %1 个任务"),
        "已请求取消任务": QCoreApplication.translate("StatusMessageCatalog", "已请求取消任务"),
        "已请求取消运行时工具操作": QCoreApplication.translate(
            "StatusMessageCatalog", "已请求取消运行时工具操作"
        ),
        "已请求暂停 %1 个任务": QCoreApplication.translate("StatusMessageCatalog", "已请求暂停 %1 个任务"),
        "已请求暂停任务": QCoreApplication.translate("StatusMessageCatalog", "已请求暂停任务"),
        "已选择水印 %1": QCoreApplication.translate("StatusMessageCatalog", "已选择水印 %1"),
        "已重新关联 %1 个素材": QCoreApplication.translate("StatusMessageCatalog", "已重新关联 %1 个素材"),
        "已重新关联 %1 个素材，仍有 %2 个未找到": QCoreApplication.translate(
            "StatusMessageCatalog", "已重新关联 %1 个素材，仍有 %2 个未找到"
        ),
        "已重新创建任务": QCoreApplication.translate("StatusMessageCatalog", "已重新创建任务"),
        "已跳过工作流阶段：%1": QCoreApplication.translate("StatusMessageCatalog", "已跳过工作流阶段：%1"),
        "序列配置已更新": QCoreApplication.translate("StatusMessageCatalog", "序列配置已更新"),
        "智能拆分完成，共拆分 %1 条": QCoreApplication.translate(
            "StatusMessageCatalog", "智能拆分完成，共拆分 %1 条"
        ),
        "术语已保存": QCoreApplication.translate("StatusMessageCatalog", "术语已保存"),
        "术语已移除": QCoreApplication.translate("StatusMessageCatalog", "术语已移除"),
        "正在关闭项目并释放文件…": QCoreApplication.translate(
            "StatusMessageCatalog", "正在关闭项目并释放文件…"
        ),
        "正在分析画面主体": QCoreApplication.translate("StatusMessageCatalog", "正在分析画面主体"),
        "正在导入 %1": QCoreApplication.translate("StatusMessageCatalog", "正在导入 %1"),
        "正在导入 %1 个素材": QCoreApplication.translate("StatusMessageCatalog", "正在导入 %1 个素材"),
        "正在导入水印 %1": QCoreApplication.translate("StatusMessageCatalog", "正在导入水印 %1"),
        "正在检测场景切点": QCoreApplication.translate("StatusMessageCatalog", "正在检测场景切点"),
        "片段素材已替换": QCoreApplication.translate("StatusMessageCatalog", "片段素材已替换"),
        "画面跟踪已应用": QCoreApplication.translate("StatusMessageCatalog", "画面跟踪已应用"),
        "离线素材已重新关联": QCoreApplication.translate("StatusMessageCatalog", "离线素材已重新关联"),
        "素材文件夹已更新": QCoreApplication.translate("StatusMessageCatalog", "素材文件夹已更新"),
        "视觉效果已更新": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已更新"),
        "视觉效果已添加": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已添加"),
        "视觉效果已移除": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已移除"),
        "视觉效果顺序已更新": QCoreApplication.translate("StatusMessageCatalog", "视觉效果顺序已更新"),
        "设置已保存；界面语言将在下次启动时生效": QCoreApplication.translate(
            "StatusMessageCatalog", "设置已保存；界面语言将在下次启动时生效"
        ),
        "示例项目已创建；跟随引导认识主要区域": QCoreApplication.translate(
            "StatusMessageCatalog", "示例项目已创建；跟随引导认识主要区域"
        ),
        "译文已保存": QCoreApplication.translate("StatusMessageCatalog", "译文已保存"),
        "该域名没有已保存的 Cookie": QCoreApplication.translate(
            "StatusMessageCatalog", "该域名没有已保存的 Cookie"
        ),
        "该高光区间已经位于主序列中": QCoreApplication.translate(
            "StatusMessageCatalog", "该高光区间已经位于主序列中"
        ),
        "转场已添加": QCoreApplication.translate("StatusMessageCatalog", "转场已添加"),
        "转录设置已更新": QCoreApplication.translate("StatusMessageCatalog", "转录设置已更新"),
        "运行时工具操作已取消": QCoreApplication.translate("StatusMessageCatalog", "运行时工具操作已取消"),
        "运行时工具操作已完成": QCoreApplication.translate("StatusMessageCatalog", "运行时工具操作已完成"),
        "错误详情已复制": QCoreApplication.translate("StatusMessageCatalog", "错误详情已复制"),
        "项目已保存": QCoreApplication.translate("StatusMessageCatalog", "项目已保存"),
        "项目已关闭：%1": QCoreApplication.translate("StatusMessageCatalog", "项目已关闭：%1"),
        "项目已创建": QCoreApplication.translate("StatusMessageCatalog", "项目已创建"),
        "项目已创建，正在下载视频": QCoreApplication.translate(
            "StatusMessageCatalog", "项目已创建，正在下载视频"
        ),
        "项目已打开": QCoreApplication.translate("StatusMessageCatalog", "项目已打开"),
        "项目正被其他窗口使用，已只读打开": QCoreApplication.translate(
            "StatusMessageCatalog", "项目正被其他窗口使用，已只读打开"
        ),
    }
    try:
        result = templates[source]
    except KeyError as error:
        raise ValueError(f"Unregistered status message source: {source}") from error
    for index, argument in enumerate(arguments, start=1):
        result = result.replace(f"%{index}", str(argument))
    return result


def encoder_label(label_key: str) -> str:
    labels = {
        "h264_software": QCoreApplication.translate("EncoderCatalog", "H.264 软件"),
        "h264_nvidia": "H.264 NVIDIA",
        "h264_intel_qsv": "H.264 Intel QSV",
        "h264_amd_amf": "H.264 AMD AMF",
        "h264_apple_videotoolbox": "H.264 Apple VideoToolbox",
        "h264_linux_vaapi": "H.264 Linux VAAPI",
        "hevc_software": QCoreApplication.translate("EncoderCatalog", "HEVC 软件"),
        "hevc_nvidia": "HEVC NVIDIA",
        "hevc_intel_qsv": "HEVC Intel QSV",
        "hevc_amd_amf": "HEVC AMD AMF",
        "hevc_apple_videotoolbox": "HEVC Apple VideoToolbox",
        "hevc_linux_vaapi": "HEVC Linux VAAPI",
        "av1_svt_software": QCoreApplication.translate("EncoderCatalog", "AV1 SVT 软件"),
        "av1_nvidia": "AV1 NVIDIA",
        "av1_intel_qsv": "AV1 Intel QSV",
        "av1_amd_amf": "AV1 AMD AMF",
        "av1_apple_videotoolbox": "AV1 Apple VideoToolbox",
        "av1_linux_vaapi": "AV1 Linux VAAPI",
        "prores_software": QCoreApplication.translate("EncoderCatalog", "ProRes 软件"),
        "h264_hardware_auto": QCoreApplication.translate("EncoderCatalog", "H.264 硬件优先（自动）"),
        "h264_hardware_nvidia": "H.264 NVIDIA",
        "h264_hardware_intel": "H.264 Intel",
        "h264_hardware_amd": "H.264 AMD",
        "h264_hardware_apple": "H.264 Apple",
        "hevc_hardware_auto": QCoreApplication.translate("EncoderCatalog", "HEVC 硬件优先（自动）"),
        "hevc_hardware_nvidia": "HEVC NVIDIA",
        "hevc_hardware_intel": "HEVC Intel",
        "hevc_hardware_amd": "HEVC AMD",
        "hevc_hardware_apple": "HEVC Apple",
        "av1_hardware_auto": QCoreApplication.translate("EncoderCatalog", "AV1 硬件优先（自动）"),
        "av1_hardware_nvidia": "AV1 NVIDIA",
        "av1_hardware_intel": "AV1 Intel",
        "av1_hardware_amd": "AV1 AMD",
        "av1_hardware_apple": "AV1 Apple",
    }
    return labels[label_key]


def no_subtitle_burn_label() -> str:
    return QCoreApplication.translate("ExportCatalog", "不烧录")


def task_title(task: Task) -> str:
    command = task.command
    if isinstance(command, ImportAssetCommand):
        template = QCoreApplication.translate("TaskCatalog", "导入素材 %1")
        return template.replace("%1", command.source_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    if isinstance(command, GenerateProxyCommand):
        return QCoreApplication.translate("TaskCatalog", "准备流畅预览")
    if isinstance(command, GenerateWaveformCommand):
        return QCoreApplication.translate("TaskCatalog", "准备音频波形")
    if isinstance(command, DownloadMediaCommand):
        template = QCoreApplication.translate("TaskCatalog", "下载 %1 %2")
        return template.replace("%1", f"{command.request.entry.index:03d}").replace(
            "%2", command.request.entry.title
        )
    if isinstance(command, ExportSequenceCommand):
        template = QCoreApplication.translate("TaskCatalog", "导出 %1")
        return template.replace("%1", command.format.value.upper())
    if isinstance(command, ExportHighlightsCommand):
        return QCoreApplication.translate("TaskCatalog", "批量导出短视频")
    if isinstance(command, RenderWebClipCommand):
        return QCoreApplication.translate("TaskCatalog", "渲染网页片段")
    if isinstance(command, TranscribeSequenceCommand):
        return QCoreApplication.translate("TaskCatalog", "转录当前时间轴")
    if isinstance(command, TranslateSegmentsCommand):
        return QCoreApplication.translate("TaskCatalog", "翻译所选字幕")
    if isinstance(command, TranslateDocumentCommand):
        if command.mode == "proofread":
            return QCoreApplication.translate("TaskCatalog", "校对字幕")
        return QCoreApplication.translate("TaskCatalog", "翻译字幕")
    if isinstance(command, AnalyzeHighlightsCommand):
        return QCoreApplication.translate("TaskCatalog", "AI 高光分析")
    if isinstance(command, AnalyzeDownloadCommand):
        return QCoreApplication.translate("TaskCatalog", "分析下载链接")
    if isinstance(command, AnalyzeSequenceBoundsCommand):
        return QCoreApplication.translate("TaskCatalog", "智能设置序列入出点")
    if isinstance(command, AnalyzeLoudnessCommand):
        return QCoreApplication.translate("TaskCatalog", "测量序列响度")
    if isinstance(command, AnalyzeScenesCommand):
        return QCoreApplication.translate("TaskCatalog", "检测场景切点")
    if isinstance(command, TrackSubjectCommand):
        label = "自动构图" if command.mode == "auto_reframe" else "主体跟踪"
        return QCoreApplication.translate("TaskCatalog", label)
    raise TypeError(f"Unknown task command: {type(command).__name__}")


def task_status_label(status: str) -> str:
    labels = {
        "pending": QCoreApplication.translate("TaskStatusCatalog", "等待中"),
        "running": QCoreApplication.translate("TaskStatusCatalog", "运行中"),
        "paused": QCoreApplication.translate("TaskStatusCatalog", "已暂停"),
        "completed": QCoreApplication.translate("TaskStatusCatalog", "已完成"),
        "failed": QCoreApplication.translate("TaskStatusCatalog", "失败"),
        "cancelled": QCoreApplication.translate("TaskStatusCatalog", "已取消"),
    }
    return labels.get(status, status)


def export_recovery_configuration_label(outcome: ExportTaskOutcome | None) -> str:
    if outcome is None:
        return ""
    recovered = [item for item in outcome.files if item.hardware_fallback_used]
    if not recovered:
        return ""
    if len(recovered) == 1:
        item = recovered[0]
        template = QCoreApplication.translate(
            "TaskCatalog",
            "硬件编码失败，已从 %1 切换为 %2",
        )
        return template.replace("%1", item.requested_video_codec or "").replace(
            "%2", item.actual_video_codec or ""
        )
    template = QCoreApplication.translate(
        "TaskCatalog",
        "%1 个文件的硬件编码失败，已自动改用软件编码",
    )
    return template.replace("%1", str(len(recovered)))


def task_message_label(code: str) -> str:
    labels = {
        "queued": QCoreApplication.translate("TaskMessageCatalog", "已排队"),
        "running": QCoreApplication.translate("TaskMessageCatalog", "正在运行"),
        "completed": QCoreApplication.translate("TaskMessageCatalog", "已完成"),
        "failed": QCoreApplication.translate("TaskMessageCatalog", "任务失败"),
        "cancelled": QCoreApplication.translate("TaskMessageCatalog", "已取消"),
        "interrupted_by_restart": QCoreApplication.translate("TaskMessageCatalog", "因应用重启而暂停"),
        "downloading": QCoreApplication.translate("TaskMessageCatalog", "正在下载"),
        "postprocessing": QCoreApplication.translate("TaskMessageCatalog", "正在整理下载文件"),
        "download_registering": QCoreApplication.translate("TaskMessageCatalog", "正在登记下载素材"),
        "loading_asr_model": QCoreApplication.translate("TaskMessageCatalog", "正在加载转录模型"),
        "preparing_asr_audio_probe": QCoreApplication.translate("TaskMessageCatalog", "正在读取转录音频信息"),
        "preparing_asr_channel_analysis": QCoreApplication.translate(
            "TaskMessageCatalog", "正在检查音频声道"
        ),
        "preparing_asr_audio": QCoreApplication.translate("TaskMessageCatalog", "正在准备转录音频"),
        "transcription_source_cached": QCoreApplication.translate(
            "TaskMessageCatalog", "正在复用已有源区间转录"
        ),
        "transcription_mapping_timeline": QCoreApplication.translate(
            "TaskMessageCatalog", "正在按剪辑位置生成时间线字幕"
        ),
        "transcription_regions_completed": QCoreApplication.translate(
            "TaskMessageCatalog", "当前源音频区间识别完成"
        ),
        "transcribing": QCoreApplication.translate("TaskMessageCatalog", "正在转录"),
        "asr_silence_detection": QCoreApplication.translate("TaskMessageCatalog", "正在检测长音频静音位置"),
        "asr_chunk_extracting": QCoreApplication.translate("TaskMessageCatalog", "正在生成长音频分块"),
        "asr_chunks_transcribing": QCoreApplication.translate("TaskMessageCatalog", "正在并行转录长音频分块"),
        "asr_cuda_cpu_fallback": QCoreApplication.translate(
            "TaskMessageCatalog", "CUDA 不可用，正在切换到 CPU"
        ),
        "asr_cli_starting": QCoreApplication.translate("TaskMessageCatalog", "正在启动转录引擎"),
        "translating": QCoreApplication.translate("TaskMessageCatalog", "正在翻译"),
        "proofreading": QCoreApplication.translate("TaskMessageCatalog", "正在校对"),
        "translation_saving": QCoreApplication.translate("TaskMessageCatalog", "正在保存翻译结果"),
        "highlight_analyzing": QCoreApplication.translate("TaskMessageCatalog", "正在分析高光"),
        "highlight_saving": QCoreApplication.translate("TaskMessageCatalog", "正在保存高光结果"),
        "proxy_encoding": QCoreApplication.translate("TaskMessageCatalog", "正在创建轻量预览文件"),
        "proxy_sdr_encoding": QCoreApplication.translate("TaskMessageCatalog", "正在创建 SDR 轻量预览文件"),
        "proxy_registering": QCoreApplication.translate("TaskMessageCatalog", "正在登记轻量预览文件"),
        "waveform_decoding": QCoreApplication.translate("TaskMessageCatalog", "正在准备音频波形"),
        "waveform_calculating": QCoreApplication.translate("TaskMessageCatalog", "正在计算音频波形"),
        "waveform_saving": QCoreApplication.translate("TaskMessageCatalog", "正在保存音频波形"),
        "import_probing": QCoreApplication.translate("TaskMessageCatalog", "正在读取素材信息"),
        "import_registering": QCoreApplication.translate("TaskMessageCatalog", "正在登记素材"),
        "export_compiling": QCoreApplication.translate("TaskMessageCatalog", "正在编译时间线"),
        "export_rendering": QCoreApplication.translate("TaskMessageCatalog", "正在导出时间线"),
        "export_hardware_encoder_fallback": QCoreApplication.translate(
            "TaskMessageCatalog", "硬件编码失败，正在改用软件编码"
        ),
        "export_verifying": QCoreApplication.translate("TaskMessageCatalog", "正在验证导出文件"),
        "export_quality_scanning": QCoreApplication.translate("TaskMessageCatalog", "正在扫描成片质量"),
        "export_quality_proof_frames": QCoreApplication.translate("TaskMessageCatalog", "正在生成成片证明帧"),
        "export_quality_hashing": QCoreApplication.translate("TaskMessageCatalog", "正在校验成片文件"),
        "web_render_preparing": QCoreApplication.translate("TaskMessageCatalog", "正在准备网页画面"),
        "web_rendering": QCoreApplication.translate("TaskMessageCatalog", "正在渲染网页画面"),
        "web_render_items": QCoreApplication.translate("TaskMessageCatalog", "正在处理网页素材"),
        "web_render_cache_ready": QCoreApplication.translate("TaskMessageCatalog", "正在复用已有网页画面"),
        "web_export_copying": QCoreApplication.translate("TaskMessageCatalog", "正在复制网页素材结果"),
        "web_export_encoding": QCoreApplication.translate("TaskMessageCatalog", "正在编码网页素材"),
        "scene_detection_preparing": QCoreApplication.translate("TaskMessageCatalog", "正在准备场景检测"),
        "scene_detection_analyzing": QCoreApplication.translate("TaskMessageCatalog", "正在检测场景切点"),
        "scene_detection_saving": QCoreApplication.translate("TaskMessageCatalog", "正在保存场景切点"),
        "subject_tracking_preparing": QCoreApplication.translate("TaskMessageCatalog", "正在准备画面分析"),
        "subject_tracking_analyzing": QCoreApplication.translate("TaskMessageCatalog", "正在跟踪画面主体"),
        "subject_tracking_saving": QCoreApplication.translate("TaskMessageCatalog", "正在保存画面跟踪结果"),
        "clip_export_items": QCoreApplication.translate("TaskMessageCatalog", "正在导出短视频"),
        "download_analyzing": QCoreApplication.translate("TaskMessageCatalog", "正在分析下载链接"),
        "download_analysis_saving": QCoreApplication.translate("TaskMessageCatalog", "正在保存下载分析结果"),
        "audio_analysis_compiling": QCoreApplication.translate("TaskMessageCatalog", "正在编译音频图"),
        "audio_analysis_waiting": QCoreApplication.translate("TaskMessageCatalog", "正在等待相同响度分析"),
        "audio_analysis_cache_ready": QCoreApplication.translate(
            "TaskMessageCatalog", "正在复用响度分析结果"
        ),
        "audio_analysis_rendering": QCoreApplication.translate("TaskMessageCatalog", "正在渲染分析音频"),
        "audio_analysis_measuring_loudness": QCoreApplication.translate("TaskMessageCatalog", "正在测量响度"),
        "audio_analysis_measuring_peak": QCoreApplication.translate("TaskMessageCatalog", "正在测量峰值"),
        "audio_analysis_saving": QCoreApplication.translate("TaskMessageCatalog", "正在保存响度结果"),
        "sequence_boundary_compiling": QCoreApplication.translate(
            "TaskMessageCatalog", "正在编译最终时间线画面"
        ),
        "sequence_boundary_leading_rendering": QCoreApplication.translate(
            "TaskMessageCatalog", "正在渲染片头检测画面"
        ),
        "sequence_boundary_leading_scanning": QCoreApplication.translate(
            "TaskMessageCatalog", "正在检测片头黑屏"
        ),
        "sequence_boundary_trailing_rendering": QCoreApplication.translate(
            "TaskMessageCatalog", "正在渲染片尾检测画面"
        ),
        "sequence_boundary_trailing_scanning": QCoreApplication.translate(
            "TaskMessageCatalog", "正在检测片尾黑屏"
        ),
        "sequence_boundary_speech": QCoreApplication.translate("TaskMessageCatalog", "正在读取字幕对白范围"),
        "sequence_boundary_saving": QCoreApplication.translate(
            "TaskMessageCatalog", "正在保存序列入出点分析"
        ),
        "workflow_cancelled": QCoreApplication.translate("TaskMessageCatalog", "工作流已取消"),
        "workflow_complete": QCoreApplication.translate("TaskMessageCatalog", "工作流已完成"),
    }
    if code in labels:
        return labels[code]
    if code.startswith("workflow_") and code.endswith("_ready"):
        return QCoreApplication.translate("TaskMessageCatalog", "上一阶段已完成")
    return code.replace("_", " ")


def export_format_options(color_mode: ColorMode) -> list[dict]:
    filters = {
        "mp4": QCoreApplication.translate("ExportCatalog", "MP4 视频 (*.mp4)"),
        "mkv": QCoreApplication.translate("ExportCatalog", "MKV 视频 (*.mkv)"),
        "mov": QCoreApplication.translate("ExportCatalog", "MOV 视频 (*.mov)"),
        "m4a": QCoreApplication.translate("ExportCatalog", "M4A 音频 (*.m4a)"),
        "ogg": QCoreApplication.translate("ExportCatalog", "OGG 音频 (*.ogg)"),
        "wav": QCoreApplication.translate("ExportCatalog", "WAV 音频 (*.wav)"),
        "flac": QCoreApplication.translate("ExportCatalog", "FLAC 音频 (*.flac)"),
    }
    options: list[dict] = []
    for variant in available_export_variants(color_mode):
        label = _EXPORT_LABELS[variant.id]
        if color_mode == ColorMode.HDR10_BT2020_PQ and variant.id == "hevc":
            label = "HEVC Main10"
        elif color_mode == ColorMode.HDR10_BT2020_PQ and variant.id == "av1":
            label = "AV1 10-bit"
        option = {
            "id": variant.id,
            "label": label,
            "value": variant.format.value,
            "suffix": variant.suffix,
            "container": variant.container,
            "encoderPolicy": (
                variant.encoder_policy.model_dump(mode="json") if variant.encoder_policy is not None else None
            ),
            "audioCodec": variant.audio_codec,
            "pixelFormat": variant.pixel_format(color_mode) or "",
            "qualityValue": variant.quality_value,
            "preset": variant.preset,
            "filter": filters[variant.suffix],
        }
        if variant.prores_profile is not None:
            option["profile"] = variant.prores_profile
        options.append(option)
    return options


def translation_mode_options() -> list[dict[str, str]]:
    labels = {
        "standard": QCoreApplication.translate("TranslationCatalog", "标准翻译"),
        "intelligent": QCoreApplication.translate("TranslationCatalog", "智能翻译"),
        "proofread": QCoreApplication.translate("TranslationCatalog", "原文校对"),
    }
    return [
        {
            "label": labels[value],
            "value": value,
        }
        for value in TRANSLATION_MODES
    ]


def translation_language_options() -> list[dict[str, str]]:
    labels = {
        "zh_CN": QCoreApplication.translate("TranslationCatalog", "简体中文"),
        "en": QCoreApplication.translate("TranslationCatalog", "英语"),
        "ja": QCoreApplication.translate("TranslationCatalog", "日语"),
        "zh_TW": QCoreApplication.translate("TranslationCatalog", "繁体中文"),
        "ko": QCoreApplication.translate("TranslationCatalog", "韩语"),
        "es": QCoreApplication.translate("TranslationCatalog", "西班牙语"),
        "fr": QCoreApplication.translate("TranslationCatalog", "法语"),
        "de": QCoreApplication.translate("TranslationCatalog", "德语"),
        "ru": QCoreApplication.translate("TranslationCatalog", "俄语"),
    }
    return [
        {
            "label": labels[value],
            "value": value,
        }
        for value in TRANSLATION_LANGUAGES
    ]


def transition_options(color_mode: ColorMode) -> list[dict[str, object]]:
    return [
        {
            "label": QCoreApplication.translate("TransitionCatalog", capability.label_key),
            "value": kind.value,
            "category": capability.category,
            "description": QCoreApplication.translate("TransitionCatalog", capability.description_key),
            "previewDirection": capability.preview_direction,
            "defaultDurationFrames": capability.default_duration_frames,
            "minimumBitDepth": capability.minimum_bit_depth,
            "hdr10Verified": capability.hdr10_verified,
        }
        for kind, capability in TRANSITION_CAPABILITIES.items()
        if transition_is_available(kind, color_mode)
    ]


def audio_effect_label(kind: AudioEffectKind) -> str:
    labels = {
        AudioEffectKind.PARAMETRIC_EQ: QCoreApplication.translate("AudioCatalog", "参数均衡器"),
        AudioEffectKind.HIGH_PASS: QCoreApplication.translate("AudioCatalog", "高通"),
        AudioEffectKind.LOW_PASS: QCoreApplication.translate("AudioCatalog", "低通"),
        AudioEffectKind.COMPRESSOR: QCoreApplication.translate("AudioCatalog", "压缩器"),
        AudioEffectKind.LIMITER: QCoreApplication.translate("AudioCatalog", "限制器"),
        AudioEffectKind.NOISE_GATE: QCoreApplication.translate("AudioCatalog", "噪声门"),
        AudioEffectKind.RNNOISE: "RNNoise",
        AudioEffectKind.CHANNEL_MAP: QCoreApplication.translate("AudioCatalog", "声道映射"),
        AudioEffectKind.LOUDNESS_NORMALIZE: QCoreApplication.translate("AudioCatalog", "响度标准化"),
        AudioEffectKind.DUCKING: QCoreApplication.translate("AudioCatalog", "自动闪避"),
    }
    return labels[kind]


def audio_parameter_specs(kind: AudioEffectKind) -> list[dict]:
    labels = {
        "low_db": QCoreApplication.translate("AudioCatalog", "低频增益"),
        "low_mid_db": QCoreApplication.translate("AudioCatalog", "中低频增益"),
        "high_mid_db": QCoreApplication.translate("AudioCatalog", "中高频增益"),
        "high_db": QCoreApplication.translate("AudioCatalog", "高频增益"),
        "frequency_hz": QCoreApplication.translate("AudioCatalog", "截止频率"),
        "threshold_db": QCoreApplication.translate("AudioCatalog", "阈值"),
        "ratio": QCoreApplication.translate("AudioCatalog", "压缩比"),
        "attack_ms": QCoreApplication.translate("AudioCatalog", "启动时间"),
        "release_ms": QCoreApplication.translate("AudioCatalog", "释放时间"),
        "ceiling_db": QCoreApplication.translate("AudioCatalog", "上限"),
        "mix": QCoreApplication.translate("AudioCatalog", "混合"),
        "layout": QCoreApplication.translate("AudioCatalog", "声道布局"),
        "target_lufs": QCoreApplication.translate("AudioCatalog", "目标响度"),
        "true_peak_db": QCoreApplication.translate("AudioCatalog", "True Peak 上限"),
        "driver_bus_id": QCoreApplication.translate("AudioCatalog", "驱动总线"),
        "reduction_db": QCoreApplication.translate("AudioCatalog", "衰减量"),
    }
    schema = audio_effect_parameter_schema(kind)
    return [
        {
            "key": key,
            "label": labels[key],
            "minimum": float(spec.get("minimum", 0.0)),
            "maximum": float(spec.get("maximum", 0.0)),
            "step": float(spec["step"]),
            "unit": str(spec["unit"]),
            "valueType": str(spec.get("value_type", "number")),
        }
        for key, spec in schema.items()
    ]


def audio_preset_options(kind: AudioEffectKind) -> list[dict[str, str]]:
    labels = {
        "default": QCoreApplication.translate("AudioCatalog", "默认"),
        "dialogue": QCoreApplication.translate("AudioCatalog", "对白"),
        "gentle": QCoreApplication.translate("AudioCatalog", "轻柔"),
        "strong": QCoreApplication.translate("AudioCatalog", "强力"),
        "social": QCoreApplication.translate("AudioCatalog", "社交平台"),
        "web": QCoreApplication.translate("AudioCatalog", "网络视频"),
        "broadcast": QCoreApplication.translate("AudioCatalog", "广播"),
        "mono": QCoreApplication.translate("AudioCatalog", "单声道"),
        "stereo": QCoreApplication.translate("AudioCatalog", "立体声"),
        "5.1": "5.1",
    }
    return [
        {"presetId": preset_id, "label": labels[preset_id]} for preset_id in audio_effect_preset_ids(kind)
    ]


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


def llm_provider_presets() -> list[dict[str, str]]:
    return [
        {
            "text": "DeepSeek",
            "value": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
        {
            "text": "OpenAI",
            "value": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "model": "gpt-4o",
        },
        {
            "text": "Google Gemini",
            "value": "google-gemini",
            "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "model": "gemini-1.5-flash",
        },
        {
            "text": "Anthropic Claude",
            "value": "anthropic-claude",
            "baseUrl": "https://api.anthropic.com/v1",
            "model": "claude-3-5-sonnet-20240620",
        },
        {
            "text": "GLM",
            "value": "glm",
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-5.1",
        },
        {
            "text": "MiniMax",
            "value": "minimax",
            "baseUrl": "https://api.minimax.io/v1",
            "model": "MiniMax-M2.7",
        },
        {
            "text": "SiliconFlow",
            "value": "siliconflow",
            "baseUrl": "https://api.siliconflow.cn/v1",
            "model": "deepseek-ai/DeepSeek-V3",
        },
        {
            "text": QCoreApplication.translate("LlmProviderCatalog", "自定义 / 本地"),
            "value": "custom-local",
            "baseUrl": "",
            "model": "",
        },
    ]
