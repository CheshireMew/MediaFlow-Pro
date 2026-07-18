import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    objectName: "downloadProgressBanner"
    visible: taskController.downloadProgressVisible
    color: "#132b37"
    border.color: Theme.accent

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 14
        spacing: 14

        Text {
            text: "↓"
            color: Theme.accentHover
            font.pixelSize: 24
            font.weight: Font.Bold
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 5
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: taskController.activeDownloadCount > 1
                          ? qsTr("正在下载 %1 个视频").arg(taskController.activeDownloadCount)
                          : qsTr("正在下载：%1").arg(taskController.activeDownloadTitle)
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    text: Math.round(taskController.downloadProgress) + "%"
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
            ProgressBar {
                id: downloadProgressBar
                objectName: "downloadProgressBar"
                Layout.fillWidth: true
                from: 0
                to: 100
                value: taskController.downloadProgress
            }
        }
        AppButton {
            text: qsTr("任务详情")
            onClicked: {
                if (!taskController.taskDrawerOpen)
                    taskController.toggleTaskDrawer()
            }
        }
        AppButton {
            text: qsTr("取消下载")
            danger: true
            onClicked: workspaceController.cancelWorkflow(workspaceController.workflowRunId)
        }
    }
}
