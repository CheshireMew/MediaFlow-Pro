import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: root
    objectName: "taskCenterPanel"

    Dialog {
        id: cancelAllDialog
        objectName: "cancelAllTasksDialog"
        anchors.centerIn: parent
        width: Math.min(380, Math.max(280, root.width - 24))
        modal: true
        title: qsTr("取消全部任务？")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: taskController.cancelAllTasks()
        contentItem: Text {
            width: cancelAllDialog.availableWidth
            text: qsTr("正在运行和等待中的任务都会取消；已经生成的结果不会删除。")
            color: Theme.text
            wrapMode: Text.WordWrap
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("任务中心")
                color: Theme.text
                font.pixelSize: Theme.fontSizeSection
                font.weight: Font.DemiBold
            }
            Text {
                objectName: "taskActivitySummary"
                text: taskController.pausedTaskCount > 0
                    ? qsTr("进行中 %1 · 已暂停 %2")
                        .arg(taskController.inFlightTaskCount)
                        .arg(taskController.pausedTaskCount)
                    : qsTr("进行中 %1").arg(taskController.inFlightTaskCount)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            Item { Layout.fillWidth: true }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            AppButton {
                objectName: "pauseActiveTasksButton"
                Layout.fillWidth: true
                enabled: taskController.inFlightTaskCount > 0
                text: qsTr("暂停进行中")
                onClicked: taskController.pauseAllTasks()
            }
            AppButton {
                Layout.fillWidth: true
                enabled: taskController.activeTaskCount > 0
                danger: true
                text: qsTr("全部取消")
                onClicked: cancelAllDialog.open()
            }
            AppButton {
                Layout.fillWidth: true
                enabled: taskController.terminalTaskCount > 0
                text: qsTr("清理已结束")
                onClicked: taskController.clearTaskHistory()
            }
        }

        ListView {
            id: taskList
            objectName: "taskCenterList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8
            clip: true
            model: taskController.tasksModel
            delegate: Panel {
                required property string displayName
                required property string commandType
                required property string kind
                required property string status
                required property string statusLabel
                required property real progress
                required property string messageCode
                required property string messageLabel
                required property int queuePosition
                required property string error
                required property string taskId
                required property var artifacts
                required property var executionTrace
                property bool traceExpanded: false
                readonly property bool compactCompleted: status === "completed"
                    && error.length === 0 && !traceExpanded
                readonly property bool userOpenableArtifact: commandType !== "generate_proxy"
                    && commandType !== "generate_waveform"
                width: taskList.width
                height: (compactCompleted ? 94 : error.length > 0 ? 166 : 142)
                    + (traceExpanded ? 30 + executionTrace.length * 25 : 0)

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 7
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: displayName
                            color: Theme.text
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            text: statusLabel
                            color: status === "completed" ? Theme.success
                                : status === "failed" ? Theme.danger : Theme.accentHover
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    ProgressBar {
                        Layout.fillWidth: true
                        visible: !compactCompleted
                        from: 0
                        to: 100
                        value: progress
                    }
                    Text {
                        visible: !compactCompleted
                        text: queuePosition > 0
                            ? qsTr("等待执行 · 队列第 %1 位").arg(queuePosition)
                            : messageLabel
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: error.length > 0
                        text: error
                        color: Theme.danger
                        font.pixelSize: Theme.fontSizeCaption
                        elide: Text.ElideRight
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        AppButton {
                            visible: status === "running"
                            text: qsTr("暂停")
                            onClicked: taskController.pauseTask(taskId)
                        }
                        AppButton {
                            visible: status === "paused"
                            text: qsTr("继续")
                            onClicked: taskController.resumeTask(taskId)
                        }
                        AppButton {
                            visible: status === "pending" || status === "running" || status === "paused"
                            text: qsTr("取消")
                            onClicked: taskController.cancelTask(taskId)
                        }
                        AppButton {
                            objectName: "taskOpenResultButton"
                            property string taskCommandType: commandType
                            visible: userOpenableArtifact && status === "completed"
                                && artifacts && artifacts.length > 0
                            text: qsTr("打开结果")
                            onClicked: taskController.openArtifact(String(artifacts[0]))
                        }
                        AppButton {
                            visible: status === "failed" || status === "cancelled"
                            text: qsTr("重试")
                            onClicked: taskController.retryTask(taskId)
                        }
                        AppButton {
                            visible: status === "completed" || status === "failed" || status === "cancelled"
                            text: qsTr("从列表移除")
                            onClicked: taskController.removeTask(taskId)
                        }
                        AppButton {
                            visible: executionTrace && executionTrace.length > 0
                            text: traceExpanded ? qsTr("收起执行记录") : qsTr("执行记录")
                            onClicked: traceExpanded = !traceExpanded
                        }
                        Item { Layout.fillWidth: true }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: traceExpanded
                        spacing: 4
                        Repeater {
                            model: executionTrace
                            RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Text {
                                    text: modelData.status === "success" ? "✓"
                                        : modelData.status === "failed" ? "!" : "×"
                                    color: modelData.status === "success" ? Theme.success
                                        : modelData.status === "failed" ? Theme.danger : Theme.warning
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.step
                                    color: Theme.textMuted
                                    font.pixelSize: Theme.fontSizeCaption
                                    elide: Text.ElideRight
                                    ToolTip.visible: traceMouse.containsMouse && modelData.error.length > 0
                                    ToolTip.text: modelData.error
                                    MouseArea {
                                        id: traceMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                    }
                                }
                                Text {
                                    text: Number(modelData.duration).toFixed(2) + "s"
                                    color: Theme.textMuted
                                    font.family: Theme.monoFontFamily
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                            }
                        }
                    }
                }
            }
            EmptyState {
                anchors.fill: parent
                visible: taskList.count === 0
                iconText: "✓"
                title: qsTr("没有后台任务")
                description: qsTr("下载、预览准备、字幕、翻译和导出进度会显示在这里。")
            }
        }
    }
}
