import QtQuick
import QtQuick.Controls
import ".."

Popup {
    id: control

    property bool danger: false

    padding: 8
    modal: false
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Item {
        implicitWidth: 220
        implicitHeight: Theme.controlHeight

        Rectangle {
            x: 1
            y: 3
            width: parent.width
            height: parent.height
            radius: Theme.radiusSmall + 1
            color: Theme.shadow
        }

        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusSmall
            color: control.danger ? Theme.dangerSoft : Theme.popup
            border.color: control.danger ? Theme.danger : Theme.borderStrong
            border.width: 1
        }
    }
}
