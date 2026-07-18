import QtQuick
import QtQuick.Controls
import ".."

Control {
    id: control
    property string text: ""
    property string placeholderText: ""
    implicitHeight: 36
    leftPadding: 12
    rightPadding: 12
    hoverEnabled: true
    contentItem: Text {
        text: control.text.length > 0 ? control.text : control.placeholderText
        color: control.text.length > 0 ? Theme.text : Theme.textMuted
        font.pixelSize: Theme.fontSizeBody
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideMiddle
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.window
        border.color: control.hovered ? Theme.borderStrong : Theme.border
        border.width: 1
    }
    ToolTip.visible: hovered && text.length > 0
    ToolTip.text: text
}
