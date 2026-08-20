import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: root
    objectName: "taskCenterPanel"
    readonly property bool canManageTasks:
        mediaflow.workspaceViewController.actionCapabilities.canManageTasks
    readonly property bool modalOpen: cancelAllDialog.opened || errorHistoryDialog.opened

    ErrorHistoryDialog {
        id: errorHistoryDialog
        anchors.centerIn: parent
    }

    AppDialog {
        id: cancelAllDialog
        objectName: "cancelAllTasksDialog"
        anchors.centerIn: parent
        width: Math.min(380, Math.max(280, root.width - 24))
        modal: true
        title: qsTr("取消全部任务？")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: if (root.canManageTasks) mediaflow.taskController.cancelAllTasks()
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
                objectName: "taskActivitySummary"
                Layout.fillWidth: true
                text: mediaflow.taskController.pausedTaskCount > 0
                    ? qsTr("进行中 %1 · 已暂停 %2")
                        .arg(mediaflow.taskController.inFlightTaskCount)
                        .arg(mediaflow.taskController.pausedTaskCount)
                    : qsTr("进行中 %1").arg(mediaflow.taskController.inFlightTaskCount)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppButton {
                objectName: "openErrorHistoryButton"
                visible: mediaflow.workspaceViewController.recentErrors.length > 0
                text: qsTr("错误记录 %1").arg(
                    mediaflow.workspaceViewController.recentErrors.length)
                compact: true
                danger: true
                onClicked: errorHistoryDialog.open()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            AppButton {
                objectName: "pauseActiveTasksButton"
                Layout.fillWidth: true
                enabled: root.canManageTasks
                    && mediaflow.taskController.inFlightTaskCount > 0
                text: qsTr("暂停进行中")
                onClicked: mediaflow.taskController.pauseAllTasks()
            }
            AppButton {
                Layout.fillWidth: true
                enabled: root.canManageTasks
                    && mediaflow.taskController.activeTaskCount > 0
                danger: true
                text: qsTr("全部取消")
                onClicked: cancelAllDialog.open()
            }
            AppButton {
                Layout.fillWidth: true
                enabled: root.canManageTasks
                    && mediaflow.taskController.terminalTaskCount > 0
                text: qsTr("清理已结束")
                onClicked: mediaflow.taskController.clearTaskHistory()
            }
        }

        ListView {
            id: taskList
            objectName: "taskCenterList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8
            clip: true
            model: mediaflow.taskController.tasksModel
            delegate: Panel {
                required property string displayName
                required property string configurationLabel
                required property bool encoderFallbackUsed
                required property string commandType
                required property string kind
                required property string status
                required property string statusLabel
                required property string progressMode
                required property real progressValue
                required property bool hasOverallProgress
                required property real overallProgressValue
                required property int progressItemIndex
                required property int progressItemTotal
                required property string progressItemLabel
                required property string messageCode
                required property string messageLabel
                required property int queuePosition
                required property string error
                required property string taskId
                required property var artifacts
                required property var executionTrace
                property bool traceExpanded: false
                property bool errorExpanded: false
                readonly property bool compactCompleted: status === "completed"
                    && error.length === 0 && !encoderFallbackUsed && !traceExpanded
                readonly property bool userOpenableArtifact: commandType !== "generate_proxy"
                    && commandType !== "generate_waveform"
                width: taskList.width
                height: taskContent.implicitHeight + 24

                ColumnLayout {
                    id: taskContent
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
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
                    AppProgressBar {
                        Layout.fillWidth: true
                        visible: !compactCompleted
                        from: 0
                        to: 100
                        indeterminate: status === "running" && !hasOverallProgress
                            && progressMode !== "determinate"
                        value: hasOverallProgress ? overallProgressValue : progressValue
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: !compactCompleted && configurationLabel.length > 0
                        text: configurationLabel
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                        elide: Text.ElideRight
                    }
                    Text {
                        visible: !compactCompleted
                        text: queuePosition > 0
                            ? qsTr("等待执行 · 队列第 %1 位").arg(queuePosition)
                            : (progressItemTotal > 0
                                ? qsTr("第 %1/%2 段 · ").arg(
                                    progressItemIndex).arg(progressItemTotal)
                                    + (progressItemLabel.length > 0
                                        ? progressItemLabel + " · " : "")
                                : "")
                                + messageLabel
                                + (hasOverallProgress
                                    ? qsTr(" · 识别 %1%").arg(
                                        Math.round(overallProgressValue))
                                    : "")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: error.length > 0
                        text: error
                        color: Theme.danger
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                        maximumLineCount: errorExpanded ? 1000 : 2
                        elide: errorExpanded ? Text.ElideNone : Text.ElideRight
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        AppButton {
                            visible: status === "running"
                            enabled: root.canManageTasks
                            text: qsTr("暂停")
                            onClicked: mediaflow.taskController.pauseTask(taskId)
                        }
                        AppButton {
                            visible: status === "paused"
                            enabled: root.canManageTasks
                            text: qsTr("继续")
                            onClicked: mediaflow.taskController.resumeTask(taskId)
                        }
                        AppButton {
                            visible: status === "pending" || status === "running" || status === "paused"
                            enabled: root.canManageTasks
                            text: qsTr("取消")
                            onClicked: mediaflow.taskController.cancelTask(taskId)
                        }
                        AppButton {
                            objectName: "taskOpenResultButton"
                            property string taskCommandType: commandType
                            visible: userOpenableArtifact && status === "completed"
                                && artifacts && artifacts.length > 0
                            text: qsTr("打开结果")
                            onClicked: mediaflow.taskController.openArtifact(String(artifacts[0]))
                        }
                        AppButton {
                            visible: status === "failed" || status === "cancelled"
                            enabled: root.canManageTasks
                            text: qsTr("重试")
                            onClicked: mediaflow.taskController.retryTask(taskId)
                        }
                        AppButton {
                            visible: status === "completed" || status === "failed" || status === "cancelled"
                            enabled: root.canManageTasks
                            text: qsTr("从列表移除")
                            onClicked: mediaflow.taskController.removeTask(taskId)
                        }
                        AppButton {
                            visible: executionTrace && executionTrace.length > 0
                            text: traceExpanded ? qsTr("收起执行记录") : qsTr("执行记录")
                            onClicked: traceExpanded = !traceExpanded
                        }
                        AppButton {
                            visible: error.length > 0
                            text: errorExpanded ? qsTr("收起错误") : qsTr("错误详情")
                            onClicked: errorExpanded = !errorExpanded
                        }
                        AppButton {
                            visible: error.length > 0
                            text: qsTr("复制错误")
                            onClicked: mediaflow.taskController.copyErrorDetails(error)
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
                                AppIcon {
                                    Layout.preferredWidth: 14
                                    Layout.preferredHeight: 14
                                    iconName: modelData.status === "success"
                                        ? "check"
                                        : modelData.status === "failed" ? "warning" : "close"
                                    iconColor: modelData.status === "success"
                                        ? Theme.success
                                        : modelData.status === "failed" ? Theme.danger : Theme.warning
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
                iconName: "check"
                title: qsTr("没有后台任务")
                description: qsTr("下载、预览准备、字幕、翻译和导出进度会显示在这里。")
            }
        }
    }
}
