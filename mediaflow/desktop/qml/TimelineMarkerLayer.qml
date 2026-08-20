pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: markerLayer
    required property var view
    required property var timelineCanvas
    x: 0
    y: 0
    width: timelineCanvas.contentWidth
    height: timelineCanvas.contentHeight
    z: 8
    Repeater {
        model: mediaflow.timelineViewController.timelineMarkersModel
        delegate: Rectangle {
            required property string markerId
            required property int frame
            required property string name
            required property string markerColor
            readonly property color effectiveMarkerColor:
                markerColor.length > 0 ? markerColor : Theme.marker
            x: frame * view.pixelsPerFrame
            width: 2
            height: markerLayer.height
            color: effectiveMarkerColor
            activeFocusOnTab: true
            Accessible.name: qsTr("标记 %1，位于第 %2 帧").arg(name).arg(frame)
            Accessible.role: Accessible.Button
            Keys.onReturnPressed: view.seekToFrame(frame)
            Keys.onSpacePressed: view.seekToFrame(frame)
            Rectangle {
                x: -7
                y: -2
                width: 16
                height: 16
                radius: 8
                visible: markerMouse.containsMouse
                color: Theme.markerSoft
            }
            Rectangle {
                x: -5
                width: 12
                height: 12
                radius: 2
                color: effectiveMarkerColor
                rotation: 45
                border.color: markerMouse.containsMouse
                    ? Theme.marker : Theme.timelineRuler
                border.width: markerMouse.containsMouse ? 2 : 1
            }
            ToolTip.visible: markerMouse.containsMouse
            ToolTip.text: name + " · " + frame
            MouseArea {
                id: markerMouse
                x: -7
                width: 16
                height: 24
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                onClicked: function (mouse) {
                    if (mouse.button === Qt.RightButton && view.canEdit)
                        mediaflow.timelineStructureController.removeTimelineMarker(markerId);
                    else
                        view.seekToFrame(frame);
                }
            }
        }
    }
}
