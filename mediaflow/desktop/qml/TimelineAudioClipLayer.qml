pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: embeddedAudioLayer
    required property var view
    required property var timelineCanvas
    required property real tracksHeight
    objectName: "embeddedAudioLayer"
    x: 0
    y: 28
    width: timelineCanvas.contentWidth
    height: tracksHeight
    z: 3

    property var clipRows: []

    function refreshOverview() {
        clipRows = mediaflow.timelineViewportController.visibleClipsModel.overview();
        embeddedAudioOverview.requestPaint();
    }

    function patchOverview(changedRows, removedIds) {
        const removed = {};
        const changed = {};
        for (let index = 0; index < removedIds.length; ++index)
            removed[String(removedIds[index])] = true;
        for (let index = 0; index < changedRows.length; ++index)
            changed[String(changedRows[index].clipId)] = changedRows[index];
        const nextRows = [];
        for (let index = 0; index < clipRows.length; ++index) {
            const clipId = String(clipRows[index].clipId);
            if (removed[clipId] === true)
                continue;
            if (changed[clipId] !== undefined) {
                nextRows.push(changed[clipId]);
                delete changed[clipId];
            } else {
                nextRows.push(clipRows[index]);
            }
        }
        for (const clipId in changed)
            nextRows.push(changed[clipId]);
        clipRows = nextRows;
        embeddedAudioOverview.requestPaint();
    }

    Component.onCompleted: refreshOverview()

    Connections {
        target: mediaflow.timelineViewportController.visibleClipsModel
        function onSourceItemsReset() { embeddedAudioLayer.refreshOverview(); }
        function onSourceItemsPatched(changedRows, removedIds) {
            embeddedAudioLayer.patchOverview(changedRows, removedIds);
        }
    }

    Connections {
        target: mediaflow.timelineViewController
        function onSelectionChanged() { embeddedAudioOverview.requestPaint(); }
    }

    Canvas {
        id: embeddedAudioOverview
        x: timelineCanvas.contentX
        width: timelineCanvas.width
        height: embeddedAudioLayer.height
        z: -1
        antialiasing: false
        property real scrollX: timelineCanvas.contentX
        property real pixelsScale: view.pixelsPerFrame
        onScrollXChanged: requestPaint()
        onPixelsScaleChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const context = getContext("2d");
            context.clearRect(0, 0, width, height);
            const firstFrame = scrollX / Math.max(0.000001, pixelsScale);
            const lastFrame = (scrollX + width) / Math.max(0.000001, pixelsScale);
            const selectedIds = mediaflow.timelineViewController.selectedClipIds;
            const selectedLookup = {};
            for (let selectedIndex = 0; selectedIndex < selectedIds.length; ++selectedIndex)
                selectedLookup[String(selectedIds[selectedIndex])] = true;
            for (let index = 0; index < embeddedAudioLayer.clipRows.length; ++index) {
                const row = embeddedAudioLayer.clipRows[index];
                if (String(row.compoundId || "").length > 0
                        || String(row.mediaKind) !== "linked_av"
                        || String(row.assetKind) !== "video"
                        || !Boolean(row.hasAudio)
                        || Number(row.audioTrackPosition) < 0
                        || Number(row.endFrame) < firstFrame
                        || Number(row.startFrame) > lastFrame)
                    continue;
                context.fillStyle = selectedLookup[String(row.clipId)] === true
                    ? Theme.selectionSoft : Theme.audioSoft;
                const x = Math.max(0, Number(row.startFrame) * pixelsScale - scrollX);
                const right = Math.min(width, Number(row.endFrame) * pixelsScale - scrollX);
                context.fillRect(
                    x,
                    Number(row.audioTrackPosition) * view.trackPitch + 10,
                    Math.max(1, right - x),
                    50);
            }
        }
    }

    Repeater {
        model: mediaflow.timelineViewportController.visibleClipsModel
        delegate: Rectangle {
            id: embeddedAudioDelegate
            required property string clipId
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
            required property bool hasAudio
            required property int audioTrackPosition
            required property bool waveformReady
            required property string compoundId
            readonly property bool selected: mediaflow.timelineViewController.isClipSelected(clipId)

            objectName: "embeddedAudioClip"
            readonly property string displayedTrackKind: view.draggingClipId === clipId ? view.draggingClipTrackKind : trackKind
            readonly property int displayedAudioTrackPosition: view.draggingClipId === clipId && view.draggingClipAudioTrackPosition >= 0 ? view.draggingClipAudioTrackPosition : audioTrackPosition
            visible: compoundId.length === 0 && displayedTrackKind === "video" && mediaKind === "linked_av" && assetKind === "video" && hasAudio && audioTrackPosition >= 0
            x: startFrame * view.pixelsPerFrame + (view.draggingClipId === clipId ? view.draggingClipOffsetX : 0)
            y: displayedAudioTrackPosition * view.trackPitch + 10
            width: Math.max(14, durationFrames * view.pixelsPerFrame)
            height: 50
            radius: 5
            color: selected ? Theme.selectionSoft : Theme.audioSoft
            border.width: selected ? 2 : 1
            border.color: selected
                ? Theme.accentHover
                : embeddedAudioMouse.containsMouse ? Theme.audio : Theme.borderStrong
            clip: true
            activeFocusOnTab: true
            Accessible.name: qsTr("%1 的音频，起始帧 %2，持续 %3 帧").arg(assetName).arg(startFrame).arg(durationFrames)
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
                visible: embeddedAudioDelegate.selected
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
                assetId: embeddedAudioDelegate.assetId
                sourceIn: embeddedAudioDelegate.sourceIn
                durationFrames: embeddedAudioDelegate.durationFrames
                speed: embeddedAudioDelegate.speed
                waveformReady: embeddedAudioDelegate.waveformReady
                viewport: timelineCanvas
                pixelsPerFrame: view.pixelsPerFrame
                clipContentX: embeddedAudioDelegate.x
                emphasized: embeddedAudioDelegate.selected
            }
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: 3
                color: Theme.audio
                opacity: embeddedAudioDelegate.selected
                    || embeddedAudioMouse.containsMouse ? 1 : 0.78
                z: 2
            }
            Text {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 6
                text: qsTr("音频 · ") + assetName
                color: Theme.textStrong
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.Medium
                elide: Text.ElideRight
            }
            TimelineClipInteraction {
                id: embeddedAudioMouse
                anchors.fill: parent
                view: embeddedAudioLayer.view
                clipItem: embeddedAudioDelegate
                layerItem: embeddedAudioLayer
                clipId: embeddedAudioDelegate.clipId
                startFrame: embeddedAudioDelegate.startFrame
                trackPosition: embeddedAudioDelegate.trackPosition
                trackKind: embeddedAudioDelegate.trackKind
                audioTrackPosition: embeddedAudioDelegate.audioTrackPosition
                linkedAudio: true
                dragEnabled: view.canEdit
            }
        }
    }
}
