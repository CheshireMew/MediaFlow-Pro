import QtQuick
import QtQuick.Controls
import ".."

ScrollBar {
    id: control

    readonly property bool hasScrollableRange: size < 0.999

    hoverEnabled: true
    padding: 2
    implicitWidth: orientation === Qt.Vertical ? 10 : 40
    implicitHeight: orientation === Qt.Horizontal ? 10 : 40

    contentItem: Rectangle {
        implicitWidth: 6
        implicitHeight: 6
        radius: Math.min(width, height) / 2
        color: !control.enabled
            ? Theme.textDisabled
            : control.pressed
            ? Theme.accent
            : control.hovered ? Theme.textSubtle : Theme.borderStrong
        opacity: control.policy === ScrollBar.AlwaysOff
            || (control.policy === ScrollBar.AsNeeded && !control.hasScrollableRange)
            ? 0
            : control.active || control.policy === ScrollBar.AlwaysOn ? 1 : 0.68

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
        Behavior on opacity {
            NumberAnimation { duration: Theme.duration }
        }
    }

    background: Rectangle {
        radius: Theme.radiusSmall
        color: (control.policy === ScrollBar.AlwaysOn || control.hasScrollableRange)
            && (control.active || control.hovered)
            ? Theme.surfaceSunken : Theme.transparent

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
}
