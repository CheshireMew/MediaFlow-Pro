from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

_RESOURCE_UI_LABELS = frozenset(
    (
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "全部")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "MG 动画")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "音效素材")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "音频效果")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "转场")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "特效")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "缩放")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "LUT")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "收藏夹")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "热门")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "文字")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "进度")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "电影感")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "科技")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "音频")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "叠加")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "动效")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "内置")),
    )
)

_BUILTIN_RESOURCE_TEXTS = frozenset(
    (
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "交叉溶解")),
        str(
            QT_TRANSLATE_NOOP(
                "MediaResourcePresentation", "前后画面平滑叠化，适合大多数连续镜头。"
            )
        ),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "淡化")),
        str(
            QT_TRANSLATE_NOOP(
                "MediaResourcePresentation", "前一个镜头逐渐让位于后一个镜头。"
            )
        ),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "淡黑")),
        str(
            QT_TRANSLATE_NOOP(
                "MediaResourcePresentation", "经过黑场连接两个镜头，适合段落分隔。"
            )
        ),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "左擦除")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "新画面从右向左擦入。")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "右擦除")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "新画面从左向右擦入。")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "左滑动")),
        str(
            QT_TRANSLATE_NOOP(
                "MediaResourcePresentation", "两个镜头保持空间关系并一起向左移动。"
            )
        ),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "右滑动")),
        str(
            QT_TRANSLATE_NOOP(
                "MediaResourcePresentation", "两个镜头保持空间关系并一起向右移动。"
            )
        ),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "缩放")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "通过中心缩放连接两个镜头。")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "亮度 / 对比度 / 饱和度")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "高斯模糊")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "暗角")),
        str(
            QT_TRANSLATE_NOOP(
                "MediaResourcePresentation", "可编辑参数并进入片段视觉效果链。"
            )
        ),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "参数均衡器")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "高通")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "低通")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "压缩器")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "限制器")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "噪声门")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "RNNoise")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "声道映射")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "响度标准化")),
        str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "自动闪避")),
        str(
            QT_TRANSLATE_NOOP(
                "MediaResourcePresentation", "应用到所选音频总线，并保留完整可编辑参数。"
            )
        ),
    )
)

_BUILTIN_TAG_LABELS = {
    "blend": str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "Blend")),
    "motion": str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "Motion")),
    "audio": str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "Audio")),
    "builtin": str(QT_TRANSLATE_NOOP("MediaResourcePresentation", "Built-in")),
}


def _translate(source: str) -> str:
    return QCoreApplication.translate("MediaResourcePresentation", source)


def media_resource_ui_label(source: str) -> str:
    return _translate(source) if source in _RESOURCE_UI_LABELS else source


def builtin_media_resource_text(catalog_id: str, source: str) -> str:
    if catalog_id != "mediaflow-builtins" or source not in _BUILTIN_RESOURCE_TEXTS:
        return source
    return _translate(source)


def builtin_media_resource_tags(catalog_id: str, tags: Iterable[object]) -> list[str]:
    normalized = [str(tag) for tag in tags]
    if catalog_id != "mediaflow-builtins":
        return normalized
    return [_translate(_BUILTIN_TAG_LABELS.get(tag, tag)) for tag in normalized]
