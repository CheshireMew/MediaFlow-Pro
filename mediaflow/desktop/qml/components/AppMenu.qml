import QtQuick
import QtQuick.Controls
import ".."

Menu {
    id: control
    margins: 6
    padding: 4
    delegate: AppMenuItem {}
    background: Rectangle {
        implicitWidth: 220
        implicitHeight: 40
        radius: Theme.radiusSmall
        color: Theme.surfaceFloating
        border.color: Theme.borderStrong
        border.width: 1
    }
}
