import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    implicitWidth: controls.implicitWidth
    implicitHeight: 38

    RowLayout {
        id: controls
        anchors.fill: parent
        spacing: 6
        Text {
            visible: workspaceController.readOnly
            text: qsTr("只读")
            color: Theme.warning
            font.pixelSize: Theme.fontSizeCaption
        }
        Text {
            visible: !workspaceController.readOnly
            text: qsTr("已保存")
            color: Theme.success
            font.pixelSize: Theme.fontSizeCaption
        }
        AppButton {
            text: "↶"
            Accessible.name: qsTr("撤销")
            enabled: timelineController.canUndo
            onClicked: timelineController.undo()
        }
        AppButton {
            text: "↷"
            Accessible.name: qsTr("重做")
            enabled: timelineController.canRedo
            onClicked: timelineController.redo()
        }
        AppButton { text: qsTr("任务"); onClicked: taskController.toggleTaskDrawer() }
        AppButton { text: qsTr("关闭项目"); onClicked: workspaceController.closeProject() }
    }
}
