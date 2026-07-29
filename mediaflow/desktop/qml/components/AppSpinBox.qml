import QtQuick
import QtQuick.Controls
import ".."

SpinBox {
    id: control
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: Theme.controlHeight
    leftPadding: 32
    rightPadding: 32
    font.pixelSize: Theme.fontSizeBody
    contentItem: TextInput {
        z: 2
        text: control.textFromValue(control.value, control.locale)
        color: control.enabled ? Theme.text : Theme.textDisabled
        selectionColor: Theme.accent
        selectedTextColor: Theme.onAccent
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        readOnly: !control.editable
        selectByMouse: true
        validator: control.validator
        inputMethodHints: Qt.ImhFormattedNumbersOnly
    }
    down.indicator: Rectangle {
        x: 0
        width: 30
        height: control.height
        radius: Theme.radiusSmall
        color: control.down.pressed
            ? Theme.controlPressed
            : control.down.hovered ? Theme.controlHover : Theme.transparent
        AppIcon {
            anchors.centerIn: parent
            width: 14
            height: 14
            iconName: "minus"
            iconColor: control.enabled ? Theme.text : Theme.textDisabled
        }
    }
    up.indicator: Rectangle {
        x: control.width - width
        width: 30
        height: control.height
        radius: Theme.radiusSmall
        color: control.up.pressed
            ? Theme.controlPressed
            : control.up.hovered ? Theme.controlHover : Theme.transparent
        AppIcon {
            anchors.centerIn: parent
            width: 14
            height: 14
            iconName: "add"
            iconColor: control.enabled ? Theme.text : Theme.textDisabled
        }
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: control.enabled ? Theme.field : Theme.controlDisabled
        border.color: control.activeFocus
            ? Theme.focusColor
            : control.hovered ? Theme.borderStrong : Theme.border
        border.width: control.activeFocus ? 2 : 1

        Behavior on border.color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
}
