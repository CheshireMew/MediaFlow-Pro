import QtQuick
import QtQuick.Controls
import ".."

TabButton {
    id: control
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitWidth: Math.max(72, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Theme.controlHeightLarge
    leftPadding: 14
    rightPadding: 14
    topPadding: 8
    bottomPadding: 8
    font.pixelSize: Theme.fontSizeBodySmall
    font.weight: checked ? Font.DemiBold : Font.Medium

    contentItem: Text {
        text: control.text
        color: !control.enabled
            ? Theme.textDisabled
            : control.checked ? Theme.text : Theme.textSubtle
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Item {
        Rectangle {
            anchors.fill: parent
            anchors.bottomMargin: 2
            radius: Theme.radiusSmall
            color: !control.enabled
                ? Theme.transparent
                : control.down
                ? Theme.controlPressed
                : control.hovered ? Theme.controlHover : Theme.transparent
            border.color: control.visualFocus ? Theme.focusColor : Theme.transparent
            border.width: control.visualFocus ? 1 : 0

            Behavior on color {
                ColorAnimation { duration: Theme.durationFast }
            }
        }
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 10
            anchors.rightMargin: 10
            height: control.checked ? 2 : 0
            radius: 1
            color: Theme.accent

            Behavior on height {
                NumberAnimation { duration: Theme.durationFast }
            }
        }
    }
}
