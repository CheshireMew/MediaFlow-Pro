import QtQuick
import QtQuick.Controls
import ".."

TextField {
    id: control
    implicitHeight: 36
    leftPadding: 12
    rightPadding: 12
    selectByMouse: true
    color: Theme.text
    placeholderTextColor: Theme.textMuted
    selectionColor: Theme.accent
    selectedTextColor: Theme.text
    font.pixelSize: Theme.fontSizeBody
    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.window
        border.color: control.activeFocus ? Theme.accent
            : control.hovered ? Theme.borderStrong : Theme.border
        border.width: control.activeFocus ? 2 : 1
    }
}
