import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    ProjectVersionsDialog {
        id: projectVersionsDialog
    }

    implicitWidth: Math.min(statusText.implicitWidth, 320)
        + undoButton.implicitWidth
        + redoButton.implicitWidth
        + versionsButton.implicitWidth
        + closeProjectButton.implicitWidth
        + controls.spacing * 4
    implicitHeight: 38

    RowLayout {
        id: controls
        anchors.fill: parent
        spacing: 6
        Text {
            id: statusText
            objectName: "workspaceStatusMessage"
            Layout.maximumWidth: 320
            text: workspaceController.readOnly
                ? qsTr("只读")
                : workspaceController.statusMessage || qsTr("已保存")
            color: workspaceController.readOnly ? Theme.warning : Theme.success
            font.pixelSize: Theme.fontSizeCaption
            elide: Text.ElideRight
            ToolTip.visible: statusHover.hovered && implicitWidth > width
            ToolTip.text: text
            HoverHandler {
                id: statusHover
            }
        }
        AppButton {
            id: undoButton
            text: "↶"
            Accessible.name: qsTr("撤销")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name + " (Ctrl+Z)"
            enabled: timelineController.canUndo
            onClicked: timelineController.undo()
        }
        AppButton {
            id: redoButton
            text: "↷"
            Accessible.name: qsTr("重做")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name + " (Ctrl+Y)"
            enabled: timelineController.canRedo
            onClicked: timelineController.redo()
        }
        AppButton {
            id: versionsButton
            objectName: "openProjectVersionsButton"
            text: qsTr("版本")
            onClicked: projectVersionsDialog.open()
        }
        AppButton {
            id: closeProjectButton
            text: qsTr("关闭项目")
            onClicked: workspaceController.closeProject()
        }
    }
}
