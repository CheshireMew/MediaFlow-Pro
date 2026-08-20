import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Flickable {
    id: canvas
    required property var view
    function repaintRuler() {
        timeRuler.requestPaint();
        timelineGridCanvas.requestPaint();
    }
    objectName: "timelineScroll"
    Layout.fillWidth: true
    Layout.fillHeight: true
    Layout.leftMargin: view.trackControlsWidth
    clip: true
    contentWidth: Math.max(width, view.contentFrameCount * view.pixelsPerFrame)
    contentHeight: Math.max(height, tracksColumn.height + 32)
    boundsBehavior: Flickable.StopAtBounds

    function scheduleFilmstrip() {
        filmstripRequest.restart();
    }

    onContentXChanged: scheduleFilmstrip()
    onWidthChanged: scheduleFilmstrip()
    Component.onCompleted: scheduleFilmstrip()

    Connections {
        target: canvas.view
        function onPixelsPerFrameChanged() { canvas.scheduleFilmstrip(); }
    }

    Connections {
        target: mediaflow.timelineViewController
        function onProjectStateChanged() { canvas.scheduleFilmstrip(); }
    }

    Timer {
        id: filmstripRequest
        interval: 120
        repeat: false
        onTriggered: mediaflow.timelineViewController.requestFilmstrip(
            canvas.contentX / Math.max(0.000001, canvas.view.pixelsPerFrame),
            (canvas.contentX + canvas.width) / Math.max(0.000001, canvas.view.pixelsPerFrame),
            canvas.view.pixelsPerFrame,
            46)
    }

    function dropTrackAt(dropY) {
        const trackCount = mediaflow.timelineViewController.tracksModel.rowCount();
        if (trackCount === 0)
            return {
                "trackId": "",
                "position": 0,
                "forceNew": true
            };
        const row = Math.floor(dropY / view.trackPitch);
        if (row < 0)
            return {
                "trackId": "",
                "position": 0,
                "forceNew": true
            };
        if (row >= trackCount)
            return {
                "trackId": "",
                "position": trackCount,
                "forceNew": true
            };
        const rowY = dropY - row * view.trackPitch;
        const insertionMargin = 10;
        if (rowY <= insertionMargin)
            return {
                "trackId": "",
                "position": row,
                "forceNew": true
            };
        if (rowY >= view.trackHeight - insertionMargin)
            return {
                "trackId": "",
                "position": row + 1,
                "forceNew": true
            };
        const track = mediaflow.timelineViewController.tracksModel.get(row);
        return {
            "trackId": String(track.trackId),
            "position": row,
            "forceNew": false
        };
    }

    Canvas {
        id: timeRuler
        objectName: "timelineRuler"
        property real scrollX: canvas.contentX
        property real pixelsScale: view.pixelsPerFrame
        property int majorStepFrames: view.rulerMajorStepFrames()
        property color backgroundColor: Theme.timelineRuler
        property color dividerColor: Theme.timelineGridStrong
        property color minorTickColor: Theme.timelineGrid
        property color majorTickColor: Theme.timelineGridStrong
        property color labelColor: Theme.timelineLabel
        x: canvas.contentX
        y: 0
        width: canvas.width
        height: 28
        antialiasing: false
        onScrollXChanged: requestPaint()
        onPixelsScaleChanged: requestPaint()
        onMajorStepFramesChanged: requestPaint()
        onWidthChanged: requestPaint()
        onBackgroundColorChanged: requestPaint()
        onDividerColorChanged: requestPaint()
        onMinorTickColorChanged: requestPaint()
        onMajorTickColorChanged: requestPaint()
        onLabelColorChanged: requestPaint()
        onPaint: {
            const context = getContext("2d");
            context.clearRect(0, 0, width, height);
            context.fillStyle = backgroundColor;
            context.fillRect(0, 0, width, height);
            context.strokeStyle = dividerColor;
            context.lineWidth = 1;
            context.beginPath();
            context.moveTo(0, height - 0.5);
            context.lineTo(width, height - 0.5);
            context.stroke();

            const major = Math.max(1, majorStepFrames);
            const minor = major % 5 === 0 ? major / 5 : major % 4 === 0 ? major / 4 : major % 2 === 0 ? major / 2 : major;
            const firstVisibleFrame = Math.max(0, Math.floor(scrollX / pixelsScale));
            const lastVisibleFrame = Math.ceil((scrollX + width) / pixelsScale);
            const firstTick = Math.floor(firstVisibleFrame / minor) * minor;
            context.font = Theme.canvasMonospaceFont(Theme.fontSizeCaption);
            context.textBaseline = "top";
            for (let frame = firstTick; frame <= lastVisibleFrame + minor; frame += minor) {
                const rulerX = Math.round(frame * pixelsScale - scrollX) + 0.5;
                const majorTick = frame % major === 0;
                context.strokeStyle = majorTick ? majorTickColor : minorTickColor;
                context.beginPath();
                context.moveTo(rulerX, majorTick ? 15 : 21);
                context.lineTo(rulerX, 28);
                context.stroke();
                if (majorTick) {
                    context.fillStyle = labelColor;
                    context.fillText(view.formatTimecode(frame), rulerX + 4, 2);
                }
            }
        }
    }

    Canvas {
        id: timelineGridCanvas
        x: canvas.contentX
        y: 28
        width: canvas.width
        height: Math.max(0, tracksColumn.height)
        z: 0.5
        antialiasing: false
        property real scrollX: canvas.contentX
        property real pixelsScale: view.pixelsPerFrame
        property int majorStepFrames: view.rulerMajorStepFrames()
        property color minorGridColor: Theme.timelineGrid
        property color majorGridColor: Theme.timelineGridStrong
        onScrollXChanged: requestPaint()
        onPixelsScaleChanged: requestPaint()
        onMajorStepFramesChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onMinorGridColorChanged: requestPaint()
        onMajorGridColorChanged: requestPaint()
        onPaint: {
            const context = getContext("2d");
            context.clearRect(0, 0, width, height);
            if (height <= 0)
                return;
            const major = Math.max(1, majorStepFrames);
            const minor = major % 5 === 0
                ? major / 5
                : major % 4 === 0
                    ? major / 4
                    : major % 2 === 0 ? major / 2 : major;
            const firstVisibleFrame = Math.max(0, Math.floor(scrollX / pixelsScale));
            const lastVisibleFrame = Math.ceil((scrollX + width) / pixelsScale);
            const firstTick = Math.floor(firstVisibleFrame / minor) * minor;
            for (let frame = firstTick; frame <= lastVisibleFrame + minor; frame += minor) {
                const gridX = Math.round(frame * pixelsScale - scrollX) + 0.5;
                context.strokeStyle = frame % major === 0
                    ? majorGridColor : minorGridColor;
                context.lineWidth = 1;
                context.beginPath();
                context.moveTo(gridX, 0);
                context.lineTo(gridX, height);
                context.stroke();
            }
        }
    }

    WheelHandler {
        acceptedModifiers: Qt.ControlModifier
        onWheel: function (event) {
            view.zoomAtViewportPoint(event.angleDelta.y > 0 ? 1.16 : 1 / 1.16, event.x);
            event.accepted = true;
        }
    }

    Item {
        id: sequenceInOutLayer
        objectName: "sequenceInOutLayer"
        x: 0
        y: 28
        width: canvas.contentWidth
        height: tracksColumn.height
        visible: mediaflow.workspaceViewController.hasSequenceInOut
        z: 8

        Rectangle {
            x: 0
            width: Math.max(0, mediaflow.workspaceViewController.sequenceInFrame * view.pixelsPerFrame)
            height: parent.height
            color: Theme.timelineBackground
            opacity: 0.78
        }
        Rectangle {
            x: mediaflow.workspaceViewController.sequenceOutFrame * view.pixelsPerFrame
            width: Math.max(0, parent.width - x)
            height: parent.height
            color: Theme.timelineBackground
            opacity: 0.78
        }
        Item {
            id: sequenceInHandle
            objectName: "sequenceInHandle"
            property real dragOffset: 0
            x: mediaflow.workspaceViewController.sequenceInFrame * view.pixelsPerFrame - width / 2 + dragOffset
            width: 12
            height: parent.height
            Accessible.name: qsTr("序列入点 %1").arg(mediaflow.workspaceViewController.sequenceInFrame)
            Accessible.role: Accessible.Slider
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 3
                height: parent.height
                color: Theme.accent
            }
            Text {
                anchors.left: parent.horizontalCenter
                anchors.top: parent.top
                anchors.leftMargin: 3
                text: "I"
                color: Theme.accent
                font.weight: Font.Bold
                font.pixelSize: Theme.fontSizeCaption
            }
            DragHandler {
                enabled: view.canEdit
                target: null
                xAxis.enabled: true
                yAxis.enabled: false
                onTranslationChanged: sequenceInHandle.dragOffset = Math.max(-mediaflow.workspaceViewController.sequenceInFrame * view.pixelsPerFrame, Math.min((mediaflow.workspaceViewController.sequenceOutFrame - mediaflow.workspaceViewController.sequenceInFrame - 1) * view.pixelsPerFrame, translation.x))
                onActiveChanged: if (!active && sequenceInHandle.dragOffset !== 0) {
                    const frame = mediaflow.workspaceViewController.sequenceInFrame + Math.round(sequenceInHandle.dragOffset / view.pixelsPerFrame);
                    mediaflow.timelineStructureController.setSequenceInOut(frame, mediaflow.workspaceViewController.sequenceOutFrame);
                    sequenceInHandle.dragOffset = 0;
                }
            }
        }
        Item {
            id: sequenceOutHandle
            objectName: "sequenceOutHandle"
            property real dragOffset: 0
            x: mediaflow.workspaceViewController.sequenceOutFrame * view.pixelsPerFrame - width / 2 + dragOffset
            width: 12
            height: parent.height
            Accessible.name: qsTr("序列出点 %1").arg(mediaflow.workspaceViewController.sequenceOutFrame)
            Accessible.role: Accessible.Slider
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 3
                height: parent.height
                color: Theme.warning
            }
            Text {
                anchors.right: parent.horizontalCenter
                anchors.top: parent.top
                anchors.rightMargin: 3
                text: "O"
                color: Theme.warning
                font.weight: Font.Bold
                font.pixelSize: Theme.fontSizeCaption
            }
            DragHandler {
                enabled: view.canEdit
                target: null
                xAxis.enabled: true
                yAxis.enabled: false
                onTranslationChanged: sequenceOutHandle.dragOffset = Math.max((mediaflow.workspaceViewController.sequenceInFrame - mediaflow.workspaceViewController.sequenceOutFrame + 1) * view.pixelsPerFrame, Math.min((mediaflow.workspaceViewController.timelineDurationFrames - mediaflow.workspaceViewController.sequenceOutFrame) * view.pixelsPerFrame, translation.x))
                onActiveChanged: if (!active && sequenceOutHandle.dragOffset !== 0) {
                    const frame = mediaflow.workspaceViewController.sequenceOutFrame + Math.round(sequenceOutHandle.dragOffset / view.pixelsPerFrame);
                    mediaflow.timelineStructureController.setSequenceInOut(mediaflow.workspaceViewController.sequenceInFrame, frame);
                    sequenceOutHandle.dragOffset = 0;
                }
            }
        }
    }

    Item {
        id: rangeLayer
        x: 0
        y: 28
        width: canvas.contentWidth
        height: tracksColumn.height
        z: 1
        Repeater {
            model: mediaflow.timelineViewController.timelineRangesModel
            delegate: Rectangle {
                required property string rangeId
                required property int startFrame
                required property int endFrame
                required property string name
                required property string rangeColor
                x: startFrame * view.pixelsPerFrame
                width: Math.max(2, (endFrame - startFrame) * view.pixelsPerFrame)
                height: rangeLayer.height
                color: Qt.rgba(
                    Qt.color(rangeColor).r,
                    Qt.color(rangeColor).g,
                    Qt.color(rangeColor).b,
                    Theme.timelineRangeFillOpacity)
                border.color: Qt.rgba(
                    Qt.color(rangeColor).r,
                    Qt.color(rangeColor).g,
                    Qt.color(rangeColor).b,
                    Theme.timelineRangeBorderOpacity)
                ToolTip.visible: rangeMouse.containsMouse
                ToolTip.text: name + "  " + startFrame + "–" + endFrame
                MouseArea {
                    id: rangeMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    onClicked: function (mouse) {
                        if (mouse.button === Qt.RightButton && view.canEdit)
                            mediaflow.timelineStructureController.removeTimelineRange(rangeId);
                        else {
                            mediaflow.timelineViewController.selectTimelineRange(rangeId);
                            view.seekToFrame(startFrame);
                        }
                    }
                }
            }
        }
    }

    Column {
        id: tracksColumn
        y: 28
        width: canvas.contentWidth
        spacing: 1
        Repeater {
            id: tracksRepeater
            model: mediaflow.timelineViewController.tracksModel
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
                height: view.trackHeight
                color: index % 2 === 0 ? Theme.timelineTrackA : Theme.timelineTrackB
            }
        }
    }

    DropArea {
        id: timelineDropArea
        objectName: "timelineDropArea"
        x: 0
        y: 28
        width: canvas.contentWidth
        height: Math.max(tracksColumn.height, canvas.height - 28)
        z: 30
        enabled: view.canEdit
        property int targetFrame: 0
        property int targetTrackPosition: -1
        property bool targetCreatesTrack: false

        function updateTarget(drag) {
            targetFrame = Math.max(0, Math.round(drag.x / view.pixelsPerFrame));
            const target = canvas.dropTrackAt(drag.y);
            targetTrackPosition = target.position;
            targetCreatesTrack = target.forceNew;
        }

        onEntered: function (drag) {
            updateTarget(drag);
        }
        onPositionChanged: function (drag) {
            updateTarget(drag);
        }
        onDropped: function (drop) {
            updateTarget(drop);
            const target = canvas.dropTrackAt(drop.y);
            if (drop.hasUrls) {
                mediaflow.timelineClipController.importFilesToTimeline(drop.urls, target.trackId, target.position, targetFrame, view.pixelsPerFrame, view.playheadFrame, view.snapEnabled, target.forceNew);
                drop.acceptProposedAction();
                return;
            }
            if (drop.source && drop.source.draggedAssetIds) {
                mediaflow.timelineClipController.dropAssets(drop.source.draggedAssetIds, target.trackId, target.position, targetFrame, view.pixelsPerFrame, view.playheadFrame, view.snapEnabled, target.forceNew);
                drop.acceptProposedAction();
            }
        }
    }

    Rectangle {
        visible: timelineDropArea.containsDrag
        x: timelineDropArea.targetFrame * view.pixelsPerFrame
        y: 28 + Math.max(0, timelineDropArea.targetTrackPosition) * view.trackPitch
        width: Math.max(120, view.fpsRounded * view.pixelsPerFrame * 2)
        height: view.trackHeight
        radius: 5
        color: Theme.accentSoft
        opacity: 0.92
        border.width: 2
        border.color: Theme.accent
        z: 31
        Text {
            anchors.centerIn: parent
            text: timelineDropArea.targetCreatesTrack ? qsTr("释放以新建轨道") : view.snapEnabled ? qsTr("释放并自动吸附") : qsTr("释放到当前位置")
            color: Theme.textStrong
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }
    }

    TimelineClipLayer {
        view: canvas.view
        timelineCanvas: canvas
        tracksHeight: tracksColumn.height
    }

    TimelineAudioClipLayer {
        view: canvas.view
        timelineCanvas: canvas
        tracksHeight: tracksColumn.height
    }

    TimelineCompoundLayer {
        view: canvas.view
        timelineCanvas: canvas
        tracksHeight: tracksColumn.height
    }

    TimelineSubtitleLayer {
        view: canvas.view
        timelineCanvas: canvas
        tracksHeight: tracksColumn.height
    }

    TimelineTransitionLayer {
        view: canvas.view
        timelineCanvas: canvas
        tracksHeight: tracksColumn.height
    }

    TimelineMarkerLayer {
        view: canvas.view
        timelineCanvas: canvas
    }

    Rectangle {
        objectName: "timelinePlayhead"
        x: view.visiblePlayheadFrame * view.pixelsPerFrame
        y: 0
        width: 2
        height: canvas.contentHeight
        color: Theme.cut
        z: 10
        Rectangle {
            width: 12
            height: 10
            x: -5
            color: Theme.cut
            border.color: Theme.cutHover
            border.width: 1
            radius: 2
        }
    }

    MouseArea {
        objectName: "timelineRulerSeekArea"
        x: 0
        y: 0
        width: canvas.contentWidth
        height: 28
        z: 9
        onPressed: view.beginPlayheadScrub(mouseX / view.pixelsPerFrame)
        onPositionChanged: if (pressed)
            view.updatePlayheadScrub(mouseX / view.pixelsPerFrame)
        onReleased: view.finishPlayheadScrub()
        onCanceled: view.finishPlayheadScrub()
    }
}
