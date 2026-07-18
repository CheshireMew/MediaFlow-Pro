import QtQuick
import QtQuick.Controls
import ".."

CheckBox {
    id: control
    spacing: 9
    font.pixelSize: Theme.fontSizeBody
    indicator: Rectangle {
        implicitWidth: 20
        implicitHeight: 20
        x: control.leftPadding
        y: Math.round((control.height - height) / 2)
        radius: 5
        color: control.checked ? Theme.accent : Theme.window
        border.color: control.activeFocus ? Theme.accentHover
            : control.hovered ? Theme.borderStrong : Theme.border
        border.width: control.activeFocus ? 2 : 1
        Text {
            anchors.centerIn: parent
            visible: control.checked
            text: "✓"
            color: "white"
            font.weight: Font.Bold
            font.pixelSize: Theme.fontSizeBodySmall
        }
    }
    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        color: control.enabled ? Theme.text : Theme.textMuted
        font: control.font
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
    }
}
