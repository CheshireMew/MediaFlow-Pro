import QtQuick
import QtQuick.Controls
import "."
import "components"

AbstractButton {
    id: root

    required property string iconKind
    required property string toolTipText
    readonly property color iconColor: checked || hovered ? Theme.text : Theme.textMuted

    implicitWidth: 32
    implicitHeight: 32
    checkable: false
    padding: 0
    Accessible.name: toolTipText

    background: Rectangle {
        radius: 5
        color: root.checked ? Theme.accentSoft
            : root.hovered ? Theme.surfaceHover : Theme.surfaceRaised
        border.color: root.checked ? Theme.accent : Theme.border
    }

    contentItem: AppIcon {
        width: 20
        height: 20
        anchors.centerIn: parent
        iconName: root.iconKind
        iconColor: root.iconColor
    }

    ToolTip.visible: hovered
    ToolTip.text: toolTipText
    ToolTip.delay: 350
}
