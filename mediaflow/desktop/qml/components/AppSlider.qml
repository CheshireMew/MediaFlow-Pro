import QtQuick
import QtQuick.Controls
import ".."

Slider {
    id: control
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitWidth: orientation === Qt.Horizontal ? 160 : 20
    implicitHeight: orientation === Qt.Horizontal ? 20 : 160

    background: Rectangle {
        x: control.orientation === Qt.Horizontal
            ? control.leftPadding
            : control.leftPadding + Math.round((control.availableWidth - width) / 2)
        y: control.orientation === Qt.Horizontal
            ? control.topPadding + Math.round((control.availableHeight - height) / 2)
            : control.topPadding
        width: control.orientation === Qt.Horizontal ? control.availableWidth : 4
        height: control.orientation === Qt.Horizontal ? 4 : control.availableHeight
        radius: 2
        color: control.enabled ? Theme.progressTrack : Theme.controlDisabled

        Rectangle {
            width: control.orientation === Qt.Horizontal
                ? parent.width * control.visualPosition
                : parent.width
            height: control.orientation === Qt.Horizontal
                ? parent.height
                : parent.height * control.visualPosition
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            radius: parent.radius
            color: control.enabled ? Theme.accent : Theme.textDisabled
        }
    }

    handle: Rectangle {
        x: control.orientation === Qt.Horizontal
            ? control.leftPadding
                + control.visualPosition * (control.availableWidth - width)
            : control.leftPadding + Math.round((control.availableWidth - width) / 2)
        y: control.orientation === Qt.Horizontal
            ? control.topPadding + Math.round((control.availableHeight - height) / 2)
            : control.topPadding
                + control.visualPosition * (control.availableHeight - height)
        implicitWidth: 16
        implicitHeight: 16
        radius: width / 2
        color: !control.enabled
            ? Theme.textDisabled
            : control.pressed ? Theme.accentPressed : Theme.accent
        border.color: control.visualFocus
            ? Theme.textStrong
            : control.hovered ? Theme.accentHover : Theme.borderStrong
        border.width: control.visualFocus ? 2 : 1

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
}
