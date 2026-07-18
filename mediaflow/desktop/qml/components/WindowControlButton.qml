import QtQuick
import QtQuick.Controls
import ".."

Button {
    id: control
    property bool closeButton: false
    implicitWidth: 46
    implicitHeight: 42
    focusPolicy: Qt.NoFocus
    font.pixelSize: Theme.fontSizeBodyLarge
    contentItem: Text {
        text: control.text
        color: control.enabled ? Theme.text : Theme.textMuted
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: control.hovered
            ? (control.closeButton ? "#c42b38" : Theme.surfaceHover)
            : "transparent"
    }
}
