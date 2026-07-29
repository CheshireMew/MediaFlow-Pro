import QtQuick
import QtQuick.Controls
import ".."

ScrollIndicator {
    id: control

    padding: 2
    implicitWidth: orientation === Qt.Vertical ? 8 : 32
    implicitHeight: orientation === Qt.Horizontal ? 8 : 32

    contentItem: Rectangle {
        implicitWidth: 4
        implicitHeight: 4
        radius: Math.min(width, height) / 2
        color: control.enabled ? Theme.borderStrong : Theme.textDisabled
        opacity: control.active ? 0.9 : 0

        Behavior on opacity {
            NumberAnimation { duration: Theme.duration }
        }
    }
}
