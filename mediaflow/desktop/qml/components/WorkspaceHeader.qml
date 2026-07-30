import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    signal exportRequested

    ProjectVersionsDialog {
        id: projectVersionsDialog
        onOpened: {
            if (root.Window.window)
                root.Window.window.projectVersionsVisible = true;
        }
        onClosed: {
            if (root.Window.window)
                root.Window.window.projectVersionsVisible = false;
        }
    }

    implicitWidth: Math.min(statusText.implicitWidth, 220)
        + 34
        + undoButton.implicitWidth
        + redoButton.implicitWidth
        + versionsButton.implicitWidth
        + closeProjectButton.implicitWidth
        + exportButton.implicitWidth
        + controls.spacing * 5
    implicitHeight: 42

    RowLayout {
        id: controls
        anchors.fill: parent
        spacing: 5

        Rectangle {
            Layout.minimumWidth: 112
            Layout.preferredWidth: Math.min(statusText.implicitWidth, 220) + 34
            Layout.preferredHeight: 28
            radius: 14
            color: Theme.surfaceRaised
            border.color: Theme.borderSubtle

            Rectangle {
                id: statusSignal
                anchors.left: parent.left
                anchors.leftMargin: 11
                anchors.verticalCenter: parent.verticalCenter
                width: 7
                height: 7
                radius: 4
                color: workspaceController.readOnly ? Theme.warning : Theme.success
            }

            Text {
                id: statusText
                objectName: "workspaceStatusMessage"
                anchors.left: statusSignal.right
                anchors.leftMargin: 8
                anchors.right: parent.right
                anchors.rightMargin: 11
                anchors.verticalCenter: parent.verticalCenter
                text: workspaceController.readOnly
                    ? qsTr("只读")
                    : workspaceController.statusMessage || qsTr("已保存")
                color: workspaceController.readOnly ? Theme.warning : Theme.textSubtle
                font.pixelSize: Theme.fontSizeCaption
                elide: Text.ElideRight
                ToolTip.visible: statusHover.hovered && implicitWidth > width
                ToolTip.text: text
                HoverHandler {
                    id: statusHover
                }
            }
        }

        AppIconButton {
            id: undoButton
            objectName: "workspaceUndoButton"
            iconName: "undo"
            Accessible.name: qsTr("撤销")
            toolTipText: Accessible.name + " (Ctrl+Z)"
            enabled: workspaceController.actionCapabilities.canEdit
                && timelineController.canUndo
            onClicked: timelineController.undo()
        }

        AppIconButton {
            id: redoButton
            objectName: "workspaceRedoButton"
            iconName: "redo"
            Accessible.name: qsTr("重做")
            toolTipText: Accessible.name + " (Ctrl+Y)"
            enabled: workspaceController.actionCapabilities.canEdit
                && timelineController.canRedo
            onClicked: timelineController.redo()
        }

        AppIconButton {
            id: versionsButton
            objectName: "openProjectVersionsButton"
            iconName: "duplicate"
            Accessible.name: qsTr("版本")
            toolTipText: Accessible.name
            onClicked: projectVersionsDialog.open()
        }

        AppButton {
            id: closeProjectButton
            text: qsTr("关闭项目")
            quiet: true
            implicitHeight: Theme.controlHeightCompact
            enabled: workspaceController.actionCapabilities.canCloseProject
            onClicked: workspaceController.closeProject()
        }

        AppButton {
            id: exportButton
            objectName: "titleExportButton"
            text: qsTr("导出")
            primary: true
            implicitHeight: Theme.controlHeightCompact
            enabled: workspaceController.actionCapabilities.canStartTasks
            onClicked: root.exportRequested()
        }
    }
}
