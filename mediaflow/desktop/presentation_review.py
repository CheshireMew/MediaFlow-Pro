from PySide6.QtCore import QCoreApplication


def review_filter_options() -> list[dict[str, str]]:
    return [
        {"label": QCoreApplication.translate("ReviewCatalog", "待处理"), "value": "open"},
        {"label": QCoreApplication.translate("ReviewCatalog", "已解决"), "value": "resolved"},
        {"label": QCoreApplication.translate("ReviewCatalog", "已归档"), "value": "archived"},
        {"label": QCoreApplication.translate("ReviewCatalog", "全部"), "value": "all"},
    ]


def review_priority_options() -> list[dict[str, str]]:
    return [
        {"label": QCoreApplication.translate("ReviewCatalog", "普通"), "value": "normal"},
        {"label": QCoreApplication.translate("ReviewCatalog", "低"), "value": "low"},
        {"label": QCoreApplication.translate("ReviewCatalog", "高"), "value": "high"},
        {"label": QCoreApplication.translate("ReviewCatalog", "阻断交付"), "value": "blocking"},
    ]
