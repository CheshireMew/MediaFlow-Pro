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
        model: mediaflow.timelineViewController.clipsModel
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
            required property var filmstripFrames
            required property bool hasAudio
            required property real gainDb
            required property real pan
            required property int fadeInFrames
            required property int fadeOutFrames
            required property string compoundId
            property real leftTrimOffset: 0
            property real rightTrimOffset: 0
            readonly property string displayedTrackKind: view.draggingClipId === clipId ? view.draggingClipTrackKind : trackKind
            readonly property bool selected: mediaflow.timelineViewController.isClipSelected(clipId)
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
            Keys.onReturnPressed: mediaflow.timelineViewController.selectClip(clipId)
            Keys.onSpacePressed: mediaflow.timelineViewController.selectClip(clipId)

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
            Repeater {
                model: clipDelegate.filmstripFrames
                delegate: Image {
                    required property var modelData
                    x: (Number(modelData.timelineFrame) - clipDelegate.startFrame)
                        * view.pixelsPerFrame
                    y: 0
                    width: 78
                    height: clipDelegate.height
                    source: String(modelData.url || "")
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    cache: true
                    opacity: clipDelegate.selected ? 0.42 : 0.3
                }
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
            Rectangle {
                id: fadeInHandle
                objectName: "clipFadeInHandle"
                visible: clipDelegate.hasAudio && view.canEdit
                    && clipDelegate.width >= 36
                x: Math.max(2, Math.min(
                    clipDelegate.width / 2 - width,
                    clipDelegate.fadeInFrames * view.pixelsPerFrame - width / 2))
                y: 2
                width: 10
                height: 10
                radius: 5
                color: Theme.audio
                border.color: Theme.textStrong
                z: 25
                DragHandler {
                    target: null
                    xAxis.enabled: true
                    yAxis.enabled: false
                    onActiveChanged: if (!active) {
                        const next = Math.max(0, Math.min(
                            clipDelegate.durationFrames - clipDelegate.fadeOutFrames,
                            Math.round((fadeInHandle.x + width / 2) / view.pixelsPerFrame)));
                        mediaflow.timelineClipController.setClipAudio(
                            clipDelegate.clipId, clipDelegate.gainDb,
                            clipDelegate.pan, next, clipDelegate.fadeOutFrames);
                    }
                }
                ToolTip.visible: fadeInHover.hovered
                ToolTip.text: qsTr("淡入 %1 帧").arg(clipDelegate.fadeInFrames)
                HoverHandler { id: fadeInHover }
            }
            Rectangle {
                id: fadeOutHandle
                objectName: "clipFadeOutHandle"
                visible: clipDelegate.hasAudio && view.canEdit
                    && clipDelegate.width >= 36
                x: Math.max(clipDelegate.width / 2, Math.min(
                    clipDelegate.width - width - 2,
                    clipDelegate.width
                        - clipDelegate.fadeOutFrames * view.pixelsPerFrame
                        - width / 2))
                y: 2
                width: 10
                height: 10
                radius: 5
                color: Theme.audio
                border.color: Theme.textStrong
                z: 25
                DragHandler {
                    target: null
                    xAxis.enabled: true
                    yAxis.enabled: false
                    onActiveChanged: if (!active) {
                        const next = Math.max(0, Math.min(
                            clipDelegate.durationFrames - clipDelegate.fadeInFrames,
                            Math.round((clipDelegate.width - fadeOutHandle.x
                                - width / 2) / view.pixelsPerFrame)));
                        mediaflow.timelineClipController.setClipAudio(
                            clipDelegate.clipId, clipDelegate.gainDb,
                            clipDelegate.pan, clipDelegate.fadeInFrames, next);
                    }
                }
                ToolTip.visible: fadeOutHover.hovered
                ToolTip.text: qsTr("淡出 %1 帧").arg(clipDelegate.fadeOutFrames)
                HoverHandler { id: fadeOutHover }
            }
            Repeater {
                model: assetKind === "web" && mediaflow.webController.isWebClip && mediaflow.timelineViewController.isClipSelected(clipId) ? mediaflow.webTimelineController.keyframesData : []
                delegate: Rectangle {
                    id: keyframeMarker
                    required property var modelData
                    property int previewFrame: Number(modelData.frame)
                    x: Math.max(5, Math.min(
                        clipDelegate.width - 7,
                        (previewFrame - clipDelegate.startFrame)
                            * view.pixelsPerFrame - 4))
                    y: Math.max(0, (clipDelegate.height - height) / 2)
                    width: 8
                    height: 8
                    rotation: 45
                    radius: 1
                    color: Theme.cut
                    border.color: Theme.textStrong
                    z: 7
                    ToolTip.visible: markerHover.hovered
                    ToolTip.text: (modelData.target === "parameter"
                        ? qsTr("参数 · ") + modelData.field
                        : modelData.layerId + "." + modelData.field)
                        + " · " + modelData.timeMs + " ms · " + modelData.easing
                    HoverHandler {
                        id: markerHover
                    }
                    DragHandler {
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        property int initialFrame: 0
                        onActiveChanged: {
                            if (active) {
                                initialFrame = Number(keyframeMarker.modelData.frame);
                            } else if (keyframeMarker.previewFrame !== initialFrame) {
                                mediaflow.webTimelineController.moveTimelineKeyframe(
                                    String(keyframeMarker.modelData.target),
                                    String(keyframeMarker.modelData.sourceId),
                                    Number(keyframeMarker.modelData.timeMs),
                                    keyframeMarker.previewFrame);
                            }
                        }
                        onTranslationChanged: {
                            if (!active)
                                return;
                            const rawFrame = initialFrame
                                + translation.x / view.pixelsPerFrame;
                            keyframeMarker.previewFrame = Math.max(
                                clipDelegate.startFrame,
                                Math.min(
                                    clipDelegate.startFrame
                                        + clipDelegate.durationFrames - 1,
                                    Math.round(rawFrame)));
                            view.seekToFrame(keyframeMarker.previewFrame);
                            mediaflow.webTimelineController.previewTimelineKeyframe(
                                String(keyframeMarker.modelData.target),
                                String(keyframeMarker.modelData.sourceId),
                                Number(keyframeMarker.modelData.timeMs),
                                keyframeMarker.previewFrame);
                        }
                        onCanceled: {
                            keyframeMarker.previewFrame = initialFrame;
                            mediaflow.webTimelineController.cancelTimelinePreview();
                        }
                    }
                }
            }
            TimelineClipInteraction {
                id: clipMouse
                anchors.fill: parent
                view: clipLayer.view
                clipItem: clipDelegate
                layerItem: clipLayer
                clipId: clipDelegate.clipId
                startFrame: clipDelegate.startFrame
                trackPosition: clipDelegate.trackPosition
                trackKind: clipDelegate.trackKind
                audioTrackPosition: clipDelegate.audioTrackPosition
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
                        mediaflow.timelineClipController.trimClipEdges(clipDelegate.clipId, clipDelegate.startFrame + delta, clipDelegate.durationFrames - delta, true);
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
                        mediaflow.timelineClipController.trimClipEdges(clipDelegate.clipId, clipDelegate.startFrame, clipDelegate.durationFrames + delta, false);
                        clipDelegate.rightTrimOffset = 0;
                    }
                }
            }
        }
    }
}
