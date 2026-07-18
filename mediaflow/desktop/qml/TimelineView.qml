import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    objectName: "timelineView"
    color: Theme.surface
    border.color: Theme.border
    property real pixelsPerFrame: 3.0
    property int playheadFrame: 0
    property int interactivePlayheadFrame: 0
    property bool playheadScrubbing: false
    property bool playheadSeekPending: false
    property string draggingClipId: ""
    property real draggingClipOffsetX: 0
    property int draggingClipTrackPosition: -1
    property string draggingClipTrackKind: ""
    property int trackControlsWidth: 244
    property bool trackControlsOpen: false
    property bool snapEnabled: true
    property int trackHeight: 72
    property int trackPitch: trackHeight + 1
    readonly property int fpsRounded: Math.max(1, Math.round(workspaceController.profileFpsNumerator / Math.max(1, workspaceController.profileFpsDenominator)))
    readonly property int rulerSeconds: Math.max(10, Math.ceil((workspaceController.timelineDurationFrames + fpsRounded * 2) / fpsRounded))
    readonly property int maxPlayheadFrame: Math.max(0, workspaceController.timelineDurationFrames - 1)
    readonly property int visiblePlayheadFrame: playheadScrubbing || playheadSeekPending
        ? interactivePlayheadFrame
        : Math.min(playheadFrame, maxPlayheadFrame)
    signal seekRequested(int frame)
    signal editProfileRequested

    function boundedPlayheadFrame(frame) {
        return Math.max(0, Math.min(maxPlayheadFrame, Math.round(frame)));
    }

    function seekToFrame(frame) {
        const boundedFrame = boundedPlayheadFrame(frame);
        interactivePlayheadFrame = boundedFrame;
        playheadSeekPending = playheadScrubbing || playheadFrame !== boundedFrame;
        seekRequested(boundedFrame);
    }

    function beginPlayheadScrub(frame) {
        playheadScrubbing = true;
        seekToFrame(frame);
    }

    function updatePlayheadScrub(frame) {
        const boundedFrame = boundedPlayheadFrame(frame);
        if (boundedFrame !== interactivePlayheadFrame)
            seekToFrame(boundedFrame);
    }

    function finishPlayheadScrub() {
        playheadScrubbing = false;
        playheadSeekPending = playheadFrame !== interactivePlayheadFrame;
    }

    function beginClipDrag(clipId, trackPosition, trackKind) {
        draggingClipId = clipId;
        draggingClipOffsetX = 0;
        draggingClipTrackPosition = trackPosition;
        draggingClipTrackKind = trackKind;
    }

    function updateClipDrag(clipId, startFrame, originalTrackPosition, allowedTrackKinds, deltaX, deltaY) {
        if (draggingClipId !== clipId)
            return;
        draggingClipOffsetX = Math.max(-startFrame * pixelsPerFrame, deltaX);
        const requestedPosition = Math.max(0, Math.min(
            timelineController.tracksModel.rowCount() - 1,
            Math.floor((originalTrackPosition * trackPitch + 12 + deltaY + 23) / trackPitch)
        ));
        const requestedTrack = timelineController.tracksModel.get(requestedPosition);
        if (allowedTrackKinds.indexOf(String(requestedTrack.kind)) >= 0 && !requestedTrack.locked) {
            draggingClipTrackPosition = requestedPosition;
            draggingClipTrackKind = String(requestedTrack.kind);
        }
    }

    function cancelClipDrag() {
        draggingClipId = "";
        draggingClipOffsetX = 0;
        draggingClipTrackPosition = -1;
        draggingClipTrackKind = "";
    }

    function finishClipDrag(clipId, startFrame, originalTrackPosition, snapEnabled) {
        if (draggingClipId !== clipId)
            return;
        const nextFrame = Math.max(0, startFrame + Math.round(draggingClipOffsetX / pixelsPerFrame));
        const nextTrackPosition = draggingClipTrackPosition;
        const moved = nextFrame !== startFrame || nextTrackPosition !== originalTrackPosition;
        const track = timelineController.tracksModel.get(nextTrackPosition);
        cancelClipDrag();
        if (moved)
            timelineController.moveClip(clipId, nextFrame, String(track.trackId), pixelsPerFrame, playheadFrame, snapEnabled);
    }

    onPlayheadFrameChanged: {
        if (!playheadScrubbing && playheadSeekPending && playheadFrame === interactivePlayheadFrame)
            playheadSeekPending = false;
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            color: Theme.surfaceRaised
            border.color: Theme.border
            Flickable {
                id: toolbarFlick
                objectName: "timelineToolbarScroll"
                anchors.fill: parent
                clip: true
                contentWidth: toolbarRow.implicitWidth + 20
                contentHeight: height
                flickableDirection: Flickable.HorizontalFlick
                boundsBehavior: Flickable.StopAtBounds
                interactive: contentWidth > width
                ScrollBar.horizontal: ScrollBar {
                    policy: toolbarFlick.contentWidth > toolbarFlick.width ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                }

                RowLayout {
                    id: toolbarRow
                    x: 10
                    height: toolbarFlick.height - 6
                    spacing: 7
                    SequenceToolbar {
                        Layout.preferredWidth: implicitWidth
                        Layout.preferredHeight: implicitHeight
                        onCreateShortRequested: workspaceController.createShortSequence("")
                        onEditProfileRequested: root.editProfileRequested()
                    }
                    AppButton {
                        text: "✂"
                        Accessible.name: qsTr("分割片段")
                        enabled: timelineController.selectedClipId.length > 0
                        onClicked: timelineController.splitClip(timelineController.selectedClipId, root.playheadFrame)
                    }
                    AppButton {
                        text: qsTr("复制")
                        enabled: timelineController.selectedClipId.length > 0
                        onClicked: timelineController.copyClip(timelineController.selectedClipId, root.pixelsPerFrame, root.playheadFrame)
                    }
                    AppButton {
                        text: "⌫"
                        Accessible.name: qsTr("删除所选片段")
                        enabled: timelineController.selectedClipIds.length > 0
                        onClicked: timelineController.deleteSelectedClips(false)
                    }
                    AppButton {
                        text: qsTr("波纹删除")
                        enabled: timelineController.selectedClipIds.length > 0
                        onClicked: timelineController.deleteSelectedClips(true)
                    }
                    AppButton {
                        text: qsTr("标记")
                        onClicked: timelineController.addTimelineMarker(root.playheadFrame)
                    }
                    AppButton {
                        objectName: "smartSequenceBoundsButton"
                        text: timelineController.sequenceBoundaryAnalysisRunning ? qsTr("正在分析入出点…") : qsTr("智能入出点")
                        enabled: workspaceController.timelineDurationFrames > 0 && !timelineController.sequenceBoundaryAnalysisRunning
                        onClicked: timelineController.analyzeSequenceBoundaries()
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("根据最终画面的首尾黑屏和启用字幕中的对白时间设置序列入出点")
                    }
                    AppButton {
                        text: "I"
                        enabled: workspaceController.timelineDurationFrames > 0
                        onClicked: timelineController.setSequenceInPoint(root.playheadFrame)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("将播放头设为序列入点")
                    }
                    AppButton {
                        text: "O"
                        enabled: workspaceController.timelineDurationFrames > 0
                        onClicked: timelineController.setSequenceOutPoint(root.playheadFrame)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("将播放头设为序列出点")
                    }
                    AppButton {
                        text: qsTr("清除 I/O")
                        visible: workspaceController.hasSequenceInOut
                        onClicked: timelineController.clearSequenceInOut()
                    }
                    AppButton {
                        text: timelineController.rangeInFrame < 0 ? qsTr("选区入点") : qsTr("选区入点 %1").arg(timelineController.rangeInFrame)
                        onClicked: timelineController.setRangeIn(root.playheadFrame)
                    }
                    AppButton {
                        text: qsTr("选区出点")
                        enabled: timelineController.rangeInFrame >= 0
                        onClicked: timelineController.commitTimelineRange(root.playheadFrame)
                    }
                    AppButton {
                        text: qsTr("选区转短视频")
                        enabled: timelineController.selectedRangeId.length > 0
                        onClicked: timelineController.createShortFromRange(timelineController.selectedRangeId)
                    }
                    AppButton {
                        text: "+V"
                        onClicked: timelineController.addTrack("video")
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("添加视频轨")
                    }
                    AppButton {
                        text: "+A"
                        onClicked: timelineController.addTrack("audio")
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("添加音频轨")
                    }
                    AppButton {
                        text: "+CC"
                        onClicked: timelineController.addTrack("subtitle")
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("添加字幕轨")
                    }
                    AppButton {
                        objectName: "timelineSnapButton"
                        text: checked ? qsTr("吸附 开") : qsTr("吸附 关")
                        checkable: true
                        checked: root.snapEnabled
                        onClicked: root.snapEnabled = checked
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("拖放或移动素材时自动吸附到片段边缘、字幕边缘、标记和播放头")
                    }
                    AppButton {
                        objectName: "trackControlsButton"
                        text: qsTr("轨道")
                        checkable: true
                        checked: root.trackControlsOpen
                        onClicked: root.trackControlsOpen = checked
                    }
                    Text {
                        text: qsTr("缩放")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Slider {
                        from: 0.5
                        to: 12
                        value: root.pixelsPerFrame
                        onMoved: root.pixelsPerFrame = value
                        Layout.preferredWidth: 130
                    }
                }
            }
        }

        Flickable {
            id: timelineFlick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: Math.max(width, root.rulerSeconds * root.fpsRounded * root.pixelsPerFrame)
            contentHeight: Math.max(height, tracksColumn.height + 32)
            boundsBehavior: Flickable.StopAtBounds

            function dropTrackAt(dropY) {
                const row = Math.floor(dropY / root.trackPitch);
                if (row < 0 || row >= timelineController.tracksModel.rowCount())
                    return {
                        "trackId": "",
                        "position": row,
                        "forceNew": true
                    };
                const track = timelineController.tracksModel.get(row);
                return {
                    "trackId": String(track.trackId),
                    "position": row,
                    "forceNew": false
                };
            }

            Row {
                x: 0
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
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }

            Item {
                id: sequenceInOutLayer
                objectName: "sequenceInOutLayer"
                x: 0
                y: 28
                width: timelineFlick.contentWidth
                height: tracksColumn.height
                visible: workspaceController.hasSequenceInOut
                z: 8

                Rectangle {
                    x: 0
                    width: Math.max(0, workspaceController.sequenceInFrame * root.pixelsPerFrame)
                    height: parent.height
                    color: "#7a000000"
                }
                Rectangle {
                    x: workspaceController.sequenceOutFrame * root.pixelsPerFrame
                    width: Math.max(0, parent.width - x)
                    height: parent.height
                    color: "#7a000000"
                }
                Item {
                    id: sequenceInHandle
                    objectName: "sequenceInHandle"
                    property real dragOffset: 0
                    x: workspaceController.sequenceInFrame * root.pixelsPerFrame - width / 2 + dragOffset
                    width: 12
                    height: parent.height
                    Accessible.name: qsTr("序列入点 %1").arg(workspaceController.sequenceInFrame)
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
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        onTranslationChanged: sequenceInHandle.dragOffset = Math.max(-workspaceController.sequenceInFrame * root.pixelsPerFrame, Math.min((workspaceController.sequenceOutFrame - workspaceController.sequenceInFrame - 1) * root.pixelsPerFrame, translation.x))
                        onActiveChanged: if (!active && sequenceInHandle.dragOffset !== 0) {
                            const frame = workspaceController.sequenceInFrame + Math.round(sequenceInHandle.dragOffset / root.pixelsPerFrame);
                            timelineController.setSequenceInOut(frame, workspaceController.sequenceOutFrame);
                            sequenceInHandle.dragOffset = 0;
                        }
                    }
                }
                Item {
                    id: sequenceOutHandle
                    objectName: "sequenceOutHandle"
                    property real dragOffset: 0
                    x: workspaceController.sequenceOutFrame * root.pixelsPerFrame - width / 2 + dragOffset
                    width: 12
                    height: parent.height
                    Accessible.name: qsTr("序列出点 %1").arg(workspaceController.sequenceOutFrame)
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
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        onTranslationChanged: sequenceOutHandle.dragOffset = Math.max((workspaceController.sequenceInFrame - workspaceController.sequenceOutFrame + 1) * root.pixelsPerFrame, Math.min((workspaceController.timelineDurationFrames - workspaceController.sequenceOutFrame) * root.pixelsPerFrame, translation.x))
                        onActiveChanged: if (!active && sequenceOutHandle.dragOffset !== 0) {
                            const frame = workspaceController.sequenceOutFrame + Math.round(sequenceOutHandle.dragOffset / root.pixelsPerFrame);
                            timelineController.setSequenceInOut(workspaceController.sequenceInFrame, frame);
                            sequenceOutHandle.dragOffset = 0;
                        }
                    }
                }
            }

            Item {
                id: rangeLayer
                x: 0
                y: 28
                width: timelineFlick.contentWidth
                height: tracksColumn.height
                z: 1
                Repeater {
                    model: timelineController.timelineRangesModel
                    delegate: Rectangle {
                        required property string rangeId
                        required property int startFrame
                        required property int endFrame
                        required property string name
                        required property string rangeColor
                        x: startFrame * root.pixelsPerFrame
                        width: Math.max(2, (endFrame - startFrame) * root.pixelsPerFrame)
                        height: rangeLayer.height
                        color: Qt.rgba(Qt.color(rangeColor).r, Qt.color(rangeColor).g, Qt.color(rangeColor).b, 0.12)
                        border.color: Qt.rgba(Qt.color(rangeColor).r, Qt.color(rangeColor).g, Qt.color(rangeColor).b, 0.52)
                        ToolTip.visible: rangeMouse.containsMouse
                        ToolTip.text: name + "  " + startFrame + "–" + endFrame
                        MouseArea {
                            id: rangeMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: function (mouse) {
                                if (mouse.button === Qt.RightButton)
                                    timelineController.removeTimelineRange(rangeId);
                                else {
                                    timelineController.selectTimelineRange(rangeId);
                                    root.seekToFrame(startFrame);
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
                    model: timelineController.tracksModel
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
                    }
                }
            }

            DropArea {
                id: timelineDropArea
                objectName: "timelineDropArea"
                x: 0
                y: 28
                width: timelineFlick.contentWidth
                height: Math.max(tracksColumn.height, timelineFlick.height - 28)
                z: 30
                property int targetFrame: 0
                property int targetTrackPosition: -1
                property bool targetCreatesTrack: false

                function updateTarget(drag) {
                    targetFrame = Math.max(0, Math.round(drag.x / root.pixelsPerFrame));
                    const target = timelineFlick.dropTrackAt(drag.y);
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
                    const target = timelineFlick.dropTrackAt(drop.y);
                    if (drop.hasUrls) {
                        timelineController.importFilesToTimeline(drop.urls, target.trackId, targetFrame, root.pixelsPerFrame, root.playheadFrame, root.snapEnabled, target.forceNew);
                        drop.acceptProposedAction();
                        return;
                    }
                    if (drop.source && drop.source.draggedAssetIds) {
                        timelineController.dropAssets(drop.source.draggedAssetIds, target.trackId, targetFrame, root.pixelsPerFrame, root.playheadFrame, root.snapEnabled, target.forceNew);
                        drop.acceptProposedAction();
                    }
                }
            }

            Rectangle {
                visible: timelineDropArea.containsDrag
                x: timelineDropArea.targetFrame * root.pixelsPerFrame
                y: timelineDropArea.targetCreatesTrack ? 28 + tracksColumn.height : 28 + Math.max(0, timelineDropArea.targetTrackPosition) * root.trackPitch
                width: Math.max(120, root.fpsRounded * root.pixelsPerFrame * 2)
                height: root.trackHeight
                radius: 5
                color: "#663274b8"
                border.width: 2
                border.color: Theme.accent
                z: 31
                Text {
                    anchors.centerIn: parent
                    text: timelineDropArea.targetCreatesTrack ? qsTr("释放以新建轨道") : root.snapEnabled ? qsTr("释放并自动吸附") : qsTr("释放到当前位置")
                    color: "white"
                    font.pixelSize: Theme.fontSizeCaption
                    font.weight: Font.DemiBold
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
                        required property var allowedTrackKinds
                        required property bool waveformReady
                        property real leftTrimOffset: 0
                        property real rightTrimOffset: 0
                        readonly property string displayedTrackKind: root.draggingClipId === clipId
                            ? root.draggingClipTrackKind
                            : trackKind
                        x: startFrame * root.pixelsPerFrame
                            + (root.draggingClipId === clipId ? root.draggingClipOffsetX : 0)
                            + leftTrimOffset
                        y: (root.draggingClipId === clipId ? root.draggingClipTrackPosition : trackPosition) * root.trackPitch + 12
                        width: Math.max(14, durationFrames * root.pixelsPerFrame - leftTrimOffset + rightTrimOffset)
                        height: 46
                        radius: 5
                        color: displayedTrackKind === "audio" ? Theme.audio : assetKind === "image" ? Theme.subtitle : Theme.video
                        border.width: timelineController.isClipSelected(clipId) ? 2 : 1
                        border.color: timelineController.isClipSelected(clipId) ? "white" : Qt.lighter(color, 1.25)
                        clip: true
                        activeFocusOnTab: true
                        Accessible.name: qsTr("片段 %1，起始帧 %2，持续 %3 帧").arg(assetName).arg(startFrame).arg(durationFrames)
                        Accessible.role: Accessible.ListItem
                        Keys.onReturnPressed: timelineController.selectClip(clipId)
                        Keys.onSpacePressed: timelineController.selectClip(clipId)

                        ClipWaveform {
                            assetId: clipDelegate.assetId
                            sourceIn: clipDelegate.sourceIn
                            durationFrames: clipDelegate.durationFrames
                            speed: clipDelegate.speed
                            waveformReady: clipDelegate.waveformReady && clipDelegate.displayedTrackKind === "audio"
                            viewport: timelineFlick
                            pixelsPerFrame: root.pixelsPerFrame
                            clipContentX: clipDelegate.x
                        }
                        Text {
                            anchors.fill: parent
                            anchors.margins: 7
                            text: displayedTrackKind === "audio" && assetKind === "video"
                                ? qsTr("音频 · ") + assetName
                                : assetName
                            color: "white"
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: Font.Medium
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                        MouseArea {
                            id: clipMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            property real pressContentX: 0
                            property real pressContentY: 0
                            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                            onPressed: function (mouse) {
                                const toggle = (mouse.modifiers & Qt.ControlModifier) !== 0;
                                if (toggle || !timelineController.isClipSelected(clipId))
                                    timelineController.selectClip(clipId, toggle);
                                const point = clipDelegate.mapToItem(clipLayer, mouse.x, mouse.y);
                                pressContentX = point.x;
                                pressContentY = point.y;
                                const sourceTrack = timelineController.tracksModel.get(trackPosition);
                                if (!sourceTrack.locked)
                                    root.beginClipDrag(clipId, trackPosition, trackKind);
                            }
                            onPositionChanged: function (mouse) {
                                if (!pressed || root.draggingClipId !== clipId)
                                    return;
                                const point = clipDelegate.mapToItem(clipLayer, mouse.x, mouse.y);
                                root.updateClipDrag(
                                    clipId,
                                    startFrame,
                                    trackPosition,
                                    allowedTrackKinds,
                                    point.x - pressContentX,
                                    point.y - pressContentY
                                );
                            }
                            onReleased: function (mouse) {
                                root.finishClipDrag(
                                    clipId,
                                    startFrame,
                                    trackPosition,
                                    root.snapEnabled && (mouse.modifiers & Qt.ShiftModifier) === 0
                                );
                            }
                            onCanceled: root.cancelClipDrag()
                        }
                        Rectangle {
                            width: 6
                            height: parent.height
                            anchors.left: parent.left
                            color: leftTrim.hovered ? "white" : "transparent"
                            z: 8
                            HoverHandler {
                                id: leftTrim
                            }
                            DragHandler {
                                target: null
                                xAxis.enabled: true
                                yAxis.enabled: false
                                onTranslationChanged: clipDelegate.leftTrimOffset = Math.max(-clipDelegate.startFrame * root.pixelsPerFrame, Math.min(clipDelegate.durationFrames * root.pixelsPerFrame - 8, translation.x))
                                onActiveChanged: if (!active && clipDelegate.leftTrimOffset !== 0) {
                                    const delta = Math.round(clipDelegate.leftTrimOffset / root.pixelsPerFrame);
                                    timelineController.trimClipEdges(clipDelegate.clipId, clipDelegate.startFrame + delta, clipDelegate.durationFrames - delta, true);
                                    clipDelegate.leftTrimOffset = 0;
                                }
                            }
                        }
                        Rectangle {
                            width: 6
                            height: parent.height
                            anchors.right: parent.right
                            color: rightTrim.hovered ? "white" : "transparent"
                            z: 8
                            HoverHandler {
                                id: rightTrim
                            }
                            DragHandler {
                                target: null
                                xAxis.enabled: true
                                yAxis.enabled: false
                                onTranslationChanged: clipDelegate.rightTrimOffset = Math.max(-(clipDelegate.durationFrames * root.pixelsPerFrame - 8), translation.x)
                                onActiveChanged: if (!active && clipDelegate.rightTrimOffset !== 0) {
                                    const delta = Math.round(clipDelegate.rightTrimOffset / root.pixelsPerFrame);
                                    timelineController.trimClipEdges(clipDelegate.clipId, clipDelegate.startFrame, clipDelegate.durationFrames + delta, false);
                                    clipDelegate.rightTrimOffset = 0;
                                }
                            }
                        }
                    }
                }
            }

            Item {
                id: embeddedAudioLayer
                objectName: "embeddedAudioLayer"
                x: 0
                y: 28
                width: timelineFlick.contentWidth
                height: tracksColumn.height
                z: 3

                Repeater {
                    model: timelineController.clipsModel
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
                        required property bool hasAudio
                        required property int audioTrackPosition
                        required property bool waveformReady

                        objectName: "embeddedAudioClip"
                        readonly property string displayedTrackKind: root.draggingClipId === clipId
                            ? root.draggingClipTrackKind
                            : trackKind
                        visible: displayedTrackKind === "video" && assetKind === "video" && hasAudio && audioTrackPosition >= 0
                        x: startFrame * root.pixelsPerFrame
                            + (root.draggingClipId === clipId ? root.draggingClipOffsetX : 0)
                        y: audioTrackPosition * root.trackPitch + 10
                        width: Math.max(14, durationFrames * root.pixelsPerFrame)
                        height: 50
                        radius: 5
                        color: Theme.audio
                        border.width: timelineController.isClipSelected(clipId) ? 2 : 1
                        border.color: timelineController.isClipSelected(clipId) ? "white" : Qt.lighter(color, 1.25)
                        clip: true
                        activeFocusOnTab: true
                        Accessible.name: qsTr("%1 的音频，起始帧 %2，持续 %3 帧").arg(assetName).arg(startFrame).arg(durationFrames)
                        Accessible.role: Accessible.ListItem
                        Keys.onReturnPressed: timelineController.selectClip(clipId)
                        Keys.onSpacePressed: timelineController.selectClip(clipId)

                        ClipWaveform {
                            assetId: embeddedAudioDelegate.assetId
                            sourceIn: embeddedAudioDelegate.sourceIn
                            durationFrames: embeddedAudioDelegate.durationFrames
                            speed: embeddedAudioDelegate.speed
                            waveformReady: embeddedAudioDelegate.waveformReady
                            viewport: timelineFlick
                            pixelsPerFrame: root.pixelsPerFrame
                            clipContentX: embeddedAudioDelegate.x
                        }
                        Text {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 6
                            text: qsTr("音频 · ") + assetName
                            color: "white"
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: Font.Medium
                            elide: Text.ElideRight
                        }
                        MouseArea {
                            id: embeddedAudioMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            property real pressContentX: 0
                            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                            onPressed: function (mouse) {
                                const toggle = (mouse.modifiers & Qt.ControlModifier) !== 0;
                                if (toggle || !timelineController.isClipSelected(clipId))
                                    timelineController.selectClip(clipId, toggle);
                                const point = embeddedAudioDelegate.mapToItem(
                                    embeddedAudioLayer,
                                    mouse.x,
                                    mouse.y
                                );
                                pressContentX = point.x;
                                const sourceTrack = timelineController.tracksModel.get(trackPosition);
                                if (!sourceTrack.locked)
                                    root.beginClipDrag(clipId, trackPosition, trackKind);
                            }
                            onPositionChanged: function (mouse) {
                                if (!pressed || root.draggingClipId !== clipId)
                                    return;
                                const point = embeddedAudioDelegate.mapToItem(
                                    embeddedAudioLayer,
                                    mouse.x,
                                    mouse.y
                                );
                                root.updateClipDrag(
                                    clipId,
                                    startFrame,
                                    trackPosition,
                                    [trackKind],
                                    point.x - pressContentX,
                                    0
                                );
                            }
                            onReleased: function (mouse) {
                                root.finishClipDrag(
                                    clipId,
                                    startFrame,
                                    trackPosition,
                                    root.snapEnabled && (mouse.modifiers & Qt.ShiftModifier) === 0
                                );
                            }
                            onCanceled: root.cancelClipDrag()
                        }
                    }
                }
            }

            Item {
                id: subtitleOverlayLayer
                objectName: "subtitleOverlayLayer"
                x: 0
                y: 28
                width: timelineFlick.contentWidth
                height: tracksColumn.height
                z: 6

                Repeater {
                    model: subtitleController.subtitlePlacementsModel
                    delegate: Rectangle {
                        id: subtitleOverlay
                        required property string placementId
                        required property int audioTrackPosition
                        required property int startFrame
                        required property int endFrame
                        required property string text

                        objectName: "subtitleWaveformOverlay"
                        visible: audioTrackPosition >= 0
                        x: startFrame * root.pixelsPerFrame
                        y: audioTrackPosition * root.trackPitch + 25
                        width: Math.max(12, (endFrame - startFrame) * root.pixelsPerFrame)
                        height: 28
                        radius: 4
                        color: "#d91a2029"
                        border.width: subtitleController.selectedSubtitlePlacementId === placementId ? 2 : 1
                        border.color: subtitleController.selectedSubtitlePlacementId === placementId ? "white" : Theme.subtitle
                        clip: true
                        activeFocusOnTab: true
                        Accessible.name: qsTr("字幕：%1").arg(text)
                        Accessible.role: Accessible.ListItem
                        Keys.onReturnPressed: subtitleController.selectSubtitlePlacement(placementId)
                        Keys.onSpacePressed: subtitleController.selectSubtitlePlacement(placementId)

                        Text {
                            anchors.fill: parent
                            anchors.leftMargin: 6
                            anchors.rightMargin: 6
                            text: "CC · " + subtitleOverlay.text
                            color: "white"
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: subtitleController.selectSubtitlePlacement(placementId)
                            ToolTip.visible: containsMouse
                            ToolTip.text: subtitleOverlay.text
                        }
                    }
                }
            }

            Item {
                id: transitionLayer
                x: 0
                y: 28
                width: timelineFlick.contentWidth
                height: tracksColumn.height
                z: 7
                Repeater {
                    model: timelineController.transitionsModel
                    delegate: Rectangle {
                        required property string transitionId
                        required property int trackPosition
                        required property string kind
                        required property int durationFrames
                        required property int boundaryFrame
                        x: (boundaryFrame - durationFrames / 2) * root.pixelsPerFrame
                        y: trackPosition * root.trackPitch + 19
                        width: Math.max(16, durationFrames * root.pixelsPerFrame)
                        height: 32
                        rotation: 45
                        radius: 3
                        color: timelineController.selectedTransitionId === transitionId ? Theme.accentHover : Theme.accent
                        border.color: "white"
                        activeFocusOnTab: true
                        Accessible.name: qsTr("转场 %1，持续 %2 帧").arg(kind).arg(durationFrames)
                        Accessible.role: Accessible.Button
                        Keys.onReturnPressed: timelineController.selectTransition(transitionId)
                        Keys.onSpacePressed: timelineController.selectTransition(transitionId)
                        Text {
                            anchors.centerIn: parent
                            rotation: -45
                            text: "T"
                            color: "white"
                            font.pixelSize: Theme.fontSizeCaption
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

            Item {
                id: markerLayer
                x: 0
                y: 0
                width: timelineFlick.contentWidth
                height: timelineFlick.contentHeight
                z: 8
                Repeater {
                    model: timelineController.timelineMarkersModel
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
                        Keys.onReturnPressed: root.seekToFrame(frame)
                        Keys.onSpacePressed: root.seekToFrame(frame)
                        Rectangle {
                            x: -5
                            width: 12
                            height: 12
                            radius: 2
                            color: markerColor
                            rotation: 45
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
                                if (mouse.button === Qt.RightButton)
                                    timelineController.removeTimelineMarker(markerId);
                                else
                                    root.seekToFrame(frame);
                            }
                        }
                    }
                }
            }

            Rectangle {
                objectName: "timelinePlayhead"
                x: root.visiblePlayheadFrame * root.pixelsPerFrame
                y: 0
                width: 2
                height: timelineFlick.contentHeight
                color: "white"
                z: 10
                Rectangle {
                    width: 12
                    height: 10
                    x: -5
                    color: "white"
                    radius: 2
                }
            }

            MouseArea {
                objectName: "timelineRulerSeekArea"
                x: 0
                y: 0
                width: timelineFlick.contentWidth
                height: 28
                z: 9
                onPressed: root.beginPlayheadScrub(mouseX / root.pixelsPerFrame)
                onPositionChanged: if (pressed)
                    root.updatePlayheadScrub(mouseX / root.pixelsPerFrame)
                onReleased: root.finishPlayheadScrub()
                onCanceled: root.finishPlayheadScrub()
            }
        }
    }

    Item {
        id: trackControlsPanel
        objectName: "trackControlsPanel"
        anchors.top: parent.top
        anchors.topMargin: 76
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: Math.min(root.trackControlsWidth, root.width)
        visible: root.trackControlsOpen && timelineController.tracksModel.rowCount() > 0
        clip: true
        z: 50

        Column {
            y: -timelineFlick.contentY
            width: parent.width
            spacing: 1

            Repeater {
                model: timelineController.tracksModel
                delegate: Rectangle {
                    required property string trackId
                    required property string displayName
                    required property string kind
                    required property int position
                    required property bool locked
                    required property bool muted
                    required property bool solo
                    required property string audioBusId
                    required property var model

                    objectName: "trackControlsOverlay"
                    width: trackControlsPanel.width
                    height: root.trackHeight
                    color: Theme.surfaceRaised
                    border.color: Theme.border
                    opacity: 0.96

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Text {
                                text: kind === "video" ? "▣" : kind === "audio" ? "♫" : "CC"
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                Layout.fillWidth: true
                                text: displayName
                                color: model.enabled ? Theme.text : Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                elide: Text.ElideRight
                            }
                            Text {
                                text: position + 1
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            Button {
                                text: model.enabled ? "●" : "○"
                                implicitWidth: 28
                                implicitHeight: 24
                                ToolTip.visible: hovered
                                ToolTip.text: model.enabled ? qsTr("禁用轨道") : qsTr("启用轨道")
                                onClicked: timelineController.updateTrack(trackId, !model.enabled, locked, muted, solo, audioBusId)
                            }
                            Button {
                                text: locked ? "🔒" : "◇"
                                implicitWidth: 28
                                implicitHeight: 24
                                ToolTip.visible: hovered
                                ToolTip.text: locked ? qsTr("解锁轨道") : qsTr("锁定轨道")
                                onClicked: timelineController.updateTrack(trackId, model.enabled, !locked, muted, solo, audioBusId)
                            }
                            Button {
                                text: "M"
                                checkable: true
                                checked: muted
                                implicitWidth: 28
                                implicitHeight: 24
                                ToolTip.visible: hovered
                                ToolTip.text: muted ? qsTr("取消静音") : qsTr("静音")
                                onClicked: timelineController.updateTrack(trackId, model.enabled, locked, !muted, solo, audioBusId)
                            }
                            Button {
                                text: "S"
                                checkable: true
                                checked: solo
                                implicitWidth: 28
                                implicitHeight: 24
                                ToolTip.visible: hovered
                                ToolTip.text: solo ? qsTr("取消独奏") : qsTr("独奏")
                                onClicked: timelineController.updateTrack(trackId, model.enabled, locked, muted, !solo, audioBusId)
                            }
                            Item {
                                Layout.fillWidth: true
                            }
                            Button {
                                text: "↑"
                                enabled: position > 0
                                implicitWidth: 28
                                implicitHeight: 24
                                ToolTip.visible: hovered
                                ToolTip.text: qsTr("轨道上移")
                                onClicked: timelineController.moveTrack(trackId, position - 1)
                            }
                            Button {
                                text: "↓"
                                enabled: position + 1 < timelineController.tracksModel.rowCount()
                                implicitWidth: 28
                                implicitHeight: 24
                                ToolTip.visible: hovered
                                ToolTip.text: qsTr("轨道下移")
                                onClicked: timelineController.moveTrack(trackId, position + 1)
                            }
                        }
                    }
                }
            }
        }
    }
}
