import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    objectName: "downloadProgressBanner"
    visible: mediaflow.downloadController.downloadProgressVisible
    color: Theme.surfaceFloating
    radius: Theme.radius
    border.width: 1
    border.color: Theme.borderStrong
    clip: true

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.divider
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 14
        spacing: 14

        AppIcon {
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            iconName: "download"
            iconColor: Theme.accentHover
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 5
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: mediaflow.downloadController.activeDownloadCount > 1
                          ? qsTr("正在下载 %1 个视频").arg(mediaflow.downloadController.activeDownloadCount)
                          : qsTr("正在下载：%1").arg(mediaflow.downloadController.activeDownloadTitle)
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    visible: mediaflow.downloadController.downloadProgressDeterminate
                    text: Math.round(mediaflow.downloadController.downloadProgress) + "%"
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
            AppProgressBar {
                id: downloadProgressBar
                objectName: "downloadProgressBar"
                Layout.fillWidth: true
                from: 0
                to: 100
                indeterminate: !mediaflow.downloadController.downloadProgressDeterminate
                value: mediaflow.downloadController.downloadProgress
            }
        }
        AppButton {
            text: qsTr("任务详情")
            compact: true
            onClicked: mediaflow.taskController.openTaskCenter()
        }
        AppButton {
            text: qsTr("取消下载")
            compact: true
            danger: true
            enabled: mediaflow.workspaceViewController.actionCapabilities.canManageWorkflow
            onClicked: mediaflow.workspaceWorkflowController.cancelWorkflow(mediaflow.workspaceViewController.workflowRunId)
        }
    }
}
