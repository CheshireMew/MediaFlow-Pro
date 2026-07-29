import QtQuick
import QtQuick.Controls
import ".."

MenuItem {
    id: control
    readonly property int shortcutSeparatorIndex: text.indexOf("\t")
    readonly property string labelText: shortcutSeparatorIndex < 0
        ? text : text.slice(0, shortcutSeparatorIndex)
    readonly property string shortcutText: shortcutSeparatorIndex < 0
        ? "" : text.slice(shortcutSeparatorIndex + 1)
    implicitWidth: Math.max(220, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Theme.controlHeight
    leftPadding: control.checkable ? 38 : 12
    rightPadding: control.subMenu ? 34 : 12
    font.pixelSize: Theme.fontSizeBody

    contentItem: Item {
        readonly property color textColor: control.enabled ? Theme.text : Theme.textDisabled
        implicitWidth: label.implicitWidth
            + (shortcut.visible ? shortcut.implicitWidth + 32 : 0)
        implicitHeight: Math.max(label.implicitHeight, shortcut.implicitHeight)

        Text {
            id: label
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: control.labelText
            color: parent.textColor
            font: control.font
        }

        Text {
            id: shortcut
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            visible: control.shortcutText.length > 0
            text: control.shortcutText
            color: control.enabled ? Theme.textMuted : Theme.textDisabled
            font.family: Theme.monoFontFamily
            font.pixelSize: Theme.fontSizeCaption
        }
    }

    indicator: AppIcon {
        x: 12
        y: Math.round((control.height - height) / 2)
        visible: control.checkable
        width: 14
        height: 14
        iconName: "check"
        iconColor: control.enabled ? Theme.accent : Theme.textDisabled
        opacity: control.checked ? 1 : 0
    }

    arrow: AppIcon {
        x: control.width - width - 12
        y: Math.round((control.height - height) / 2)
        visible: control.subMenu
        width: 14
        height: 14
        iconName: "chevron-right"
        iconColor: control.enabled ? Theme.textMuted : Theme.textDisabled
    }

    background: Rectangle {
        x: 2
        y: 2
        width: control.width - 4
        height: control.height - 4
        radius: Theme.radiusSmall
        color: {
            if (control.down)
                return Theme.controlPressed
            if (control.highlighted)
                return Theme.controlHover
            return Theme.transparent
        }
        border.color: control.visualFocus ? Theme.focusColor : Theme.transparent
        border.width: control.visualFocus ? 1 : 0

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
}
