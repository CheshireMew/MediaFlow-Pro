from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mediaflow.application.export_catalog import available_export_variants
from mediaflow.domain.audio import audio_effect_parameter_schema
from mediaflow.domain.audio_effect_presets import audio_effect_preset_ids
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import AudioEffectKind, ColorMode, TransitionKind
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeSequenceBoundsCommand,
    DownloadMediaCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    ImportAssetCommand,
    TranscribeAssetCommand,
    TranscribeRegionCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.domain.tasks import Task
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


def system_name(name: str) -> str:
    exact = {
        "主总线": QCoreApplication.translate("SystemNameCatalog", "主总线"),
        "对白": QCoreApplication.translate("SystemNameCatalog", "对白"),
        "音乐": QCoreApplication.translate("SystemNameCatalog", "音乐"),
        "效果": QCoreApplication.translate("SystemNameCatalog", "效果"),
    }
    if name in exact:
        return exact[name]
    prefixes = {
        "视频 ": QCoreApplication.translate("SystemNameCatalog", "视频 %1"),
        "音频 ": QCoreApplication.translate("SystemNameCatalog", "音频 %1"),
        "字幕 ": QCoreApplication.translate("SystemNameCatalog", "字幕 %1"),
    }
    for prefix, template in prefixes.items():
        suffix = name[len(prefix) :] if name.startswith(prefix) else ""
        if suffix.isdigit():
            return template.replace("%1", suffix)
    return name


def encoder_label(label_key: str) -> str:
    labels = {
        "h264_software": QCoreApplication.translate("EncoderCatalog", "H.264 软件"),
        "h264_nvidia": "H.264 NVIDIA",
        "h264_intel_qsv": "H.264 Intel QSV",
        "h264_amd_amf": "H.264 AMD AMF",
        "hevc_software": QCoreApplication.translate("EncoderCatalog", "HEVC 软件"),
        "hevc_nvidia": "HEVC NVIDIA",
        "hevc_intel_qsv": "HEVC Intel QSV",
        "hevc_amd_amf": "HEVC AMD AMF",
        "av1_svt_software": QCoreApplication.translate("EncoderCatalog", "AV1 SVT 软件"),
        "av1_nvidia": "AV1 NVIDIA",
        "av1_intel_qsv": "AV1 Intel QSV",
        "av1_amd_amf": "AV1 AMD AMF",
        "prores_software": QCoreApplication.translate("EncoderCatalog", "ProRes 软件"),
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
        return QCoreApplication.translate("TaskCatalog", "生成代理")
    if isinstance(command, GenerateWaveformCommand):
        return QCoreApplication.translate("TaskCatalog", "生成波形")
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
    if isinstance(command, TranscribeAssetCommand):
        return QCoreApplication.translate("TaskCatalog", "转录字幕")
    if isinstance(command, TranscribeRegionCommand):
        if command.translate_after:
            return QCoreApplication.translate("TaskCatalog", "选区转录并翻译")
        return QCoreApplication.translate("TaskCatalog", "选区转录")
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
        "loading_asr_model": QCoreApplication.translate("TaskMessageCatalog", "正在加载转录模型"),
        "preparing_asr_audio": QCoreApplication.translate("TaskMessageCatalog", "正在准备转录音频"),
        "transcribing": QCoreApplication.translate("TaskMessageCatalog", "正在转录"),
        "extracting_asr_region": QCoreApplication.translate("TaskMessageCatalog", "正在提取转录选区"),
        "asr_audio_splitting": QCoreApplication.translate("TaskMessageCatalog", "正在按静音位置切分长音频"),
        "asr_chunks_progress": QCoreApplication.translate("TaskMessageCatalog", "正在转录长音频分块"),
        "asr_cuda_cpu_fallback": QCoreApplication.translate(
            "TaskMessageCatalog", "CUDA 不可用，正在切换到 CPU"
        ),
        "asr_cli_starting": QCoreApplication.translate("TaskMessageCatalog", "正在启动转录引擎"),
        "transcription_completed": QCoreApplication.translate("TaskMessageCatalog", "转录完成"),
        "translating": QCoreApplication.translate("TaskMessageCatalog", "正在翻译"),
        "proofreading": QCoreApplication.translate("TaskMessageCatalog", "正在校对"),
        "translation_completed": QCoreApplication.translate("TaskMessageCatalog", "翻译完成"),
        "highlight_analyzing": QCoreApplication.translate("TaskMessageCatalog", "正在分析高光"),
        "highlight_completed": QCoreApplication.translate("TaskMessageCatalog", "高光分析完成"),
        "proxy_preparing": QCoreApplication.translate("TaskMessageCatalog", "正在准备代理"),
        "proxy_verifying": QCoreApplication.translate("TaskMessageCatalog", "正在验证代理"),
        "waveform_decoding": QCoreApplication.translate("TaskMessageCatalog", "正在生成波形"),
        "waveform_verifying": QCoreApplication.translate("TaskMessageCatalog", "正在验证波形"),
        "import_probing": QCoreApplication.translate("TaskMessageCatalog", "正在读取素材信息"),
        "import_registering": QCoreApplication.translate("TaskMessageCatalog", "正在登记素材"),
        "export_compiling": QCoreApplication.translate("TaskMessageCatalog", "正在编译时间线"),
        "export_verifying": QCoreApplication.translate("TaskMessageCatalog", "正在验证导出文件"),
        "clip_exporting": QCoreApplication.translate("TaskMessageCatalog", "正在导出短视频"),
        "clip_export_completed": QCoreApplication.translate("TaskMessageCatalog", "短视频批量导出完成"),
        "download_analyzing": QCoreApplication.translate("TaskMessageCatalog", "正在分析下载链接"),
        "download_analysis_ready": QCoreApplication.translate("TaskMessageCatalog", "下载分析完成"),
        "audio_analysis_compiling": QCoreApplication.translate("TaskMessageCatalog", "正在编译音频图"),
        "audio_analysis_measuring_loudness": QCoreApplication.translate("TaskMessageCatalog", "正在测量响度"),
        "audio_analysis_measuring_peak": QCoreApplication.translate("TaskMessageCatalog", "正在测量峰值"),
        "audio_analysis_complete": QCoreApplication.translate("TaskMessageCatalog", "响度测量完成"),
        "sequence_boundary_compiling": QCoreApplication.translate(
            "TaskMessageCatalog", "正在编译最终时间线画面"
        ),
        "sequence_boundary_leading_black": QCoreApplication.translate(
            "TaskMessageCatalog", "正在检测片头黑屏"
        ),
        "sequence_boundary_trailing_black": QCoreApplication.translate(
            "TaskMessageCatalog", "正在检测片尾黑屏"
        ),
        "sequence_boundary_speech": QCoreApplication.translate("TaskMessageCatalog", "正在读取字幕对白范围"),
        "sequence_boundary_complete": QCoreApplication.translate("TaskMessageCatalog", "序列入出点分析完成"),
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
            "videoCodec": variant.video_codec or "",
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


def transition_options(color_mode: ColorMode) -> list[dict[str, str]]:
    labels = {
        TransitionKind.DISSOLVE: QCoreApplication.translate("TransitionCatalog", "交叉溶解"),
        TransitionKind.FADE: QCoreApplication.translate("TransitionCatalog", "淡化"),
        TransitionKind.FADE_BLACK: QCoreApplication.translate("TransitionCatalog", "淡黑"),
        TransitionKind.WIPE_LEFT: QCoreApplication.translate("TransitionCatalog", "左擦除"),
        TransitionKind.WIPE_RIGHT: QCoreApplication.translate("TransitionCatalog", "右擦除"),
        TransitionKind.SLIDE_LEFT: QCoreApplication.translate("TransitionCatalog", "左滑动"),
        TransitionKind.SLIDE_RIGHT: QCoreApplication.translate("TransitionCatalog", "右滑动"),
        TransitionKind.ZOOM: QCoreApplication.translate("TransitionCatalog", "缩放"),
    }
    return [
        {
            "label": labels[kind],
            "value": kind.value,
        }
        for kind in TransitionKind
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
