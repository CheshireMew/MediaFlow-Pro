import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    objectName: "timelineView"
    color: Theme.timelineBackground
    radius: Theme.radius
    border.width: 1
    border.color: Theme.borderSubtle
    clip: true
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
    property int draggingClipAudioTrackPosition: -1
    property string draggingClipTrackKind: ""
    property string draggingClipTrackId: ""
    property int trackControlsWidth: 196
    property bool snapEnabled: true
    property bool multiSelectMode: false
    property bool shortcutsEnabled: true
    property int trackHeight: 72
    property int trackPitch: trackHeight + 1
    readonly property real fpsExact: Math.max(1, mediaflow.workspaceViewController.profileFpsNumerator) / Math.max(1, mediaflow.workspaceViewController.profileFpsDenominator)
    readonly property int fpsRounded: Math.max(1, Math.round(mediaflow.workspaceViewController.profileFpsNumerator / Math.max(1, mediaflow.workspaceViewController.profileFpsDenominator)))
    readonly property int contentFrameCount: Math.max(Math.ceil(fpsExact * 10), mediaflow.workspaceViewController.timelineDurationFrames + Math.ceil(fpsExact * 2))
    readonly property real minimumPixelsPerFrame: Math.max(0.000001, Math.min(0.5, timelineFlick.width / Math.max(1, contentFrameCount)))
    readonly property int maxPlayheadFrame: Math.max(0, mediaflow.workspaceViewController.timelineDurationFrames - 1)
    readonly property Item focusedItem: root.Window.window ? root.Window.window.activeFocusItem : null
    readonly property bool textInputActive: focusedItem instanceof TextInput || focusedItem instanceof TextEdit
    readonly property bool canEdit: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
    readonly property bool modalOpen: timelineItemActions.modalOpen
    readonly property int visiblePlayheadFrame: playheadScrubbing || playheadSeekPending ? interactivePlayheadFrame : Math.min(playheadFrame, maxPlayheadFrame)
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
        const candidates = [1, 2, 5, 10, Math.round(fpsExact / 2), Math.round(fpsExact), Math.round(fpsExact * 2), Math.round(fpsExact * 5), Math.round(fpsExact * 10), Math.round(fpsExact * 15), Math.round(fpsExact * 30), Math.round(fpsExact * 60), Math.round(fpsExact * 120), Math.round(fpsExact * 300), Math.round(fpsExact * 600), Math.round(fpsExact * 900), Math.round(fpsExact * 1800), Math.round(fpsExact * 3600)];
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
        timelineFlick.contentX = Math.max(0, Math.min(nextContentWidth - timelineFlick.width, frame * next - viewportX));
        timelineFlick.repaintRuler();
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
        pixelsPerFrame = Math.max(minimumPixelsPerFrame, Math.min(12, (timelineFlick.width - 2) / Math.max(1, contentFrameCount)));
        timelineFlick.contentX = 0;
        timelineFlick.repaintRuler();
    }

    function synchronizeInitialZoom() {
        const sequenceId = mediaflow.workspaceViewController.activeSequenceId;
        if (sequenceId !== zoomSequenceId) {
            zoomSequenceId = sequenceId;
            zoomAwaitingFirstContent = mediaflow.workspaceViewController.timelineDurationFrames <= 0;
            Qt.callLater(fitTimeline);
        } else if (zoomAwaitingFirstContent && mediaflow.workspaceViewController.timelineDurationFrames > 0) {
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

    function requestedDragTrackPosition(originalTrackPosition, deltaY) {
        return Math.max(0, Math.min(mediaflow.timelineViewController.tracksModel.rowCount() - 1, Math.floor((originalTrackPosition * trackPitch + 12 + deltaY + 23) / trackPitch)));
    }

    function beginClipDrag(clipId, trackPosition, trackKind, audioTrackPosition) {
        if (!canEdit)
            return;
        draggingClipOffsetX = 0;
        draggingClipTrackPosition = trackPosition;
        draggingClipAudioTrackPosition = audioTrackPosition === undefined ? -1 : Number(audioTrackPosition);
        draggingClipTrackKind = trackKind;
        draggingClipTrackId = String(mediaflow.timelineViewController.tracksModel.get(trackPosition).trackId);
        draggingClipId = clipId;
    }

    function clearTimelineSelection() {
        multiSelectMode = false;
        mediaflow.timelineViewController.clearSelection();
    }

    function updateClipDrag(clipId, startFrame, originalTrackPosition, deltaX, deltaY) {
        if (!canEdit || draggingClipId !== clipId)
            return;
        draggingClipOffsetX = Math.max(-startFrame * pixelsPerFrame, deltaX);
        const requestedPosition = requestedDragTrackPosition(originalTrackPosition, deltaY);
        const nextFrame = Math.max(0, startFrame + Math.round(draggingClipOffsetX / pixelsPerFrame));
        const preview = mediaflow.timelineClipController.previewClipMove(clipId, nextFrame, requestedPosition, false);
        if (!preview.accepted)
            return;
        draggingClipTrackId = String(preview.trackId);
        draggingClipTrackPosition = Number(preview.trackPosition);
        draggingClipAudioTrackPosition = Number(preview.audioTrackPosition);
        draggingClipTrackKind = String(preview.trackKind);
    }

    function updateLinkedAudioDrag(clipId, startFrame, originalAudioTrackPosition, deltaX, deltaY) {
        if (!canEdit || draggingClipId !== clipId)
            return;
        draggingClipOffsetX = Math.max(-startFrame * pixelsPerFrame, deltaX);
        const requestedAudioPosition = requestedDragTrackPosition(originalAudioTrackPosition, deltaY);
        const nextFrame = Math.max(0, startFrame + Math.round(draggingClipOffsetX / pixelsPerFrame));
        const preview = mediaflow.timelineClipController.previewClipMove(clipId, nextFrame, requestedAudioPosition, true);
        if (!preview.accepted)
            return;
        draggingClipTrackId = String(preview.trackId);
        draggingClipTrackPosition = Number(preview.trackPosition);
        draggingClipAudioTrackPosition = Number(preview.audioTrackPosition);
        draggingClipTrackKind = String(preview.trackKind);
    }

    function cancelClipDrag() {
        draggingClipId = "";
        draggingClipOffsetX = 0;
        draggingClipTrackPosition = -1;
        draggingClipAudioTrackPosition = -1;
        draggingClipTrackKind = "";
        draggingClipTrackId = "";
    }

    function finishClipDrag(clipId, startFrame, originalTrackPosition, snapEnabled) {
        if (!canEdit || draggingClipId !== clipId)
            return;
        const nextFrame = Math.max(0, startFrame + Math.round(draggingClipOffsetX / pixelsPerFrame));
        const nextTrackPosition = draggingClipTrackPosition;
        const moved = nextFrame !== startFrame || nextTrackPosition !== originalTrackPosition;
        const nextTrackId = draggingClipTrackId;
        cancelClipDrag();
        if (moved)
            mediaflow.timelineClipController.moveClip(clipId, nextFrame, nextTrackId, pixelsPerFrame, playheadFrame, snapEnabled);
    }

    function openClipContextMenu(clipId) {
        contextClipId = clipId;
        if (!mediaflow.timelineViewController.isClipSelected(clipId))
            mediaflow.timelineViewController.selectClip(clipId, false);
        clipContextMenu.popup();
    }

    function openTimelineItemContextMenu(kind, itemId, itemName) {
        timelineItemActions.open(kind, itemId, itemName)
    }

    AppMenu {
        id: clipContextMenu
        objectName: "timelineClipContextMenu"
        AppMenuItem {
            objectName: "timelineSplitClipMenuItem"
            text: qsTr("在播放头处分割") + "\tCtrl+K"
            enabled: root.canEdit && root.contextClipId.length > 0
            onTriggered: mediaflow.timelineClipController.splitClip(root.contextClipId, root.playheadFrame)
        }
        AppMenuItem {
            text: qsTr("创建片段副本") + "\tCtrl+D"
            enabled: root.canEdit && root.contextClipId.length > 0
            onTriggered: mediaflow.timelineClipController.duplicateClip(root.contextClipId, root.pixelsPerFrame, root.playheadFrame)
        }
        AppMenuItem {
            objectName: "timelineDetachAudioMenuItem"
            text: qsTr("解除视音频绑定")
            enabled: root.canEdit && root.contextClipId.length > 0 && mediaflow.timelineViewController.selectedClipData.canDetachAudio === true
            onTriggered: mediaflow.timelineClipController.detachClipAudio(root.contextClipId)
        }
        AppMenuSeparator {}
        AppMenuItem {
            text: qsTr("删除所选片段") + "\tDelete"
            enabled: root.canEdit && mediaflow.timelineViewController.selectedClipIds.length > 0
            onTriggered: mediaflow.timelineClipController.deleteSelectedClips(false)
        }
        AppMenuItem {
            text: qsTr("波纹删除所选片段") + "\tShift+Delete"
            enabled: root.canEdit && mediaflow.timelineViewController.selectedClipIds.length > 0
            onTriggered: mediaflow.timelineClipController.deleteSelectedClips(true)
        }
    }

    TimelineItemMenu {
        id: timelineItemActions
        anchors.fill: parent
        canEdit: root.canEdit
    }

    onPlayheadFrameChanged: {
        if (!playheadScrubbing && playheadSeekPending && playheadFrame === interactivePlayheadFrame)
            playheadSeekPending = false;
    }

    Component.onCompleted: Qt.callLater(synchronizeInitialZoom)

    Connections {
        target: mediaflow.workspaceViewController
        function onProjectStateChanged() {
            root.synchronizeInitialZoom();
        }
        function onHistoryChanged() {
            root.synchronizeInitialZoom();
        }
    }
    Connections {
        target: mediaflow.timelineClipController
        function onExclusiveSelectionRequested() {
            root.multiSelectMode = false;
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TimelineToolbar {
            view: root
            timelineViewport: timelineFlick
        }

        TimelineCanvas {
            id: timelineFlick
            view: root
        }
    }

    Shortcut {
        sequence: "="
        enabled: root.shortcutsEnabled && !root.textInputActive
        onActivated: root.zoomAtPlayhead(1.25)
    }
    Shortcut {
        sequence: "-"
        enabled: root.shortcutsEnabled && !root.textInputActive
        onActivated: root.zoomAtPlayhead(1 / 1.25)
    }

    TimelineTrackControls {
        view: root
        scrollY: timelineFlick.contentY
    }
}
