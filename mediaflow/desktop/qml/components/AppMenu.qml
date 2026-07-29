import QtQuick
import QtQuick.Controls
import ".."

Menu {
    id: control
    margins: 6
    padding: 5
    delegate: AppMenuItem {}
    background: Rectangle {
        implicitWidth: 220
        implicitHeight: Theme.controlHeight
        radius: Theme.radiusSmall
        color: Theme.popup
        border.color: Theme.borderStrong
        border.width: 1
    }
}
