import QtQuick
import ".."

AppIconButton {
    id: control

    property bool closeButton: false

    implicitWidth: 46
    implicitHeight: 46
    iconSize: Theme.iconSizeToolbar
    flat: true
    danger: closeButton
    focusPolicy: Qt.NoFocus

    background: Rectangle {
        color: {
            if (!control.hovered && !control.down)
                return Theme.transparent;
            if (control.closeButton)
                return control.down ? Theme.dangerPressed : Theme.danger;
            return control.down ? Theme.surfacePressed : Theme.surfaceHover;
        }
    }

    contentItem: AppIcon {
        width: control.iconSize
        height: control.iconSize
        anchors.centerIn: parent
        iconName: control.iconName
        iconColor: control.closeButton && (control.hovered || control.down)
            ? Theme.textStrong
            : control.enabled ? Theme.textSubtle : Theme.textDisabled
    }
}
