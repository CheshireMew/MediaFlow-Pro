import QtQuick
import QtQuick.Controls
import ".."

Button {
    id: control
    focusPolicy: Qt.StrongFocus
    property bool primary: false
    property bool danger: false
    implicitHeight: 36
    leftPadding: 14
    rightPadding: 14
    font.pixelSize: Theme.fontSizeBody
    font.weight: primary ? Font.DemiBold : Font.Medium
    contentItem: Text {
        text: control.text
        color: control.enabled ? Theme.text : Theme.textMuted
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: {
            if (!control.enabled)
                return Theme.surface
            if (control.down)
                return control.primary ? "#1472cf" : Theme.surfaceHover
            if (control.checked)
                return Theme.accentSoft
            if (control.hovered)
                return control.primary ? Theme.accentHover : Theme.surfaceHover
            if (control.danger)
                return "#4a252a"
            return control.primary ? Theme.accent : Theme.surfaceRaised
        }
        border.color: control.activeFocus
            ? (control.primary ? Theme.text : Theme.accentHover)
            : control.primary ? "transparent"
            : control.checked ? Theme.accent
            : control.danger ? "#76343a" : Theme.border
        border.width: control.activeFocus ? 2 : 1
    }
}
