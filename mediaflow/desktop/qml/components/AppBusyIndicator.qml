pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import ".."

BusyIndicator {
    id: control
    implicitWidth: 24
    implicitHeight: 24

    contentItem: Item {
        implicitWidth: 24
        implicitHeight: 24
        visible: control.running

        Item {
            id: spinner
            anchors.fill: parent
            Repeater {
                model: 8
                Item {
                    id: spoke
                    required property int index
                    anchors.fill: parent
                    rotation: spoke.index * 45
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: parent.top
                        width: 3
                        height: Math.max(5, parent.height * 0.26)
                        radius: width / 2
                        color: control.enabled ? Theme.accent : Theme.textDisabled
                        opacity: 0.3 + spoke.index * 0.08
                    }
                }
            }

            RotationAnimator on rotation {
                from: 0
                to: 360
                duration: 780
                loops: Animation.Infinite
                running: control.running && control.visible
            }
        }
    }
}
