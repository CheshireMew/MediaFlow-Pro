import QtQuick
import QtQuick.Controls
import ".."

CheckBox {
    id: control
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    spacing: 9
    implicitHeight: Math.max(Theme.controlHeightCompact, implicitContentHeight)
    font.pixelSize: Theme.fontSizeBody
    indicator: Rectangle {
        implicitWidth: 20
        implicitHeight: 20
        x: control.leftPadding
        y: Math.round((control.height - height) / 2)
        radius: Theme.radiusSmall - 1
        color: !control.enabled
            ? Theme.controlDisabled
            : control.checkState === Qt.Unchecked
            ? Theme.field
            : Theme.accent
        border.color: control.activeFocus
            ? Theme.focusColor
            : control.hovered ? Theme.borderStrong : Theme.border
        border.width: control.activeFocus ? 2 : 1
        AppIcon {
            anchors.centerIn: parent
            visible: control.checkState !== Qt.Unchecked
            width: 12
            height: 12
            iconName: control.checkState === Qt.PartiallyChecked ? "minus" : "check"
            iconColor: control.enabled ? Theme.onAccent : Theme.textDisabled
            strokeWidth: 2.2
        }

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        color: control.enabled ? Theme.text : Theme.textDisabled
        font: control.font
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
    }
}
