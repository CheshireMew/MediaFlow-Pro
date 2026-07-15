import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    width: 380
    color: Theme.surface
    border.color: Theme.borderStrong

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 54
            color: Theme.surfaceRaised
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 10
                Text { text: qsTr("任务中心"); color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
                Item { Layout.fillWidth: true }
                AppButton { text: "×"; onClicked: projectController.toggleTaskDrawer() }
            }
        }
        ListView {
            id: taskList
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 10
            spacing: 8
            clip: true
            model: projectController.tasksModel
            delegate: Panel {
                required property string name
                required property string displayName
                required property string kind
                required property string status
                required property string statusLabel
                required property real progress
                required property string messageCode
                required property string messageLabel
                required property string error
                required property string taskId
                required property var artifacts
                width: taskList.width
                height: error.length > 0 ? 154 : 130
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 7
                    RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: displayName; color: Theme.text; font.weight: Font.DemiBold; elide: Text.ElideRight }
                        Text {
                            text: statusLabel
                            color: status === "completed" ? Theme.success : status === "failed" ? Theme.danger : Theme.accentHover
                            font.pixelSize: 11
                        }
                    }
                    ProgressBar {
                        Layout.fillWidth: true
                        from: 0
                        to: 100
                        value: progress
                    }
                    Text { text: messageLabel; color: Theme.textMuted; font.pixelSize: 11 }
                    Text { Layout.fillWidth: true; visible: error.length > 0; text: error; color: Theme.danger; font.pixelSize: 10; elide: Text.ElideRight }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        AppButton {
                            visible: status === "running"
                            text: qsTr("暂停")
                            onClicked: projectController.pauseTask(taskId)
                        }
                        AppButton {
                            visible: status === "paused"
                            text: qsTr("继续")
                            onClicked: projectController.resumeTask(taskId)
                        }
                        AppButton {
                            visible: status === "pending" || status === "running" || status === "paused"
                            text: qsTr("取消")
                            onClicked: projectController.cancelTask(taskId)
                        }
                        AppButton {
                            visible: status === "completed" && artifacts && artifacts.length > 0
                            text: qsTr("打开产物")
                            onClicked: projectController.openArtifact(String(artifacts[0]))
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
            EmptyState {
                anchors.fill: parent
                visible: taskList.count === 0
                iconText: "✓"
                title: qsTr("没有后台任务")
                description: qsTr("下载、代理、转录、翻译和导出进度会显示在这里。")
            }
        }
    }
}
