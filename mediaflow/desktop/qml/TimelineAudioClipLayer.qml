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

    Repeater {
        model: mediaflow.timelineViewController.clipsModel
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
