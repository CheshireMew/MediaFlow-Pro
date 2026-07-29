import QtQuick
import QtQuick.Controls
import ".."

Button {
    id: control
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    property bool primary: false
    property bool danger: false
    property bool quiet: false
    property bool compact: false
    readonly property color foregroundColor: {
        if (!control.enabled)
            return Theme.textDisabled
        if (control.primary)
            return Theme.onAccent
        if (control.danger && (control.hovered || control.down))
            return Theme.textStrong
        if (control.danger)
            return Theme.danger
        if (control.checked)
            return Theme.accentHover
        return Theme.text
    }
    readonly property color backgroundColor: {
        if (!control.enabled)
            return control.quiet ? Theme.transparent : Theme.controlDisabled
        if (control.primary)
            return control.down
                ? Theme.accentPressed
                : control.hovered ? Theme.accentHover : Theme.accent
        if (control.danger)
            return control.down
                ? Theme.dangerPressed
                : control.hovered ? Theme.danger : Theme.dangerSoft
        if (control.down)
            return Theme.controlPressed
        if (control.checked)
            return Theme.selectionSoft
        if (control.hovered)
            return Theme.controlHover
        return control.quiet ? Theme.transparent : Theme.control
    }
    implicitHeight: compact ? Theme.controlHeightCompact : Theme.controlHeight
    leftPadding: compact ? 10 : 14
    rightPadding: compact ? 10 : 14
    font.pixelSize: Theme.fontSizeBody
    font.weight: primary ? Font.DemiBold : Font.Medium
    contentItem: Text {
        text: control.text
        color: control.foregroundColor
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: control.backgroundColor
        border.color: control.visualFocus
            ? Theme.focusColor
            : control.primary
            ? Theme.transparent
            : control.checked
            ? Theme.accent
            : control.danger
            ? Theme.danger
            : control.quiet
            ? Theme.transparent
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
