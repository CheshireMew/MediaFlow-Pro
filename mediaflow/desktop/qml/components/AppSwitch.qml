import QtQuick
import QtQuick.Controls
import ".."

Switch {
    id: control
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    spacing: 9
    implicitHeight: Math.max(Theme.controlHeightCompact, implicitContentHeight)
    font.pixelSize: Theme.fontSizeBody

    indicator: Rectangle {
        implicitWidth: 38
        implicitHeight: 22
        x: control.leftPadding
        y: Math.round((control.height - height) / 2)
        radius: height / 2
        color: !control.enabled
            ? Theme.controlDisabled
            : control.checked ? Theme.accent : Theme.progressTrack
        border.color: control.activeFocus
            ? Theme.focusColor
            : control.hovered ? Theme.borderStrong : Theme.border
        border.width: control.activeFocus ? 2 : 1

        Rectangle {
            x: 3 + control.visualPosition * (parent.width - width - 6)
            anchors.verticalCenter: parent.verticalCenter
            width: 16
            height: 16
            radius: width / 2
            color: !control.enabled
                ? Theme.textDisabled
                : control.checked ? Theme.onAccent : Theme.textSubtle

            Behavior on x {
                NumberAnimation {
                    duration: Theme.duration
                    easing.type: Easing.OutCubic
                }
            }
            Behavior on color {
                ColorAnimation { duration: Theme.durationFast }
            }
        }

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }

    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        color: control.enabled ? Theme.text : Theme.textDisabled
        font: control.font
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
    }
}
