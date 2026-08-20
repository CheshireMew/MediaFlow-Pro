import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "errorHistoryDialog"
    title: qsTr("错误记录")
    width: Math.min(680, parent ? parent.width - 40 : 680)
    height: Math.min(640, parent ? parent.height - 40 : 640)
    modal: true
    closePolicy: Popup.CloseOnEscape
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        width: root.availableWidth
        height: root.availableHeight
        spacing: 10

        Text {
            Layout.fillWidth: true
            text: qsTr("这里保留本次运行中最近 50 条界面错误。任务失败仍保留在任务列表中。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }

        ListView {
            id: errorList
            objectName: "errorHistoryList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: mediaflow.workspaceViewController.recentErrors.length > 0
            spacing: 8
            clip: true
            model: mediaflow.workspaceViewController.recentErrors
            delegate: Panel {
                required property var modelData
                width: errorList.width
                height: errorContent.implicitHeight + 22
                ColumnLayout {
                    id: errorContent
                    anchors.fill: parent
                    anchors.margins: 11
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("错误 %1 · %2").arg(modelData.errorId).arg(modelData.timeLabel)
                            color: Theme.danger
                            font.pixelSize: Theme.fontSizeCaption
                            font.family: Theme.monoFontFamily
                        }
                        AppButton {
                            text: qsTr("复制详情")
                            compact: true
                            onClicked: mediaflow.taskController.copyErrorDetails(
                                String(modelData.message) + " [" + String(modelData.errorId) + "]")
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData.message
                        color: Theme.text
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                    }
                }
            }
        }

        EmptyState {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: mediaflow.workspaceViewController.recentErrors.length === 0
            iconName: "tasks"
            title: qsTr("没有错误记录")
            description: qsTr("界面操作发生错误后，可以从这里重新查看和复制详情。")
        }

        RowLayout {
            Layout.fillWidth: true
            AppButton {
                text: qsTr("清空显示记录")
                visible: mediaflow.workspaceViewController.recentErrors.length > 0
                onClicked: mediaflow.workspaceViewController.clearErrorHistory()
            }
            Item { Layout.fillWidth: true }
            AppButton {
                text: qsTr("关闭")
                onClicked: root.close()
            }
        }
    }
}
