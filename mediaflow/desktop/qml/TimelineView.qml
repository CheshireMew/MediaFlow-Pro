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
    property string zoomSequenceId: ""
    property bool zoomAwaitingFirstContent: true
    property int playheadFrame: 0
    property int interactivePlayheadFrame: 0
    property bool playheadScrubbing: false
    property bool playheadSeekPending: false
    property string draggingClipId: ""
    property string contextClipId: ""
    property real draggingClipOffsetX: 0
    property int draggingClipTrackPosition: -1
    property string draggingClipTrackKind: ""
    property int trackControlsWidth: 196
    property bool snapEnabled: true
    property int trackHeight: 72
    property int trackPitch: trackHeight + 1
    readonly property real fpsExact: Math.max(1, workspaceController.profileFpsNumerator)
        / Math.max(1, workspaceController.profileFpsDenominator)
    readonly property int fpsRounded: Math.max(1, Math.round(workspaceController.profileFpsNumerator / Math.max(1, workspaceController.profileFpsDenominator)))
    readonly property int contentFrameCount: Math.max(
        Math.ceil(fpsExact * 10),
        workspaceController.timelineDurationFrames + Math.ceil(fpsExact * 2))
    readonly property real minimumPixelsPerFrame: Math.max(
        0.000001,
        Math.min(0.5, timelineFlick.width / Math.max(1, contentFrameCount)))
    readonly property int maxPlayheadFrame: Math.max(0, workspaceController.timelineDurationFrames - 1)
    readonly property Item focusedItem: root.Window.window
        ? root.Window.window.activeFocusItem : null
    readonly property bool textInputActive: focusedItem instanceof TextInput
        || focusedItem instanceof TextEdit
    readonly property int visiblePlayheadFrame: playheadScrubbing || playheadSeekPending
        ? interactivePlayheadFrame
        : Math.min(playheadFrame, maxPlayheadFrame)
    signal seekRequested(int frame)
    signal editProfileRequested

    function boundedPlayheadFrame(frame) {
        return Math.max(0, Math.min(maxPlayheadFrame, Math.round(frame)));
    }

    function formatTimecode(frame) {
        const bounded = Math.max(0, Math.round(frame));
        const frames = bounded % fpsRounded;
        const totalSeconds = Math.floor(bounded / fpsRounded);
        const seconds = totalSeconds % 60;
        const totalMinutes = Math.floor(totalSeconds / 60);
        const minutes = totalMinutes % 60;
        const hours = Math.floor(totalMinutes / 60);
        function pad(value) {
            return value < 10 ? "0" + value : String(value);
        }
        return pad(hours) + ":" + pad(minutes) + ":" + pad(seconds) + ":" + pad(frames);
    }

    function rulerMajorStepFrames() {
        const targetFrames = 110 / Math.max(0.000001, pixelsPerFrame);
        const candidates = [
            1,
            2,
            5,
            10,
            Math.round(fpsExact / 2),
            Math.round(fpsExact),
            Math.round(fpsExact * 2),
            Math.round(fpsExact * 5),
            Math.round(fpsExact * 10),
            Math.round(fpsExact * 15),
            Math.round(fpsExact * 30),
            Math.round(fpsExact * 60),
            Math.round(fpsExact * 120),
            Math.round(fpsExact * 300),
            Math.round(fpsExact * 600),
            Math.round(fpsExact * 900),
            Math.round(fpsExact * 1800),
            Math.round(fpsExact * 3600)
        ];
        for (let index = 0; index < candidates.length; ++index) {
            if (candidates[index] >= targetFrames)
                return Math.max(1, candidates[index]);
        }
        const targetHours = targetFrames / (fpsExact * 3600);
        const exponent = Math.pow(10, Math.floor(Math.log(targetHours) / Math.LN10));
        const normalized = targetHours / exponent;
        const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
        return Math.max(1, Math.round(factor * exponent * fpsExact * 3600));
    }

    function setTimelineZoom(nextPixelsPerFrame, anchorFrame, anchorViewportX) {
        const next = Math.max(minimumPixelsPerFrame, Math.min(12, nextPixelsPerFrame));
        if (Math.abs(next - pixelsPerFrame) < Math.max(0.0000001, pixelsPerFrame * 0.0001))
            return;
        const frame = Math.max(0, Number(anchorFrame));
        const viewportX = Math.max(0, Math.min(timelineFlick.width, Number(anchorViewportX)));
        pixelsPerFrame = next;
        const nextContentWidth = Math.max(timelineFlick.width, contentFrameCount * next);
        timelineFlick.contentX = Math.max(
            0,
            Math.min(nextContentWidth - timelineFlick.width, frame * next - viewportX));
        timeRuler.requestPaint();
    }

    function zoomAtPlayhead(factor) {
        let anchorFrame = visiblePlayheadFrame;
        let anchorViewportX = anchorFrame * pixelsPerFrame - timelineFlick.contentX;
        if (anchorViewportX < 0 || anchorViewportX > timelineFlick.width) {
            anchorViewportX = timelineFlick.width / 2;
            anchorFrame = (timelineFlick.contentX + anchorViewportX) / pixelsPerFrame;
        }
        setTimelineZoom(pixelsPerFrame * factor, anchorFrame, anchorViewportX);
    }

    function zoomAtViewportPoint(factor, viewportX) {
        const anchorX = Math.max(0, Math.min(timelineFlick.width, viewportX));
        const anchorFrame = (timelineFlick.contentX + anchorX) / pixelsPerFrame;
        setTimelineZoom(pixelsPerFrame * factor, anchorFrame, anchorX);
    }

    function fitTimeline() {
        if (timelineFlick.width <= 0)
            return;
        pixelsPerFrame = Math.max(
            minimumPixelsPerFrame,
            Math.min(12, (timelineFlick.width - 2) / Math.max(1, contentFrameCount)));
        timelineFlick.contentX = 0;
        timeRuler.requestPaint();
    }

    function synchronizeInitialZoom() {
        const sequenceId = workspaceController.activeSequenceId;
        if (sequenceId !== zoomSequenceId) {
            zoomSequenceId = sequenceId;
            zoomAwaitingFirstContent = workspaceController.timelineDurationFrames <= 0;
            Qt.callLater(fitTimeline);
        } else if (zoomAwaitingFirstContent && workspaceController.timelineDurationFrames > 0) {
            zoomAwaitingFirstContent = false;
            Qt.callLater(fitTimeline);
        }
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

    function openClipContextMenu(clipId) {
        contextClipId = clipId;
        if (!timelineController.isClipSelected(clipId))
            timelineController.selectClip(clipId, false);
        clipContextMenu.popup();
    }

    Menu {
        id: clipContextMenu
        objectName: "timelineClipContextMenu"
        MenuItem {
            text: qsTr("在播放头处分割") + "\tCtrl+K"
            enabled: root.contextClipId.length > 0
            onTriggered: timelineController.splitClip(root.contextClipId, root.playheadFrame)
        }
        MenuItem {
            text: qsTr("创建片段副本") + "\tCtrl+D"
            enabled: root.contextClipId.length > 0
            onTriggered: timelineController.duplicateClip(
                root.contextClipId, root.pixelsPerFrame, root.playheadFrame)
        }
        MenuSeparator {}
        MenuItem {
            text: qsTr("删除所选片段") + "\tDelete"
            enabled: timelineController.selectedClipIds.length > 0
            onTriggered: timelineController.deleteSelectedClips(false)
        }
        MenuItem {
            text: qsTr("波纹删除所选片段") + "\tShift+Delete"
            enabled: timelineController.selectedClipIds.length > 0
            onTriggered: timelineController.deleteSelectedClips(true)
        }
    }

    onPlayheadFrameChanged: {
        if (!playheadScrubbing && playheadSeekPending && playheadFrame === interactivePlayheadFrame)
            playheadSeekPending = false;
    }

    Component.onCompleted: Qt.callLater(synchronizeInitialZoom)

    Connections {
        target: workspaceController
        function onProjectStateChanged() {
            root.synchronizeInitialZoom();
        }
        function onHistoryChanged() {
            root.synchronizeInitialZoom();
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 44
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
                        leftPadding: 10
                        rightPadding: 10
                        enabled: timelineController.selectedClipId.length > 0
                        onClicked: timelineController.splitClip(timelineController.selectedClipId, root.playheadFrame)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("在播放头处分割所选片段（Ctrl+K / Ctrl+B）")
                    }
                    AppButton {
                        text: "⧉"
                        Accessible.name: qsTr("创建片段副本")
                        leftPadding: 10
                        rightPadding: 10
                        enabled: timelineController.selectedClipId.length > 0
                        onClicked: timelineController.duplicateClip(timelineController.selectedClipId, root.pixelsPerFrame, root.playheadFrame)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("在片段末尾创建副本（Ctrl+D）")
                    }
                    AppButton {
                        text: "⌫"
                        Accessible.name: qsTr("删除所选片段")
                        leftPadding: 10
                        rightPadding: 10
                        enabled: timelineController.selectedClipIds.length > 0
                        onClicked: timelineController.deleteSelectedClips(false)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("删除所选片段并保留空隙（Delete）")
                    }
                    AppButton {
                        text: qsTr("波纹删")
                        enabled: timelineController.selectedClipIds.length > 0
                        onClicked: timelineController.deleteSelectedClips(true)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("删除所选片段并闭合空隙（Shift+Delete）")
                    }
                    AppButton {
                        text: "M"
                        Accessible.name: qsTr("添加标记")
                        leftPadding: 11
                        rightPadding: 11
                        onClicked: timelineController.addTimelineMarker(root.playheadFrame)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("在播放头处添加标记（M）")
                    }
                    AppButton {
                        text: "I"
                        Accessible.name: qsTr("设置入点")
                        leftPadding: 11
                        rightPadding: 11
                        enabled: workspaceController.timelineDurationFrames > 0
                        onClicked: timelineController.setSequenceInPoint(root.playheadFrame)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("设置入点（I）")
                    }
                    AppButton {
                        text: "O"
                        Accessible.name: qsTr("设置出点")
                        leftPadding: 11
                        rightPadding: 11
                        enabled: workspaceController.timelineDurationFrames > 0
                        onClicked: timelineController.setSequenceOutPoint(root.playheadFrame)
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("设置出点（O）")
                    }
                    AppButton {
                        objectName: "timelineSnapButton"
                        text: qsTr("吸附")
                        checkable: true
                        checked: root.snapEnabled
                        onClicked: root.snapEnabled = checked
                        ToolTip.visible: hovered
                        ToolTip.text: checked
                            ? qsTr("吸附已开启（S）") : qsTr("吸附已关闭（S）")
                    }
                    AppButton {
                        id: timelineMoreButton
                        objectName: "timelineMoreButton"
                        text: qsTr("更多") + " ▾"
                        onClicked: timelineMoreMenu.open()
                        Menu {
                            id: timelineMoreMenu
                            y: timelineMoreButton.height + 4
                            MenuItem {
                                objectName: "smartSequenceBoundsButton"
                                text: timelineController.sequenceBoundaryAnalysisRunning
                                    ? qsTr("正在分析入出点…") : qsTr("智能设置入出点")
                                enabled: workspaceController.timelineDurationFrames > 0
                                    && !timelineController.sequenceBoundaryAnalysisRunning
                                onTriggered: timelineController.analyzeSequenceBoundaries()
                            }
                            MenuItem {
                                text: qsTr("清除入点和出点")
                                enabled: workspaceController.hasSequenceInOut
                                onTriggered: timelineController.clearSequenceInOut()
                            }
                            MenuSeparator {}
                            MenuItem {
                                text: timelineController.rangeInFrame < 0
                                    ? qsTr("设置短视频选区起点")
                                    : qsTr("重新设置短视频选区起点")
                                onTriggered: timelineController.setRangeIn(root.playheadFrame)
                            }
                            MenuItem {
                                text: qsTr("设置短视频选区终点")
                                enabled: timelineController.rangeInFrame >= 0
                                onTriggered: timelineController.commitTimelineRange(root.playheadFrame)
                            }
                            MenuItem {
                                text: qsTr("从所选区间创建短视频")
                                enabled: timelineController.selectedRangeId.length > 0
                                onTriggered: timelineController.createShortFromRange(
                                    timelineController.selectedRangeId)
                            }
                            MenuSeparator {}
                            MenuItem {
                                text: qsTr("添加视频轨")
                                onTriggered: timelineController.addTrack("video")
                            }
                            MenuItem {
                                text: qsTr("添加音频轨")
                                onTriggered: timelineController.addTrack("audio")
                            }
                            MenuItem {
                                text: qsTr("添加字幕轨")
                                onTriggered: timelineController.addTrack("subtitle")
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Text {
                        text: qsTr("缩放")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Slider {
                        id: timelineZoomSlider
                        objectName: "timelineZoomSlider"
                        from: root.minimumPixelsPerFrame
                        to: 12
                        value: root.pixelsPerFrame
                        onMoved: {
                            let anchorFrame = root.visiblePlayheadFrame;
                            let anchorX = anchorFrame * root.pixelsPerFrame - timelineFlick.contentX;
                            if (anchorX < 0 || anchorX > timelineFlick.width) {
                                anchorX = timelineFlick.width / 2;
                                anchorFrame = (timelineFlick.contentX + anchorX) / root.pixelsPerFrame;
                            }
                            root.setTimelineZoom(value, anchorFrame, anchorX);
                        }
                        Layout.preferredWidth: 110
                    }
                    AppButton {
                        objectName: "fitTimelineButton"
                        text: qsTr("适配")
                        Accessible.name: qsTr("适配整个时间线")
                        onClicked: root.fitTimeline()
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("显示整条时间线")
                    }
                }
            }
        }

        Flickable {
            id: timelineFlick
            objectName: "timelineScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: root.trackControlsWidth
            clip: true
            contentWidth: Math.max(width, root.contentFrameCount * root.pixelsPerFrame)
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

            Canvas {
                id: timeRuler
                objectName: "timelineRuler"
                property real scrollX: timelineFlick.contentX
                property real pixelsScale: root.pixelsPerFrame
                property int majorStepFrames: root.rulerMajorStepFrames()
                x: timelineFlick.contentX
                y: 0
                width: timelineFlick.width
                height: 28
                antialiasing: false
                onScrollXChanged: requestPaint()
                onPixelsScaleChanged: requestPaint()
                onMajorStepFramesChanged: requestPaint()
                onWidthChanged: requestPaint()
                onPaint: {
                    const context = getContext("2d");
                    context.clearRect(0, 0, width, height);
                    context.fillStyle = "#14181e";
                    context.fillRect(0, 0, width, height);
                    context.strokeStyle = "#343b46";
                    context.lineWidth = 1;
                    context.beginPath();
                    context.moveTo(0, height - 0.5);
                    context.lineTo(width, height - 0.5);
                    context.stroke();

                    const major = Math.max(1, majorStepFrames);
                    const minor = major % 5 === 0 ? major / 5
                        : major % 4 === 0 ? major / 4
                        : major % 2 === 0 ? major / 2 : major;
                    const firstVisibleFrame = Math.max(0, Math.floor(scrollX / pixelsScale));
                    const lastVisibleFrame = Math.ceil((scrollX + width) / pixelsScale);
                    const firstTick = Math.floor(firstVisibleFrame / minor) * minor;
                    context.font = Theme.fontSizeCaption + "px " + Theme.monoFontFamily;
                    context.textBaseline = "top";
                    for (let frame = firstTick; frame <= lastVisibleFrame + minor; frame += minor) {
                        const rulerX = Math.round(frame * pixelsScale - scrollX) + 0.5;
                        const majorTick = frame % major === 0;
                        context.strokeStyle = majorTick ? "#687386" : "#3b424d";
                        context.beginPath();
                        context.moveTo(rulerX, majorTick ? 15 : 21);
                        context.lineTo(rulerX, 28);
                        context.stroke();
                        if (majorTick) {
                            context.fillStyle = "#aeb8c7";
                            context.fillText(root.formatTimecode(frame), rulerX + 4, 2);
                        }
                    }
                }
            }

            WheelHandler {
                acceptedModifiers: Qt.ControlModifier
                onWheel: function (event) {
                    root.zoomAtViewportPoint(
                        event.angleDelta.y > 0 ? 1.16 : 1 / 1.16,
                        event.x);
                    event.accepted = true;
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
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    onPressed: function (mouse) {
                        timelineController.clearSelection();
                        root.seekToFrame(mouse.x / root.pixelsPerFrame);
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
                        Repeater {
                            model: assetKind === "web" && webController.isWebClip
                                && timelineController.isClipSelected(clipId)
                                ? webController.keyframesData : []
                            delegate: Rectangle {
                                required property var modelData
                                x: Math.max(5, Math.min(
                                    clipDelegate.width - 7,
                                    (modelData.frame - clipDelegate.startFrame) * root.pixelsPerFrame - 4))
                                anchors.verticalCenter: parent.verticalCenter
                                width: 8
                                height: 8
                                rotation: 45
                                radius: 1
                                color: Theme.warning
                                border.color: "white"
                                z: 7
                                ToolTip.visible: markerHover.hovered
                                ToolTip.text: modelData.layerId + "." + modelData.field
                                    + " · " + modelData.timeMs + " ms · " + modelData.easing
                                HoverHandler { id: markerHover }
                            }
                        }
                        MouseArea {
                            id: clipMouse
                            anchors.fill: parent
                            hoverEnabled: true
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
                                if (mouse.button === Qt.RightButton) {
                                    root.cancelClipDrag();
                                    root.openClipContextMenu(clipId);
                                    return;
                                }
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
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            property real pressContentX: 0
                            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                            onPressed: function (mouse) {
                                if (mouse.button === Qt.RightButton) {
                                    if (!timelineController.isClipSelected(clipId))
                                        timelineController.selectClip(clipId, false);
                                    return;
                                }
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
                                if (mouse.button === Qt.RightButton) {
                                    root.cancelClipDrag();
                                    root.openClipContextMenu(clipId);
                                    return;
                                }
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
                        required property string documentId
                        required property int audioTrackPosition
                        required property int startFrame
                        required property int endFrame
                        required property string text
                        required property bool timingOverridden
                        property real moveOffset: 0
                        property real leftTrimOffset: 0
                        property real rightTrimOffset: 0

                        objectName: "subtitleWaveformOverlay"
                        visible: audioTrackPosition >= 0
                        x: startFrame * root.pixelsPerFrame + moveOffset + leftTrimOffset
                        y: audioTrackPosition * root.trackPitch + 25
                        width: Math.max(12, (endFrame - startFrame) * root.pixelsPerFrame - leftTrimOffset + rightTrimOffset)
                        height: 28
                        radius: 4
                        color: "#d91a2029"
                        border.width: subtitleController.selectedSubtitlePlacementId === placementId ? 2 : 1
                        border.color: subtitleController.selectedSubtitlePlacementId === placementId ? "white" : Theme.subtitle
                        clip: true
                        activeFocusOnTab: true
                        Accessible.name: qsTr("字幕：%1。拖动可移动，拖动两侧可调整时间。 ").arg(text)
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
                        Rectangle {
                            visible: subtitleOverlay.timingOverridden
                            anchors.top: parent.top
                            anchors.right: parent.right
                            anchors.margins: 3
                            width: 6
                            height: 6
                            radius: 3
                            color: Theme.warning
                        }
                        MouseArea {
                            id: subtitleBody
                            objectName: "subtitleOverlayBody"
                            anchors.fill: parent
                            anchors.leftMargin: 7
                            anchors.rightMargin: 7
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            property real pressContentX: 0
                            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                            onPressed: function (mouse) {
                                subtitleController.selectSubtitlePlacement(placementId);
                                if (mouse.button === Qt.RightButton)
                                    return;
                                const point = subtitleOverlay.mapToItem(
                                    subtitleOverlayLayer, mouse.x, mouse.y);
                                pressContentX = point.x;
                            }
                            onPositionChanged: function (mouse) {
                                if (!pressed || (mouse.buttons & Qt.LeftButton) === 0)
                                    return;
                                const point = subtitleOverlay.mapToItem(
                                    subtitleOverlayLayer, mouse.x, mouse.y);
                                subtitleOverlay.moveOffset = Math.max(
                                    -subtitleOverlay.startFrame * root.pixelsPerFrame,
                                    point.x - pressContentX
                                );
                            }
                            onReleased: function (mouse) {
                                if (mouse.button === Qt.RightButton) {
                                    subtitleOverlayMenu.popup();
                                    return;
                                }
                                const delta = Math.round(
                                    subtitleOverlay.moveOffset / root.pixelsPerFrame);
                                if (delta !== 0)
                                    subtitleController.moveSubtitlePlacement(
                                        placementId,
                                        startFrame + delta,
                                        root.pixelsPerFrame,
                                        root.playheadFrame,
                                        root.snapEnabled && (mouse.modifiers & Qt.ShiftModifier) === 0
                                    );
                                else
                                    root.seekToFrame(startFrame);
                                subtitleOverlay.moveOffset = 0;
                            }
                            onCanceled: subtitleOverlay.moveOffset = 0
                            onDoubleClicked: subtitleController.previewSubtitlePlacement(placementId)
                            ToolTip.visible: containsMouse
                            ToolTip.text: subtitleOverlay.text + qsTr("\n拖动移动；双击播放；按住 Shift 临时关闭吸附")
                        }
                        Menu {
                            id: subtitleOverlayMenu
                            MenuItem {
                                text: qsTr("播放这一条")
                                onTriggered: subtitleController.previewSubtitlePlacement(placementId)
                            }
                            MenuItem {
                                text: qsTr("恢复字幕文档时间")
                                enabled: subtitleOverlay.timingOverridden
                                onTriggered: subtitleController.resetSubtitlePlacementTiming(placementId)
                            }
                        }
                        Rectangle {
                            id: subtitleLeftHandle
                            objectName: "subtitleLeftTrimHandle"
                            anchors.left: parent.left
                            width: 7
                            height: parent.height
                            color: leftHover.hovered ? "white" : "transparent"
                            z: 9
                            HoverHandler { id: leftHover }
                            DragHandler {
                                target: null
                                xAxis.enabled: true
                                yAxis.enabled: false
                                onTranslationChanged: subtitleOverlay.leftTrimOffset = Math.max(
                                    -subtitleOverlay.startFrame * root.pixelsPerFrame,
                                    Math.min(
                                        (subtitleOverlay.endFrame - subtitleOverlay.startFrame) * root.pixelsPerFrame - 8,
                                        translation.x
                                    )
                                )
                                onActiveChanged: if (!active && subtitleOverlay.leftTrimOffset !== 0) {
                                    const delta = Math.round(
                                        subtitleOverlay.leftTrimOffset / root.pixelsPerFrame);
                                    subtitleController.resizeSubtitlePlacement(
                                        placementId,
                                        startFrame + delta,
                                        endFrame,
                                        root.pixelsPerFrame,
                                        root.playheadFrame,
                                        root.snapEnabled
                                    );
                                    subtitleOverlay.leftTrimOffset = 0;
                                }
                            }
                        }
                        Rectangle {
                            id: subtitleRightHandle
                            objectName: "subtitleRightTrimHandle"
                            anchors.right: parent.right
                            width: 7
                            height: parent.height
                            color: rightHover.hovered ? "white" : "transparent"
                            z: 9
                            HoverHandler { id: rightHover }
                            DragHandler {
                                target: null
                                xAxis.enabled: true
                                yAxis.enabled: false
                                onTranslationChanged: subtitleOverlay.rightTrimOffset = Math.max(
                                    -((subtitleOverlay.endFrame - subtitleOverlay.startFrame) * root.pixelsPerFrame - 8),
                                    translation.x
                                )
                                onActiveChanged: if (!active && subtitleOverlay.rightTrimOffset !== 0) {
                                    const delta = Math.round(
                                        subtitleOverlay.rightTrimOffset / root.pixelsPerFrame);
                                    subtitleController.resizeSubtitlePlacement(
                                        placementId,
                                        startFrame,
                                        endFrame + delta,
                                        root.pixelsPerFrame,
                                        root.playheadFrame,
                                        root.snapEnabled
                                    );
                                    subtitleOverlay.rightTrimOffset = 0;
                                }
                            }
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

    Shortcut {
        sequence: "="
        enabled: !root.textInputActive
        onActivated: root.zoomAtPlayhead(1.25)
    }
    Shortcut {
        sequence: "-"
        enabled: !root.textInputActive
        onActivated: root.zoomAtPlayhead(1 / 1.25)
    }

    Item {
        id: trackControlsPanel
        objectName: "trackControlsPanel"
        anchors.top: parent.top
        anchors.topMargin: 44
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        width: Math.min(root.trackControlsWidth, root.width)
        visible: timelineController.tracksModel.rowCount() > 0
        clip: true
        z: 10

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 28
            color: Theme.surfaceSunken
            border.color: Theme.border

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 5
                spacing: 5
                Text {
                    Layout.fillWidth: true
                    text: qsTr("轨道")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    font.weight: Font.DemiBold
                }
                Button {
                    id: addTrackButton
                    objectName: "addTrackButton"
                    implicitWidth: 24
                    implicitHeight: 22
                    text: "+"
                    Accessible.name: qsTr("添加轨道")
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("添加轨道")
                    onClicked: addTrackMenu.open()
                    Menu {
                        id: addTrackMenu
                        y: addTrackButton.height + 3
                        MenuItem {
                            text: qsTr("视频轨")
                            onTriggered: timelineController.addTrack("video")
                        }
                        MenuItem {
                            text: qsTr("音频轨")
                            onTriggered: timelineController.addTrack("audio")
                        }
                        MenuItem {
                            text: qsTr("字幕轨")
                            onTriggered: timelineController.addTrack("subtitle")
                        }
                    }
                }
            }
        }

        Column {
            y: 28 - timelineFlick.contentY
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
                                text: model.enabled ? "✓" : "—"
                                implicitWidth: 24
                                implicitHeight: 22
                                Accessible.name: model.enabled ? qsTr("禁用轨道") : qsTr("启用轨道")
                                ToolTip.visible: hovered
                                ToolTip.text: Accessible.name
                                onClicked: timelineController.updateTrack(trackId, !model.enabled, locked, muted, solo, audioBusId)
                            }
                            Button {
                                text: locked ? "🔒" : "🔓"
                                implicitWidth: 24
                                implicitHeight: 22
                                Accessible.name: locked ? qsTr("解锁轨道") : qsTr("锁定轨道")
                                ToolTip.visible: hovered
                                ToolTip.text: Accessible.name
                                onClicked: timelineController.updateTrack(trackId, model.enabled, !locked, muted, solo, audioBusId)
                            }
                            Button {
                                text: "M"
                                checkable: true
                                checked: muted
                                implicitWidth: 24
                                implicitHeight: 22
                                Accessible.name: muted ? qsTr("取消静音") : qsTr("静音")
                                ToolTip.visible: hovered
                                ToolTip.text: Accessible.name
                                onClicked: timelineController.updateTrack(trackId, model.enabled, locked, !muted, solo, audioBusId)
                            }
                            Button {
                                text: "S"
                                checkable: true
                                checked: solo
                                implicitWidth: 24
                                implicitHeight: 22
                                Accessible.name: solo ? qsTr("取消独奏") : qsTr("独奏")
                                ToolTip.visible: hovered
                                ToolTip.text: Accessible.name
                                onClicked: timelineController.updateTrack(trackId, model.enabled, locked, muted, !solo, audioBusId)
                            }
                            Item {
                                Layout.fillWidth: true
                            }
                            Button {
                                text: "↑"
                                enabled: position > 0
                                implicitWidth: 24
                                implicitHeight: 22
                                Accessible.name: qsTr("轨道上移")
                                ToolTip.visible: hovered
                                ToolTip.text: Accessible.name
                                onClicked: timelineController.moveTrack(trackId, position - 1)
                            }
                            Button {
                                text: "↓"
                                enabled: position + 1 < timelineController.tracksModel.rowCount()
                                implicitWidth: 24
                                implicitHeight: 22
                                Accessible.name: qsTr("轨道下移")
                                ToolTip.visible: hovered
                                ToolTip.text: Accessible.name
                                onClicked: timelineController.moveTrack(trackId, position + 1)
                            }
                        }
                    }
                }
            }
        }
    }
}
