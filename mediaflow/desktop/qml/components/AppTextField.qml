import QtQuick
import QtQuick.Controls
import ".."

TextField {
    id: control
    property bool error: false
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: Theme.controlHeight
    leftPadding: 12
    rightPadding: 12
    topPadding: 7
    bottomPadding: 7
    selectByMouse: true
    color: control.enabled ? Theme.text : Theme.textDisabled
    placeholderTextColor: control.enabled ? Theme.textMuted : Theme.textDisabled
    selectionColor: Theme.accent
    selectedTextColor: Theme.onAccent
    font.pixelSize: Theme.fontSizeBody
    background: Rectangle {
        radius: Theme.radiusSmall
        color: control.enabled
            ? control.readOnly ? Theme.fieldReadOnly : Theme.field
            : Theme.controlDisabled
        border.color: control.error
            ? Theme.danger
            : control.activeFocus
            ? Theme.focusColor
            : control.hovered
            ? Theme.borderStrong
            : Theme.borderSubtle
        border.width: control.activeFocus ? 2 : 1

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
}
