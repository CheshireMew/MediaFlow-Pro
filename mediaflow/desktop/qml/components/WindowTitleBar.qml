import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import ".."

Rectangle {
    id: root
    objectName: "appTitleBar"
    required property var hostWindow
    color: Theme.window
    border.color: Theme.border

    function toggleMaximized() {
        if (hostWindow.visibility === Window.Maximized)
            hostWindow.showNormal()
        else
            hostWindow.showMaximized()
    }

    MouseArea {
        id: dragArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.rightMargin: 138
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        acceptedButtons: Qt.LeftButton
        onPressed: root.hostWindow.startSystemMove()
        onDoubleClicked: root.toggleMaximized()
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        spacing: 9
        Rectangle {
            width: 24
            height: 24
            radius: 7
            color: Theme.accent
            Text {
                anchors.centerIn: parent
                text: "M"
                color: "white"
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.Bold
            }
        }
        Text {
            objectName: "windowProjectName"
            Layout.minimumWidth: 80
            Layout.preferredWidth: Math.min(implicitWidth, 320)
            Layout.maximumWidth: 320
            text: root.hostWindow.title
            color: Theme.text
            font.pixelSize: Theme.fontSizeBody
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
        Item { Layout.fillWidth: true }
        WorkspaceHeader {
            visible: workspaceController.hasProject
            Layout.fillHeight: true
            Layout.minimumWidth: implicitWidth
            Layout.preferredWidth: implicitWidth
            Layout.maximumWidth: implicitWidth
        }
        RowLayout {
            id: windowControls
            spacing: 0
            WindowControlButton {
                objectName: "minimizeWindowButton"
                text: "—"
                Accessible.name: qsTr("最小化窗口")
                onClicked: root.hostWindow.showMinimized()
            }
            WindowControlButton {
                objectName: "maximizeWindowButton"
                text: root.hostWindow.visibility === Window.Maximized ? "❐" : "□"
                Accessible.name: root.hostWindow.visibility === Window.Maximized
                    ? qsTr("还原窗口") : qsTr("最大化窗口")
                onClicked: root.toggleMaximized()
            }
            WindowControlButton {
                objectName: "closeWindowButton"
                closeButton: true
                text: "×"
                Accessible.name: qsTr("关闭窗口")
                onClicked: root.hostWindow.close()
            }
        }
    }
}
