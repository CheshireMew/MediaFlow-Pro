import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import ".."

Rectangle {
    id: root
    objectName: "appTitleBar"
    required property var hostWindow
    implicitHeight: 48
    color: Theme.surfaceSunken

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
        anchors.leftMargin: 18
        spacing: 11

        BrandMark {
            Layout.preferredWidth: 28
            Layout.preferredHeight: 28
        }

        RowLayout {
            spacing: 3
            Text {
                text: "MediaFlow"
                color: Theme.textStrong
                font.pixelSize: Theme.fontSizeTitleSmall
                font.weight: Font.DemiBold
                font.letterSpacing: 0.2
            }
            Text {
                text: "/"
                color: Theme.textDisabled
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.Bold
            }
            Text {
                text: "PRO"
                color: Theme.accent
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
                font.letterSpacing: 1.2
            }
        }

        Rectangle {
            Layout.preferredWidth: 1
            Layout.preferredHeight: 16
            color: Theme.divider
        }

        Text {
            objectName: "windowProjectName"
            Layout.minimumWidth: 80
            Layout.preferredWidth: Math.min(implicitWidth, 320)
            Layout.maximumWidth: 320
            text: root.hostWindow.title
            color: workspaceController.hasProject ? Theme.text : Theme.textMuted
            font.pixelSize: Theme.fontSizeBody
            font.weight: workspaceController.hasProject ? Font.Medium : Font.Normal
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
                iconName: "minimize"
                Accessible.name: qsTr("最小化窗口")
                toolTipText: Accessible.name
                onClicked: root.hostWindow.showMinimized()
            }
            WindowControlButton {
                objectName: "maximizeWindowButton"
                iconName: root.hostWindow.visibility === Window.Maximized
                    ? "restore" : "maximize"
                Accessible.name: root.hostWindow.visibility === Window.Maximized
                    ? qsTr("还原窗口") : qsTr("最大化窗口")
                toolTipText: Accessible.name
                onClicked: root.toggleMaximized()
            }
            WindowControlButton {
                objectName: "closeWindowButton"
                closeButton: true
                iconName: "close"
                Accessible.name: qsTr("关闭窗口")
                toolTipText: Accessible.name
                onClicked: root.hostWindow.close()
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.divider
    }

}
