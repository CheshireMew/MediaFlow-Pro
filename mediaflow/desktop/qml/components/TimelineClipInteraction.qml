import QtQuick

MouseArea {
    id: root
    required property var view
    required property var clipItem
    required property var layerItem
    required property string clipId
    required property int startFrame
    required property int trackPosition
    required property string trackKind
    required property int audioTrackPosition
    property bool linkedAudio: false
    property bool dragEnabled: true
    property real pressContentX: 0
    property real pressContentY: 0

    hoverEnabled: true
    preventStealing: true
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    cursorShape: dragEnabled
        ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
        : Qt.ArrowCursor

    onPressed: function (mouse) {
        if (mouse.button === Qt.RightButton) {
            if (!mediaflow.timelineViewController.isClipSelected(clipId))
                mediaflow.timelineViewController.selectClip(clipId, false);
            return;
        }
        const toggle = view.multiSelectMode
            || (mouse.modifiers & Qt.ControlModifier) !== 0;
        if (toggle || !mediaflow.timelineViewController.isClipSelected(clipId))
            mediaflow.timelineViewController.selectClip(clipId, toggle);
        if (!dragEnabled)
            return;
        const point = clipItem.mapToItem(layerItem, mouse.x, mouse.y);
        pressContentX = point.x;
        pressContentY = point.y;
        view.beginClipDrag(clipId, trackPosition, trackKind, audioTrackPosition);
    }

    onPositionChanged: function (mouse) {
        if (!pressed || view.draggingClipId !== clipId)
            return;
        const point = clipItem.mapToItem(layerItem, mouse.x, mouse.y);
        if (linkedAudio) {
            view.updateLinkedAudioDrag(
                clipId,
                startFrame,
                audioTrackPosition,
                point.x - pressContentX,
                point.y - pressContentY);
        } else {
            view.updateClipDrag(
                clipId,
                startFrame,
                trackPosition,
                point.x - pressContentX,
                point.y - pressContentY);
        }
    }

    onReleased: function (mouse) {
        if (mouse.button === Qt.RightButton) {
            view.cancelClipDrag();
            view.openClipContextMenu(clipId);
            return;
        }
        view.finishClipDrag(
            clipId,
            startFrame,
            trackPosition,
            view.snapEnabled && (mouse.modifiers & Qt.ShiftModifier) === 0);
    }

    onCanceled: view.cancelClipDrag()
}
