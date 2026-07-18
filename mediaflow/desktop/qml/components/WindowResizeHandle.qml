import QtQuick
import QtQuick.Window

MouseArea {
    required property var hostWindow
    required property int edges
    acceptedButtons: Qt.LeftButton
    hoverEnabled: true
    visible: hostWindow.visibility === Window.Windowed
    onPressed: hostWindow.startSystemResize(edges)
}
