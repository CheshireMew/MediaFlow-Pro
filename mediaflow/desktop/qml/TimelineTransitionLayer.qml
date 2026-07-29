pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: transitionLayer
    required property var view
    required property var timelineCanvas
    required property real tracksHeight
    objectName: "transitionLayer"
    x: 0
    y: 28
    width: timelineCanvas.contentWidth
    height: tracksHeight
    z: 7
    Repeater {
        model: timelineController.transitionsModel
        delegate: Item {
            objectName: "timelineTransition"
            required property string transitionId
            required property int trackPosition
            required property string kind
            required property int durationFrames
            required property int boundaryFrame
            required property bool internalToCompound
            readonly property bool selected:
                timelineController.selectedTransitionId === transitionId
            x: (boundaryFrame - durationFrames / 2) * view.pixelsPerFrame
            y: trackPosition * view.trackPitch + 19
            width: Math.max(24, durationFrames * view.pixelsPerFrame)
            height: 32
            visible: !internalToCompound
            activeFocusOnTab: true
            Accessible.name: qsTr("转场 %1，持续 %2 帧").arg(kind).arg(durationFrames)
            Accessible.role: Accessible.Button
            Keys.onReturnPressed: timelineController.selectTransition(transitionId)
            Keys.onSpacePressed: timelineController.selectTransition(transitionId)

            Rectangle {
                anchors.fill: parent
                anchors.topMargin: 9
                anchors.bottomMargin: 9
                radius: 7
                color: parent.selected ? Theme.selectionSoft : Theme.transitionSoft
                border.color: parent.selected
                    ? Theme.accentHover
                    : transitionMouse.containsMouse ? Theme.transition : Theme.borderStrong
                border.width: parent.selected ? 2 : 1
            }
            Rectangle {
                width: 24
                height: 22
                anchors.centerIn: parent
                radius: 11
                color: parent.selected ? Theme.transition : Theme.timelineRuler
                border.color: Theme.transition
                AppIcon {
                    objectName: "transitionCrossfadeIcon"
                    anchors.centerIn: parent
                    width: 15
                    height: 15
                    iconName: "transition"
                    iconColor: Theme.textStrong
                    strokeWidth: 2
                }
            }
            ToolTip.visible: transitionMouse.containsMouse
            ToolTip.text: kind + " · " + durationFrames + qsTr(" 帧")
            MouseArea {
                id: transitionMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: timelineController.selectTransition(transitionId)
            }
        }
    }
}
