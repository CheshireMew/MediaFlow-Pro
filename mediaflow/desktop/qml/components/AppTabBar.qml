import QtQuick
import QtQuick.Controls
import ".."

TabBar {
    id: control
    implicitHeight: Theme.controlHeightLarge
    spacing: 4
    leftPadding: 0
    rightPadding: 0
    topPadding: 0
    bottomPadding: 1

    background: Rectangle {
        color: Theme.transparent
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Theme.divider
        }
    }
}
