pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: clipLayer
    required property var view
    required property var timelineCanvas
    required property real tracksHeight
    x: 0
    y: 28
    width: timelineCanvas.contentWidth
    height: Math.max(tracksHeight, timelineCanvas.height - 28)
    z: 2
    MouseArea {
        objectName: "timelineBlankSelectionArea"
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onPressed: function (mouse) {
            view.clearTimelineSelection();
            view.seekToFrame(mouse.x / view.pixelsPerFrame);
        }
    }
    Repeater {
        model: timelineController.clipsModel
        delegate: Rectangle {
            id: clipDelegate
            objectName: "timelineClip"
            required property string clipId
            required property string trackId
            required property int trackPosition
            required property string assetId
            required property string assetName
            required property int sourceIn
            required property int startFrame
            required property int durationFrames
            required property real speed
            required property string assetKind
            required property string trackKind
            required property string mediaKind
            required property int audioTrackPosition
            required property bool waveformReady
            required property string compoundId
            property real leftTrimOffset: 0
            property real rightTrimOffset: 0
            readonly property string displayedTrackKind: view.draggingClipId === clipId ? view.draggingClipTrackKind : trackKind
            readonly property bool selected: timelineController.isClipSelected(clipId)
            readonly property color clipAccent: displayedTrackKind === "audio"
                ? Theme.audio
                : assetKind === "image"
                    ? Theme.image
                    : assetKind === "web" ? Theme.web : Theme.video
            readonly property color clipSurface: displayedTrackKind === "audio"
                ? Theme.audioSoft
                : assetKind === "image"
                    ? Theme.imageSoft
                    : assetKind === "web" ? Theme.webSoft : Theme.videoSoft
            visible: compoundId.length === 0
            x: startFrame * view.pixelsPerFrame + (view.draggingClipId === clipId ? view.draggingClipOffsetX : 0) + leftTrimOffset
            y: (view.draggingClipId === clipId ? view.draggingClipTrackPosition : trackPosition) * view.trackPitch + 12
            width: Math.max(14, durationFrames * view.pixelsPerFrame - leftTrimOffset + rightTrimOffset)
            height: 46
            radius: 5
            color: selected ? Theme.selectionSoft : clipSurface
            border.width: selected ? 2 : 1
            border.color: selected
                ? Theme.accentHover
                : clipMouse.containsMouse ? clipAccent : Theme.borderStrong
            clip: true
            activeFocusOnTab: true
            Accessible.name: qsTr("片段 %1，起始帧 %2，持续 %3 帧").arg(assetName).arg(startFrame).arg(durationFrames)
            Accessible.role: Accessible.ListItem
            Keys.onReturnPressed: timelineController.selectClip(clipId)
            Keys.onSpacePressed: timelineController.selectClip(clipId)

            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 4
                width: 20
                height: 20
                radius: 10
                visible: clipDelegate.selected
                color: Theme.accent
                border.color: Theme.textStrong
                z: 20
                AppIcon {
                    anchors.centerIn: parent
                    width: 12
                    height: 12
                    iconName: "check"
                    iconColor: Theme.onAccent
                    strokeWidth: 2.2
                }
            }

            ClipWaveform {
                assetId: clipDelegate.assetId
                sourceIn: clipDelegate.sourceIn
                durationFrames: clipDelegate.durationFrames
                speed: clipDelegate.speed
                waveformReady: clipDelegate.waveformReady && clipDelegate.displayedTrackKind === "audio"
                viewport: timelineCanvas
                pixelsPerFrame: view.pixelsPerFrame
                clipContentX: clipDelegate.x
                emphasized: clipDelegate.selected
            }
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: 3
                color: clipDelegate.clipAccent
                opacity: clipDelegate.selected || clipMouse.containsMouse ? 1 : 0.78
                z: 2
            }
            Text {
                anchors.fill: parent
                anchors.margins: 7
                text: displayedTrackKind === "audio" && assetKind === "video" ? qsTr("音频 · ") + assetName : assetName
                color: Theme.textStrong
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.Medium
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }
            Repeater {
                model: assetKind === "web" && webController.isWebClip && timelineController.isClipSelected(clipId) ? webController.keyframesData : []
                delegate: Rectangle {
                    required property var modelData
                    x: Math.max(5, Math.min(clipDelegate.width - 7, (modelData.frame - clipDelegate.startFrame) * view.pixelsPerFrame - 4))
                    anchors.verticalCenter: parent.verticalCenter
                    width: 8
                    height: 8
                    rotation: 45
                    radius: 1
                    color: Theme.cut
                    border.color: Theme.textStrong
                    z: 7
                    ToolTip.visible: markerHover.hovered
                    ToolTip.text: modelData.layerId + "." + modelData.field + " · " + modelData.timeMs + " ms · " + modelData.easing
                    HoverHandler {
                        id: markerHover
                    }
                }
            }
            MouseArea {
                id: clipMouse
                anchors.fill: parent
                hoverEnabled: true
                preventStealing: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                property real pressContentX: 0
                property real pressContentY: 0
                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                onPressed: function (mouse) {
                    if (mouse.button === Qt.RightButton) {
                        if (!timelineController.isClipSelected(clipId))
                            timelineController.selectClip(clipId, false);
                        return;
                    }
                    const toggle = view.multiSelectMode || (mouse.modifiers & Qt.ControlModifier) !== 0;
                    if (toggle || !timelineController.isClipSelected(clipId))
                        timelineController.selectClip(clipId, toggle);
                    const point = clipDelegate.mapToItem(clipLayer, mouse.x, mouse.y);
                    pressContentX = point.x;
                    pressContentY = point.y;
                    view.beginClipDrag(clipId, trackPosition, trackKind, audioTrackPosition);
                }
                onPositionChanged: function (mouse) {
                    if (!pressed || view.draggingClipId !== clipId)
                        return;
                    const point = clipDelegate.mapToItem(clipLayer, mouse.x, mouse.y);
                    view.updateClipDrag(clipId, startFrame, trackPosition, point.x - pressContentX, point.y - pressContentY);
                }
                onReleased: function (mouse) {
                    if (mouse.button === Qt.RightButton) {
                        view.cancelClipDrag();
                        view.openClipContextMenu(clipId);
                        return;
                    }
                    view.finishClipDrag(clipId, startFrame, trackPosition, view.snapEnabled && (mouse.modifiers & Qt.ShiftModifier) === 0);
                }
                onCanceled: view.cancelClipDrag()
            }
            Rectangle {
                width: 6
                height: parent.height
                anchors.left: parent.left
                color: leftTrim.hovered ? Theme.cutHover : Theme.transparent
                z: 8
                HoverHandler {
                    id: leftTrim
                }
                DragHandler {
                    enabled: view.canEdit
                    target: null
                    xAxis.enabled: true
                    yAxis.enabled: false
                    onTranslationChanged: clipDelegate.leftTrimOffset = Math.max(-clipDelegate.startFrame * view.pixelsPerFrame, Math.min(clipDelegate.durationFrames * view.pixelsPerFrame - 8, translation.x))
                    onActiveChanged: if (!active && clipDelegate.leftTrimOffset !== 0) {
                        const delta = Math.round(clipDelegate.leftTrimOffset / view.pixelsPerFrame);
                        timelineController.trimClipEdges(clipDelegate.clipId, clipDelegate.startFrame + delta, clipDelegate.durationFrames - delta, true);
                        clipDelegate.leftTrimOffset = 0;
                    }
                }
            }
            Rectangle {
                width: 6
                height: parent.height
                anchors.right: parent.right
                color: rightTrim.hovered ? Theme.cutHover : Theme.transparent
                z: 8
                HoverHandler {
                    id: rightTrim
                }
                DragHandler {
                    enabled: view.canEdit
                    target: null
                    xAxis.enabled: true
                    yAxis.enabled: false
                    onTranslationChanged: clipDelegate.rightTrimOffset = Math.max(-(clipDelegate.durationFrames * view.pixelsPerFrame - 8), translation.x)
                    onActiveChanged: if (!active && clipDelegate.rightTrimOffset !== 0) {
                        const delta = Math.round(clipDelegate.rightTrimOffset / view.pixelsPerFrame);
                        timelineController.trimClipEdges(clipDelegate.clipId, clipDelegate.startFrame, clipDelegate.durationFrames + delta, false);
                        clipDelegate.rightTrimOffset = 0;
                    }
                }
            }
        }
    }
}
