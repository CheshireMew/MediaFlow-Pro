import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    color: Theme.surface
    border.color: Theme.border
    property real pixelsPerFrame: 3.0
    property int playheadFrame: 0
    property int headerWidth: 244
    property int trackHeight: 72
    property int trackPitch: trackHeight + 1
    readonly property int fpsRounded: Math.max(1, Math.round(
        projectController.profileFpsNumerator / Math.max(1, projectController.profileFpsDenominator)))
    readonly property int rulerSeconds: Math.max(
        10, Math.ceil((projectController.timelineDurationFrames + fpsRounded * 2) / fpsRounded))
    signal seekRequested(int frame)

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 44
            color: Theme.surfaceRaised
            border.color: Theme.border
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                spacing: 7
                AppButton { text: "✂"; Accessible.name: qsTr("分割片段"); enabled: projectController.selectedClipId.length > 0; onClicked: projectController.splitClip(projectController.selectedClipId, root.playheadFrame) }
                AppButton {
                    text: qsTr("复制")
                    enabled: projectController.selectedClipId.length > 0
                    onClicked: projectController.copyClip(
                        projectController.selectedClipId, root.pixelsPerFrame, root.playheadFrame)
                }
                AppButton { text: "⌫"; Accessible.name: qsTr("删除片段"); enabled: projectController.selectedClipId.length > 0; onClicked: projectController.deleteClip(projectController.selectedClipId, false) }
                AppButton { text: qsTr("波纹删除"); enabled: projectController.selectedClipId.length > 0; onClicked: projectController.deleteClip(projectController.selectedClipId, true) }
                AppButton { text: qsTr("标记"); onClicked: projectController.addTimelineMarker(root.playheadFrame) }
                AppButton {
                    text: projectController.rangeInFrame < 0 ? qsTr("设入点") : qsTr("入点 %1").arg(projectController.rangeInFrame)
                    onClicked: projectController.setRangeIn(root.playheadFrame)
                }
                AppButton {
                    text: qsTr("设出点")
                    enabled: projectController.rangeInFrame >= 0
                    onClicked: projectController.commitTimelineRange(root.playheadFrame)
                }
                AppButton {
                    text: qsTr("选区转短视频")
                    enabled: projectController.selectedRangeId.length > 0
                    onClicked: projectController.createShortFromRange(projectController.selectedRangeId)
                }
                AppButton { text: "+V"; onClicked: projectController.addTrack("video"); ToolTip.visible: hovered; ToolTip.text: qsTr("添加视频轨") }
                AppButton { text: "+A"; onClicked: projectController.addTrack("audio"); ToolTip.visible: hovered; ToolTip.text: qsTr("添加音频轨") }
                AppButton { text: "+CC"; onClicked: projectController.addTrack("subtitle"); ToolTip.visible: hovered; ToolTip.text: qsTr("添加字幕轨") }
                Item { Layout.fillWidth: true }
                Text { text: qsTr("缩放"); color: Theme.textMuted; font.pixelSize: 11 }
                Slider {
                    from: 0.5
                    to: 12
                    value: root.pixelsPerFrame
                    onMoved: root.pixelsPerFrame = value
                    Layout.preferredWidth: 130
                }
            }
        }

        Flickable {
            id: timelineFlick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: Math.max(
                width, root.headerWidth + root.rulerSeconds * root.fpsRounded * root.pixelsPerFrame)
            contentHeight: Math.max(height, tracksColumn.height + 32)
            boundsBehavior: Flickable.StopAtBounds

            Rectangle {
                x: 0
                y: 0
                width: root.headerWidth
                height: timelineFlick.contentHeight
                color: "#101318"
                z: 4
            }

            Row {
                x: root.headerWidth
                y: 0
                spacing: 0
                Repeater {
                    model: root.rulerSeconds
                    Rectangle {
                        width: root.fpsRounded * root.pixelsPerFrame
                        height: 28
                        color: index % 2 === 0 ? "#15191f" : "#13171c"
                        border.color: Theme.border
                        Text {
                            anchors.left: parent.left
                            anchors.leftMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            text: index + "s"
                            color: Theme.textMuted
                            font.pixelSize: 9
                        }
                    }
                }
            }

            Item {
                id: rangeLayer
                x: root.headerWidth
                y: 28
                width: timelineFlick.contentWidth - root.headerWidth
                height: tracksColumn.height
                z: 1
                Repeater {
                    model: projectController.timelineRangesModel
                    delegate: Rectangle {
                        required property string rangeId
                        required property int startFrame
                        required property int endFrame
                            required property string name
                            required property string displayName
                        required property string rangeColor
                        x: startFrame * root.pixelsPerFrame
                        width: Math.max(2, (endFrame - startFrame) * root.pixelsPerFrame)
                        height: rangeLayer.height
                        color: Qt.rgba(
                            Qt.color(rangeColor).r, Qt.color(rangeColor).g,
                            Qt.color(rangeColor).b, 0.12)
                        border.color: Qt.rgba(
                            Qt.color(rangeColor).r, Qt.color(rangeColor).g,
                            Qt.color(rangeColor).b, 0.52)
                        ToolTip.visible: rangeMouse.containsMouse
                        ToolTip.text: name + "  " + startFrame + "–" + endFrame
                        MouseArea {
                            id: rangeMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: function(mouse) {
                                if (mouse.button === Qt.RightButton)
                                    projectController.removeTimelineRange(rangeId)
                                else {
                                    projectController.selectTimelineRange(rangeId)
                                    root.seekRequested(startFrame)
                                }
                            }
                        }
                    }
                }
            }

            Column {
                id: tracksColumn
                y: 28
                width: timelineFlick.contentWidth
                spacing: 1
                Repeater {
                    model: projectController.tracksModel
                    delegate: Rectangle {
                        id: trackRow
                        required property string trackId
                        required property int index
                        required property string name
                        required property string displayName
                        required property string kind
                        required property int position
                        required property bool locked
                        required property bool muted
                        required property bool solo
                        required property string audioBusId
                        required property var model
                        width: tracksColumn.width
                        height: root.trackHeight
                        color: index % 2 === 0 ? "#111419" : "#0f1216"

                        Rectangle {
                            width: root.headerWidth
                            height: parent.height
                            color: Theme.surfaceRaised
                            border.color: Theme.border
                            z: 3
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 6
                                spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Text { text: kind === "video" ? "▣" : kind === "audio" ? "♫" : "CC"; color: Theme.textMuted; font.pixelSize: 11 }
                                    Text { Layout.fillWidth: true; text: displayName; color: model.enabled ? Theme.text : Theme.textMuted; font.pixelSize: 11; elide: Text.ElideRight }
                                    Text { text: position + 1; color: Theme.textMuted; font.pixelSize: 9 }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Button {
                                        text: model.enabled ? "●" : "○"; implicitWidth: 28; implicitHeight: 24
                                        ToolTip.visible: hovered; ToolTip.text: model.enabled ? qsTr("禁用轨道") : qsTr("启用轨道")
                                        onClicked: projectController.updateTrack(trackId, !model.enabled, locked, muted, solo, audioBusId)
                                    }
                                    Button {
                                        text: locked ? "🔒" : "◇"; implicitWidth: 28; implicitHeight: 24
                                        ToolTip.visible: hovered; ToolTip.text: locked ? qsTr("解锁轨道") : qsTr("锁定轨道")
                                        onClicked: projectController.updateTrack(trackId, model.enabled, !locked, muted, solo, audioBusId)
                                    }
                                    Button {
                                        text: "M"; checkable: true; checked: muted; implicitWidth: 28; implicitHeight: 24
                                        ToolTip.visible: hovered; ToolTip.text: muted ? qsTr("取消静音") : qsTr("静音")
                                        onClicked: projectController.updateTrack(trackId, model.enabled, locked, !muted, solo, audioBusId)
                                    }
                                    Button {
                                        text: "S"; checkable: true; checked: solo; implicitWidth: 28; implicitHeight: 24
                                        ToolTip.visible: hovered; ToolTip.text: solo ? qsTr("取消独奏") : qsTr("独奏")
                                        onClicked: projectController.updateTrack(trackId, model.enabled, locked, muted, !solo, audioBusId)
                                    }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        text: "↑"; enabled: position > 0; implicitWidth: 28; implicitHeight: 24
                                        ToolTip.visible: hovered; ToolTip.text: qsTr("轨道上移")
                                        onClicked: projectController.moveTrack(trackId, position - 1)
                                    }
                                    Button {
                                        text: "↓"; enabled: position + 1 < projectController.tracksModel.rowCount(); implicitWidth: 28; implicitHeight: 24
                                        ToolTip.visible: hovered; ToolTip.text: qsTr("轨道下移")
                                        onClicked: projectController.moveTrack(trackId, position + 1)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Item {
                id: clipLayer
                x: 0
                y: 28
                width: timelineFlick.contentWidth
                height: tracksColumn.height
                z: 2
                Repeater {
                    model: projectController.clipsModel
                    delegate: Rectangle {
                        id: clipDelegate
                        required property string clipId
                        required property string trackId
                        required property int trackPosition
                        required property string assetId
                        required property string assetName
                        required property int sourceIn
                        required property int startFrame
                        required property int durationFrames
                        required property real speed
                        required property string kind
                        required property bool waveformReady
                        property real leftTrimOffset: 0
                        property real rightTrimOffset: 0
                        x: root.headerWidth + startFrame * root.pixelsPerFrame + leftTrimOffset
                        y: trackPosition * root.trackPitch + 12
                        width: Math.max(
                            14, durationFrames * root.pixelsPerFrame - leftTrimOffset + rightTrimOffset)
                        height: 46
                        radius: 5
                        color: kind === "audio" ? Theme.audio : kind === "image" ? Theme.subtitle : Theme.video
                        border.width: projectController.selectedClipId === clipId ? 2 : 1
                        border.color: projectController.selectedClipId === clipId ? "white" : Qt.lighter(color, 1.25)
                        clip: true
                        activeFocusOnTab: true
                        Accessible.name: qsTr("片段 %1，起始帧 %2，持续 %3 帧").arg(assetName).arg(startFrame).arg(durationFrames)
                        Accessible.role: Accessible.ListItem
                        Keys.onReturnPressed: projectController.selectClip(clipId)
                        Keys.onSpacePressed: projectController.selectClip(clipId)

                        Canvas {
                            id: waveform
                            anchors.fill: parent
                            anchors.margins: 3
                            visible: waveformReady && (kind === "audio" || kind === "video")
                            opacity: 0.55
                            onWidthChanged: requestPaint()
                            onPaint: {
                                var context = getContext("2d")
                                context.clearRect(0, 0, width, height)
                                var peaks = projectController.waveformPeaks(
                                    assetId, sourceIn, durationFrames, speed, Math.round(width))
                                if (!peaks || peaks.length < 2)
                                    return
                                var count = peaks.length / 2
                                context.strokeStyle = "rgba(255,255,255,0.9)"
                                context.lineWidth = 1
                                context.beginPath()
                                for (var i = 0; i < count; ++i) {
                                    var px = count === 1 ? 0 : i * (width - 1) / (count - 1)
                                    var minimum = Number(peaks[i * 2])
                                    var maximum = Number(peaks[i * 2 + 1])
                                    context.moveTo(px, height * (0.5 - maximum * 0.46))
                                    context.lineTo(px, height * (0.5 - minimum * 0.46))
                                }
                                context.stroke()
                            }
                        }
                        Text {
                            anchors.fill: parent
                            anchors.margins: 7
                            text: assetName
                            color: "white"
                            font.pixelSize: 10
                            font.weight: Font.Medium
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                        MouseArea {
                            id: clipMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            drag.target: clipDelegate
                            drag.axis: Drag.XAndYAxis
                            cursorShape: Qt.OpenHandCursor
                            onPressed: {
                                cursorShape = Qt.ClosedHandCursor
                                projectController.selectClip(clipId)
                            }
                            onReleased: {
                                cursorShape = Qt.OpenHandCursor
                                var nextFrame = Math.max(0, Math.round((clipDelegate.x - root.headerWidth) / root.pixelsPerFrame))
                                var nextTrack = Math.max(0, Math.min(
                                    projectController.tracksModel.rowCount() - 1,
                                    Math.floor((clipDelegate.y + clipDelegate.height / 2) / root.trackPitch)))
                                var track = projectController.tracksModel.get(nextTrack)
                                projectController.moveClip(
                                    clipId, nextFrame, String(track.trackId),
                                    root.pixelsPerFrame, root.playheadFrame)
                            }
                        }
                        Rectangle {
                            width: 6; height: parent.height; anchors.left: parent.left
                            color: leftTrim.hovered ? "white" : "transparent"
                            z: 8
                            HoverHandler { id: leftTrim }
                            DragHandler {
                                target: null
                                xAxis.enabled: true; yAxis.enabled: false
                                onTranslationChanged: clipDelegate.leftTrimOffset = Math.max(
                                    -clipDelegate.startFrame * root.pixelsPerFrame,
                                    Math.min(clipDelegate.durationFrames * root.pixelsPerFrame - 8, translation.x))
                                onActiveChanged: if (!active && clipDelegate.leftTrimOffset !== 0) {
                                    const delta = Math.round(clipDelegate.leftTrimOffset / root.pixelsPerFrame)
                                    projectController.trimClipEdges(
                                        clipDelegate.clipId,
                                        clipDelegate.startFrame + delta,
                                        clipDelegate.durationFrames - delta,
                                        true)
                                    clipDelegate.leftTrimOffset = 0
                                }
                            }
                        }
                        Rectangle {
                            width: 6; height: parent.height; anchors.right: parent.right
                            color: rightTrim.hovered ? "white" : "transparent"
                            z: 8
                            HoverHandler { id: rightTrim }
                            DragHandler {
                                target: null
                                xAxis.enabled: true; yAxis.enabled: false
                                onTranslationChanged: clipDelegate.rightTrimOffset = Math.max(
                                    -(clipDelegate.durationFrames * root.pixelsPerFrame - 8), translation.x)
                                onActiveChanged: if (!active && clipDelegate.rightTrimOffset !== 0) {
                                    const delta = Math.round(clipDelegate.rightTrimOffset / root.pixelsPerFrame)
                                    projectController.trimClipEdges(
                                        clipDelegate.clipId,
                                        clipDelegate.startFrame,
                                        clipDelegate.durationFrames + delta,
                                        false)
                                    clipDelegate.rightTrimOffset = 0
                                }
                            }
                        }
                    }
                }
            }

            Item {
                id: transitionLayer
                x: 0; y: 28
                width: timelineFlick.contentWidth
                height: tracksColumn.height
                z: 7
                Repeater {
                    model: projectController.transitionsModel
                    delegate: Rectangle {
                        required property string transitionId
                        required property int trackPosition
                        required property string kind
                        required property int durationFrames
                        required property int boundaryFrame
                        x: root.headerWidth + (boundaryFrame - durationFrames / 2) * root.pixelsPerFrame
                        y: trackPosition * root.trackPitch + 19
                        width: Math.max(16, durationFrames * root.pixelsPerFrame)
                        height: 32
                        rotation: 45
                        radius: 3
                        color: projectController.selectedTransitionId === transitionId
                               ? Theme.accentHover : Theme.accent
                        border.color: "white"
                        activeFocusOnTab: true
                        Accessible.name: qsTr("转场 %1，持续 %2 帧").arg(kind).arg(durationFrames)
                        Accessible.role: Accessible.Button
                        Keys.onReturnPressed: projectController.selectTransition(transitionId)
                        Keys.onSpacePressed: projectController.selectTransition(transitionId)
                        Text {
                            anchors.centerIn: parent
                            rotation: -45
                            text: "T"
                            color: "white"
                            font.pixelSize: 9
                        }
                        ToolTip.visible: transitionMouse.containsMouse
                        ToolTip.text: kind + " · " + durationFrames + qsTr(" 帧")
                        MouseArea {
                            id: transitionMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: projectController.selectTransition(transitionId)
                        }
                    }
                }
            }

            Item {
                id: markerLayer
                x: root.headerWidth
                y: 0
                width: timelineFlick.contentWidth - root.headerWidth
                height: timelineFlick.contentHeight
                z: 8
                Repeater {
                    model: projectController.timelineMarkersModel
                    delegate: Rectangle {
                        required property string markerId
                        required property int frame
                        required property string name
                        required property string markerColor
                        x: frame * root.pixelsPerFrame
                        width: 2
                        height: markerLayer.height
                        color: markerColor
                        activeFocusOnTab: true
                        Accessible.name: qsTr("标记 %1，位于第 %2 帧").arg(name).arg(frame)
                        Accessible.role: Accessible.Button
                        Keys.onReturnPressed: root.seekRequested(frame)
                        Keys.onSpacePressed: root.seekRequested(frame)
                        Rectangle {
                            x: -5; width: 12; height: 12; radius: 2
                            color: markerColor
                            rotation: 45
                        }
                        ToolTip.visible: markerMouse.containsMouse
                        ToolTip.text: name + " · " + frame
                        MouseArea {
                            id: markerMouse
                            x: -7; width: 16; height: 24
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: function(mouse) {
                                if (mouse.button === Qt.RightButton)
                                    projectController.removeTimelineMarker(markerId)
                                else
                                    root.seekRequested(frame)
                            }
                        }
                    }
                }
            }

            Rectangle {
                x: root.headerWidth + root.playheadFrame * root.pixelsPerFrame
                y: 0
                width: 2
                height: timelineFlick.contentHeight
                color: "white"
                z: 10
                Rectangle { width: 12; height: 10; x: -5; color: "white"; radius: 2 }
            }

            MouseArea {
                x: root.headerWidth
                y: 0
                width: timelineFlick.contentWidth - root.headerWidth
                height: 28
                z: 9
                onPressed: root.seekRequested(Math.max(0, Math.round(mouseX / root.pixelsPerFrame)))
                onPositionChanged: if (pressed) root.seekRequested(Math.max(0, Math.round(mouseX / root.pixelsPerFrame)))
            }
        }
    }
}
