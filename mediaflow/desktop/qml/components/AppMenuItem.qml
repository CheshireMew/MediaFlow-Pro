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
    implicitHeight: 40
    leftPadding: control.checkable ? 38 : 12
    rightPadding: control.subMenu ? 34 : 12
    font.pixelSize: Theme.fontSizeBodyLarge

    contentItem: Item {
        readonly property color textColor: control.enabled ? Theme.text : Theme.textMuted
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
            color: parent.textColor
            font: control.font
        }
    }

    indicator: Text {
        x: 12
        y: Math.round((control.height - height) / 2)
        visible: control.checkable
        text: control.checked ? "✓" : ""
        color: control.enabled ? Theme.text : Theme.textMuted
        font: control.font
    }

    arrow: Text {
        x: control.width - width - 12
        y: Math.round((control.height - height) / 2)
        visible: control.subMenu
        text: "›"
        color: control.enabled ? Theme.textMuted : Theme.borderStrong
        font: control.font
    }

    background: Rectangle {
        x: 2
        y: 2
        width: control.width - 4
        height: control.height - 4
        radius: Theme.radiusSmall
        color: {
            if (control.down)
                return Theme.accentSoft
            if (control.highlighted)
                return Theme.surfaceHover
            return "transparent"
        }
        border.color: control.visualFocus ? Theme.accent : "transparent"
        border.width: control.visualFocus ? 1 : 0
    }
}
