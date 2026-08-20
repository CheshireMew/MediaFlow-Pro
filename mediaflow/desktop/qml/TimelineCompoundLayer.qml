pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: compoundClipLayer
    required property var view
    required property var timelineCanvas
    required property real tracksHeight
    objectName: "compoundClipLayer"
    x: 0
    y: 28
    width: timelineCanvas.contentWidth
    height: tracksHeight
    z: 5

    Repeater {
        objectName: "compoundClipRepeater"
        model: mediaflow.timelineViewController.compoundClipsModel
        delegate: Rectangle {
            id: compoundDelegate
            objectName: "timelineCompoundClip"
            required property string compoundId
            required property string name
            required property string primaryClipId
            required property var memberClipIds
            required property int memberCount
            required property string trackId
            required property int trackPosition
            required property string trackKind
            required property int startFrame
            required property int durationFrames
            readonly property bool selected:
                mediaflow.timelineViewController.selectedCompoundId === compoundId

            x: startFrame * view.pixelsPerFrame + (view.draggingClipId === primaryClipId ? view.draggingClipOffsetX : 0)
            y: (view.draggingClipId === primaryClipId ? view.draggingClipTrackPosition : trackPosition) * view.trackPitch + 12
            width: Math.max(28, durationFrames * view.pixelsPerFrame)
            height: 46
            radius: 7
            color: selected ? Theme.selectionSoft : Theme.compoundSoft
            border.width: selected ? 2 : 1
            border.color: selected
                ? Theme.accentHover
                : compoundMouse.containsMouse ? Theme.compound : Theme.borderStrong
            clip: true
            activeFocusOnTab: true
            Accessible.name: qsTr("复合片段 %1，包含 %2 个片段").arg(name).arg(memberCount)
            Accessible.role: Accessible.ListItem
            Keys.onReturnPressed: mediaflow.timelineViewController.selectCompoundClip(compoundId)
            Keys.onSpacePressed: mediaflow.timelineViewController.selectCompoundClip(compoundId)

            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: 6
                color: Theme.compound
            }
            Text {
                anchors.left: parent.left
                anchors.leftMargin: 14
                anchors.right: selectionMark.left
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("复合片段 · %1 个").arg(memberCount)
                color: Theme.textStrong
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            Rectangle {
                id: selectionMark
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 4
                width: 20
                height: 20
                radius: 10
                visible: compoundDelegate.selected
                color: Theme.accent
                border.color: Theme.textStrong
                AppIcon {
                    anchors.centerIn: parent
                    width: 12
                    height: 12
                    iconName: "check"
                    iconColor: Theme.onAccent
                    strokeWidth: 2.2
                }
            }
            MouseArea {
                id: compoundMouse
                anchors.fill: parent
                hoverEnabled: true
                preventStealing: true
                acceptedButtons: Qt.LeftButton
                property real pressContentX: 0
                property real pressContentY: 0
                cursorShape: view.canEdit
                    ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                    : Qt.ArrowCursor
                onPressed: function (mouse) {
                    mediaflow.timelineViewController.selectCompoundClip(compoundId);
                    if (view.canEdit) {
                        const point = compoundDelegate.mapToItem(
                            compoundClipLayer, mouse.x, mouse.y);
                        pressContentX = point.x;
                        pressContentY = point.y;
                        view.beginClipDrag(primaryClipId, trackPosition, trackKind);
                    }
                }
                onPositionChanged: function (mouse) {
                    if (!pressed || view.draggingClipId !== primaryClipId)
                        return;
                    const point = compoundDelegate.mapToItem(compoundClipLayer, mouse.x, mouse.y);
                    view.updateClipDrag(primaryClipId, startFrame, trackPosition, point.x - pressContentX, point.y - pressContentY);
                }
                onReleased: function (mouse) {
                    view.finishClipDrag(primaryClipId, startFrame, trackPosition, view.snapEnabled && (mouse.modifiers & Qt.ShiftModifier) === 0);
                }
                onCanceled: view.cancelClipDrag()
            }
        }
    }
}
