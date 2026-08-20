import QtQuick
import QtQuick.Controls
import ".."

AbstractButton {
    id: control

    property string iconName: ""
    property bool compact: false
    property int iconSize: compact ? Theme.iconSizeSmall : Theme.iconSizeToolbar
    property bool primary: false
    property bool danger: false
    property bool flat: true
    property string toolTipText: ""
    readonly property bool usesLucideIcon: buttonGlyph.usesLucide
    readonly property color foregroundColor: {
        if (!control.enabled)
            return Theme.textDisabled;
        if (control.primary)
            return Theme.onAccent;
        if (control.danger)
            return control.hovered || control.down ? Theme.dangerHover : Theme.text;
        if (control.checked)
            return Theme.accentHover;
        return Theme.text;
    }
    readonly property color backgroundColor: {
        if (!control.enabled)
            return control.flat ? Theme.transparent : Theme.controlDisabled;
        if (control.primary)
            return control.down
                ? Theme.accentPressed
                : control.hovered ? Theme.accentHover : Theme.accent;
        if (control.danger)
            return control.down
                ? Theme.dangerPressed
                : control.hovered ? Theme.dangerSoft : Theme.transparent;
        if (control.down)
            return Theme.controlPressed;
        if (control.checked)
            return Theme.selectionSoft;
        if (control.hovered)
            return Theme.controlHover;
        return control.flat ? Theme.transparent : Theme.control;
    }

    implicitWidth: compact ? Theme.iconButtonSizeCompact : Theme.iconButtonSize
    implicitHeight: compact ? Theme.iconButtonSizeCompact : Theme.iconButtonSize
    padding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.Button

    contentItem: Item {
        implicitWidth: control.iconSize
        implicitHeight: control.iconSize

        AppIcon {
            id: buttonGlyph
            width: control.iconSize
            height: control.iconSize
            anchors.centerIn: parent
            iconName: control.iconName
            iconColor: control.foregroundColor
        }
    }

    background: Rectangle {
        radius: Theme.radiusSmall
        color: control.backgroundColor
        border.width: control.activeFocus || (!control.flat && control.hovered) ? 1 : 0
        border.color: control.danger
            ? Theme.danger
            : control.activeFocus ? Theme.focusColor : Theme.borderStrong

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }

    ToolTip.visible: control.hovered && control.toolTipText.length > 0
    ToolTip.text: control.toolTipText
    ToolTip.delay: 350
}
