import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Panel {
    objectName: "exportHistoryPanel"
    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 22
    visible: exportController.exportHistory.length > 0
    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        spacing: 7
        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: qsTr("导出历史与质量检查")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            Text {
                text: qsTr("%1 次").arg(exportController.exportHistory.length)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
        }
        Repeater {
            model: exportController.exportHistory.slice(0, 5)
            Rectangle {
                required property var modelData
                objectName: "exportHistoryItem_" + modelData.recordId
                Layout.fillWidth: true
                implicitHeight: historyRow.implicitHeight + 16
                radius: Theme.radiusSmall
                color: Theme.surfaceRaised
                border.color: modelData.qualityPassed ? Theme.success : Theme.danger
                RowLayout {
                    id: historyRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 8
                    spacing: 7
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            Layout.fillWidth: true
                            text: modelData.outputName
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: Font.DemiBold
                            elide: Text.ElideMiddle
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.qualityPassed
                                ? qsTr("检查通过 · %1 个提醒").arg(modelData.warningCount)
                                : qsTr("发现 %1 个问题 · %2 个提醒")
                                    .arg(modelData.failureCount).arg(modelData.warningCount)
                            color: modelData.qualityPassed ? Theme.success : Theme.danger
                            font.pixelSize: 10
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: Boolean(modelData.encoderFallbackUsed)
                            text: qsTr("硬件编码失败，已从 %1 切换为 %2")
                                .arg(modelData.requestedVideoCodec)
                                .arg(modelData.actualVideoCodec)
                            color: Theme.warning
                            font.pixelSize: 10
                            elide: Text.ElideRight
                        }
                    }
                    AppButton {
                        text: qsTr("成片")
                        onClicked: taskController.openArtifact(modelData.outputPath)
                    }
                    AppButton {
                        objectName: "openExportQualityReportButton"
                        text: qsTr("报告")
                        onClicked: taskController.openArtifact(modelData.reportPath)
                    }
                }
            }
        }
    }
}
