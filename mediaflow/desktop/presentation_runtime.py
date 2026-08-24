from __future__ import annotations

from PySide6.QtCore import QCoreApplication


def runtime_status_text(source: str) -> str:
    fixed = {
        "尚未检测 CUDA": QCoreApplication.translate(
            "RuntimeStatusCatalog", "尚未检测 CUDA"
        ),
        "内置 faster-whisper 当前不能使用 CUDA，可继续使用 CPU": QCoreApplication.translate(
            "RuntimeStatusCatalog", "内置 faster-whisper 当前不能使用 CUDA，可继续使用 CPU"
        ),
        "该可选组件没有当前平台的受支持构建": QCoreApplication.translate(
            "RuntimeStatusCatalog", "该可选组件没有当前平台的受支持构建"
        ),
        "尚未安装或选择本地目录": QCoreApplication.translate(
            "RuntimeStatusCatalog", "尚未安装或选择本地目录"
        ),
        "尚未安装本地 3D-Speaker 音色模型": QCoreApplication.translate(
            "RuntimeStatusCatalog", "尚未安装本地 3D-Speaker 音色模型"
        ),
        "3D-Speaker 模型大小不正确，请重新安装": QCoreApplication.translate(
            "RuntimeStatusCatalog", "3D-Speaker 模型大小不正确，请重新安装"
        ),
        "运行环境不可用": QCoreApplication.translate(
            "RuntimeStatusCatalog", "运行环境不可用"
        ),
    }
    if source in fixed:
        return fixed[source]
    prefix = "CUDA 可用，检测到 "
    suffix = " 个设备"
    if source.startswith(prefix) and source.endswith(suffix):
        count = source.removeprefix(prefix).removesuffix(suffix)
        return QCoreApplication.translate(
            "RuntimeStatusCatalog", "CUDA 可用，检测到 %1 个设备"
        ).replace("%1", count)
    prefix = "本地说话人识别探测失败："
    if source.startswith(prefix):
        return QCoreApplication.translate(
            "RuntimeStatusCatalog", "本地说话人识别探测失败：%1"
        ).replace("%1", source.removeprefix(prefix))
    prefix = "探测失败："
    if source.startswith(prefix):
        return QCoreApplication.translate(
            "RuntimeStatusCatalog", "探测失败：%1"
        ).replace("%1", source.removeprefix(prefix))
    prefix = "探测退出码 "
    if source.startswith(prefix):
        return QCoreApplication.translate(
            "RuntimeStatusCatalog", "探测退出码 %1"
        ).replace("%1", source.removeprefix(prefix))
    return source


def localized_runtime_tool_status(status: dict) -> dict:
    localized = dict(status)
    localized["cudaSummary"] = runtime_status_text(str(status.get("cudaSummary") or ""))
    localized["message"] = runtime_status_text(str(status.get("message") or ""))
    components = status.get("components")
    if isinstance(components, dict):
        localized["components"] = {
            key: {
                **dict(value),
                "reason": runtime_status_text(str(value.get("reason") or "")),
            }
            if isinstance(value, dict)
            else value
            for key, value in components.items()
        }
    speaker = status.get("speakerClustering")
    if isinstance(speaker, dict):
        localized["speakerClustering"] = {
            **speaker,
            "reason": runtime_status_text(str(speaker.get("reason") or "")),
        }
    return localized
