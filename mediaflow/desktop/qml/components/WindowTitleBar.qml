import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import ".."

Rectangle {
    id: root
    objectName: "appTitleBar"
    required property var hostWindow
    property var workspaceItem: null
    signal exportRequested
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

        Text {
            objectName: "applicationProductName"
            text: Qt.application.name
            color: Theme.textStrong
            font.pixelSize: Theme.fontSizeTitleSmall
            font.weight: Font.DemiBold
            font.letterSpacing: 0.2
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: mediaflow.workspaceViewController.hasProject ? 80 : 0

            Text {
                id: projectName
                objectName: "windowProjectName"
                visible: mediaflow.workspaceViewController.hasProject
                anchors.centerIn: parent
                width: Math.min(320, parent.width)
                text: root.hostWindow.title
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.Medium
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }
        }

        WorkspaceHeader {
            visible: mediaflow.workspaceViewController.hasProject
            workspaceItem: root.workspaceItem
            Layout.fillHeight: true
            Layout.minimumWidth: implicitWidth
            Layout.preferredWidth: implicitWidth
            Layout.maximumWidth: implicitWidth
            onExportRequested: root.exportRequested()
        }
        RowLayout {
            id: windowControls
            objectName: "windowControls"
            Layout.leftMargin: 2
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
                    ? "window-restore" : "window-maximize"
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
