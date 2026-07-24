import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Panel {
    id: root
    property var taskData: ({})
    property string fallbackTitle: qsTr("后台任务")
    property bool showArtifact: true
    readonly property bool taskActive: taskData.status === "pending"
        || taskData.status === "running" || taskData.status === "paused"
    readonly property bool compactCompleted: taskData.status === "completed"
        && !taskData.error

    visible: Boolean(taskData.taskId)
    implicitHeight: content.implicitHeight + 20

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 10
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "contextTaskTitle"
                Layout.fillWidth: true
                text: root.taskData.displayName || root.fallbackTitle
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            Text {
                objectName: "contextTaskStatus"
                text: root.compactCompleted ? root.taskData.statusLabel
                    : (root.taskData.statusLabel || "")
                        + (Boolean(root.taskData.hasOverallProgress)
                            ? " · " + qsTr("识别 %1%").arg(
                                Math.round(Number(root.taskData.overallProgressValue || 0)))
                            : root.taskData.progressMode === "determinate"
                            ? " · " + Math.round(Number(root.taskData.progressValue || 0)) + "%"
                            : "")
                color: Theme.accentHover
                font.pixelSize: Theme.fontSizeCaption
            }
        }
        ProgressBar {
            Layout.fillWidth: true
            visible: !root.compactCompleted
            from: 0
            to: 100
            indeterminate: root.taskActive && !Boolean(root.taskData.hasOverallProgress)
                && root.taskData.progressMode !== "determinate"
            value: Boolean(root.taskData.hasOverallProgress)
                ? Number(root.taskData.overallProgressValue || 0)
                : Number(root.taskData.progressValue || 0)
        }
        Text {
            Layout.fillWidth: true
            visible: !root.compactCompleted
                && Boolean(root.taskData.configurationLabel)
            text: root.taskData.configurationLabel || ""
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        Text {
            Layout.fillWidth: true
            visible: !root.compactCompleted
            text: root.taskData.error || (
                (root.taskData.progressItemTotal > 0
                    ? qsTr("第 %1/%2 段 · ").arg(
                        root.taskData.progressItemIndex).arg(
                        root.taskData.progressItemTotal)
                        + (root.taskData.progressItemLabel
                            ? root.taskData.progressItemLabel + " · " : "")
                    : "")
                + (root.taskData.messageLabel || qsTr("等待执行"))
                + (Boolean(root.taskData.hasOverallProgress)
                    && root.taskData.progressMode === "determinate"
                    ? qsTr(" · 当前步骤 %1%").arg(
                        Math.round(Number(root.taskData.progressValue || 0)))
                    : "")
            )
            color: root.taskData.error ? Theme.danger : Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        ProgressBar {
            Layout.fillWidth: true
            visible: !root.compactCompleted
                && Boolean(root.taskData.hasOverallProgress)
                && root.taskData.progressMode === "determinate"
            from: 0
            to: 100
            value: Number(root.taskData.progressValue || 0)
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton {
                Layout.fillWidth: true
                visible: root.taskActive
                danger: true
                text: qsTr("取消")
                onClicked: taskController.cancelTask(root.taskData.taskId)
            }
            AppButton {
                Layout.fillWidth: true
                visible: root.taskData.status === "failed"
                    || root.taskData.status === "cancelled"
                text: qsTr("重试")
                onClicked: taskController.retryTask(root.taskData.taskId)
            }
            AppButton {
                Layout.fillWidth: true
                visible: root.showArtifact && root.taskData.status === "completed"
                    && root.taskData.artifacts && root.taskData.artifacts.length > 0
                text: qsTr("打开结果")
                onClicked: taskController.openArtifact(root.taskData.artifacts[0])
            }
            AppButton {
                objectName: "contextTaskDetailsButton"
                text: qsTr("任务详情")
                onClicked: taskController.openTaskCenter()
            }
        }
    }
}
