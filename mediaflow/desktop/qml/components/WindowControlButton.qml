import QtQuick
import ".."

AppIconButton {
    id: control

    property bool closeButton: false

    implicitWidth: 40
    implicitHeight: 38
    iconSize: Theme.iconSizeSmall
    flat: true
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

}
