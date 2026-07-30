import QtQuick
import "."

Item {
    id: root

    required property real toolPanelWidth
    required property real previewPanelWidth
    required property real gutter

    signal openSettingsRequested
    signal openExportRequested

    readonly property real previewPanelX: gutter * 2 + toolPanelWidth
    readonly property real overlayWidth: Math.min(
        760, Math.max(360, previewPanelWidth - 24))

    DownloadProgressBanner {
        anchors.top: parent.top
        anchors.topMargin: 70
        x: root.previewPanelX + (root.previewPanelWidth - width) / 2
        width: root.overlayWidth
        height: 64
    }

    WorkflowBanner {
        anchors.top: parent.top
        anchors.topMargin: 70
        x: root.previewPanelX + (root.previewPanelWidth - width) / 2
        width: root.overlayWidth
        height: 64
        onOpenSettingsRequested: root.openSettingsRequested()
        onOpenExportRequested: root.openExportRequested()
    }
}
