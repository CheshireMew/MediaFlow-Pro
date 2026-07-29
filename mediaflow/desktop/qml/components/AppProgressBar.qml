import QtQuick
import QtQuick.Controls
import ".."

ProgressBar {
    id: control
    implicitWidth: 200
    implicitHeight: 8
    padding: 0

    background: Rectangle {
        implicitWidth: 200
        implicitHeight: 6
        y: Math.round((control.height - height) / 2)
        radius: height / 2
        color: control.enabled ? Theme.progressTrack : Theme.controlDisabled
    }

    contentItem: Item {
        id: progressContent
        implicitWidth: 200
        implicitHeight: 6
        clip: true

        Rectangle {
            visible: !control.indeterminate
            width: progressContent.width * control.visualPosition
            height: progressContent.height
            radius: height / 2
            color: control.enabled ? Theme.progressFill : Theme.textDisabled

            Behavior on width {
                enabled: control.visible && !control.indeterminate
                NumberAnimation {
                    duration: Theme.duration
                    easing.type: Easing.OutCubic
                }
            }
        }

        Rectangle {
            id: indeterminateSegment
            visible: control.indeterminate
            x: -width
            width: Math.max(42, progressContent.width * 0.28)
            height: progressContent.height
            radius: height / 2
            color: control.enabled ? Theme.progressFill : Theme.textDisabled

            NumberAnimation on x {
                from: -indeterminateSegment.width
                to: progressContent.width
                duration: Theme.durationProgress
                loops: Animation.Infinite
                running: control.visible && control.indeterminate
                easing.type: Easing.InOutCubic
            }
        }
    }
}
