from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    CommitDubbingCommand,
    DiagnosticsBundleCommand,
    DownloadMediaCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    ImportAssetCommand,
    PrepareDubbingCommand,
    RenderWebClipCommand,
    SynthesizeDubbingCommand,
    TrackSubjectCommand,
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.domain.tasks import ExportTaskOutcome, Task


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
    if isinstance(command, DiagnosticsBundleCommand):
        return QCoreApplication.translate("TaskCatalog", "生成诊断包")
    if isinstance(command, TranslateSegmentsCommand):
        return QCoreApplication.translate("TaskCatalog", "翻译所选字幕")
    if isinstance(command, TranslateDocumentCommand):
        if command.mode == "proofread":
            return QCoreApplication.translate("TaskCatalog", "校对字幕")
        return QCoreApplication.translate("TaskCatalog", "翻译字幕")
    if isinstance(command, PrepareDubbingCommand):
        return QCoreApplication.translate("TaskCatalog", "准备多人配音")
    if isinstance(command, SynthesizeDubbingCommand):
        return QCoreApplication.translate("TaskCatalog", "生成多人配音")
    if isinstance(command, CommitDubbingCommand):
        return QCoreApplication.translate("TaskCatalog", "提交多人配音到时间线")
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
        "dubbing_rendering_dialogue": QCoreApplication.translate("TaskMessageCatalog", "正在提取对白音轨"),
        "dubbing_identifying_speakers": QCoreApplication.translate(
            "TaskMessageCatalog", "正在识别不同说话人"
        ),
        "dubbing_extracting_references": QCoreApplication.translate(
            "TaskMessageCatalog", "正在提取说话人参考音色"
        ),
        "dubbing_synthesizing_utterances": QCoreApplication.translate(
            "TaskMessageCatalog", "正在逐句生成配音"
        ),
        "speaker_clustering_creating_environment": QCoreApplication.translate(
            "TaskMessageCatalog", "正在创建本地音色识别环境"
        ),
        "speaker_clustering_installing_runtime": QCoreApplication.translate(
            "TaskMessageCatalog", "正在安装本地音色识别运行库"
        ),
        "speaker_clustering_downloading_model": QCoreApplication.translate(
            "TaskMessageCatalog", "正在下载 3D-Speaker 音色模型"
        ),
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
        "diagnostics_collecting_project": QCoreApplication.translate(
            "TaskMessageCatalog", "正在收集项目诊断信息"
        ),
        "diagnostics_inspecting_runtime": QCoreApplication.translate(
            "TaskMessageCatalog", "正在检查媒体运行时"
        ),
        "diagnostics_probing_media": QCoreApplication.translate(
            "TaskMessageCatalog", "正在读取素材身份与媒体信息"
        ),
        "diagnostics_writing_bundle": QCoreApplication.translate("TaskMessageCatalog", "正在写入诊断包"),
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
