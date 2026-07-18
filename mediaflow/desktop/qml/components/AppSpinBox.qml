import QtQuick
import QtQuick.Controls
import ".."

SpinBox {
    id: control
    implicitHeight: 36
    leftPadding: 32
    rightPadding: 32
    font.pixelSize: Theme.fontSizeBody
    contentItem: TextInput {
        z: 2
        text: control.textFromValue(control.value, control.locale)
        color: Theme.text
        selectionColor: Theme.accent
        selectedTextColor: Theme.text
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        readOnly: !control.editable
        validator: control.validator
        inputMethodHints: Qt.ImhFormattedNumbersOnly
    }
    down.indicator: Rectangle {
        x: 0
        width: 30
        height: control.height
        radius: Theme.radiusSmall
        color: control.down.pressed ? Theme.surfaceHover : "transparent"
        Text {
            anchors.centerIn: parent
            text: "−"
            color: control.down.enabled ? Theme.text : Theme.textMuted
            font.pixelSize: Theme.fontSizeBodyLarge
        }
    }
    up.indicator: Rectangle {
        x: control.width - width
        width: 30
        height: control.height
        radius: Theme.radiusSmall
        color: control.up.pressed ? Theme.surfaceHover : "transparent"
        Text {
            anchors.centerIn: parent
            text: "+"
            color: control.up.enabled ? Theme.text : Theme.textMuted
            font.pixelSize: Theme.fontSizeBodyLarge
        }
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.window
        border.color: control.activeFocus ? Theme.accent : Theme.border
        border.width: control.activeFocus ? 2 : 1
    }
}
