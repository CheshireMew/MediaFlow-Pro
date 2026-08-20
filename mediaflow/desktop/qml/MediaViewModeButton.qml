import QtQuick
import QtQuick.Controls
import "."
import "components"

AbstractButton {
    id: root

    required property string iconKind
    required property string toolTipText
    readonly property color iconColor: checked || hovered ? Theme.text : Theme.textMuted
    readonly property bool usesLucideIcon: viewModeGlyph.usesLucide

    implicitWidth: Theme.iconButtonSizeCompact
    implicitHeight: Theme.iconButtonSizeCompact
    checkable: false
    padding: 0
    Accessible.name: toolTipText

    background: Rectangle {
        radius: 5
        color: root.checked ? Theme.accentSoft
            : root.hovered ? Theme.surfaceHover : Theme.surfaceRaised
        border.color: root.checked ? Theme.accent : Theme.border
    }

    contentItem: Item {
        implicitWidth: Theme.iconSizeSmall
        implicitHeight: Theme.iconSizeSmall

        AppIcon {
            id: viewModeGlyph
            width: Theme.iconSizeSmall
            height: Theme.iconSizeSmall
            anchors.centerIn: parent
            iconName: root.iconKind
            iconColor: root.iconColor
        }
    }

    ToolTip.visible: hovered
    ToolTip.text: toolTipText
    ToolTip.delay: 350
}
