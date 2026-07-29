import QtQuick.Controls

ScrollView {
    id: control

    ScrollBar.vertical: AppScrollBar {
        parent: control
        anchors.top: control.top
        anchors.right: control.right
        anchors.bottom: control.bottom
        policy: ScrollBar.AsNeeded
    }
    ScrollBar.horizontal: AppScrollBar {
        parent: control
        anchors.left: control.left
        anchors.right: control.right
        anchors.bottom: control.bottom
        policy: ScrollBar.AlwaysOff
    }
}
