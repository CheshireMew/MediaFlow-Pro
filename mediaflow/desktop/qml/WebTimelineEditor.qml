pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import "."
import "components"

Item {
    id: root
    objectName: "webTimelineEditor"
    property int playheadFrame: 0
    property var items: mediaflow.webTimelineController.timelineItemsData
    readonly property var intervals: items.filter(item => item.kind === "interval")
    readonly property var keyframes: items.filter(item => item.kind === "keyframe")
    readonly property int durationMs: items.length > 0
        ? Math.max(1, Number(items[0].durationMs || 1)) : 1
    readonly property real labelWidth: 78
    readonly property real trackWidth: Math.max(80, width - labelWidth - 8)
    signal seekRequested(int frame)
    implicitHeight: 42 + intervals.length * 36 + (keyframes.length > 0 ? 34 : 0)

    function xForTime(timeMs) {
        return labelWidth + Math.max(0, Math.min(1, timeMs / durationMs)) * trackWidth;
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: Theme.surfaceSunken
        border.color: Theme.border
    }

    Text {
        x: 8
        y: 7
        text: qsTr("场景时间")
        color: Theme.textMuted
        font.pixelSize: 10
    }

    Repeater {
        model: 5
        delegate: Item {
            required property int index
            x: root.labelWidth + index * root.trackWidth / 4
            y: 4
            width: 1
            height: root.height - 8
            Rectangle {
                anchors.fill: parent
                color: Theme.border
                opacity: 0.65
            }
            Text {
                anchors.top: parent.top
                anchors.horizontalCenter: parent.horizontalCenter
                text: Math.round(index * root.durationMs / 400) / 10 + "s"
                color: Theme.textMuted
                font.pixelSize: 9
            }
        }
    }

    Repeater {
        model: root.intervals
        delegate: Item {
            id: intervalRow
            required property int index
            required property var modelData
            property int previewStart: Number(modelData.startMs)
            property int previewEnd: Number(modelData.endMs)
            x: 0
            y: 30 + index * 36
            width: root.width
            height: 30

            function preview() {
                mediaflow.webTimelineController.previewTimelineInterval(
                    String(modelData.startField),
                    String(modelData.endField),
                    previewStart,
                    previewEnd,
                    Boolean(modelData.endIsDuration));
            }

            function commit() {
                mediaflow.webTimelineController.commitTimelineInterval(
                    String(modelData.startField),
                    String(modelData.endField),
                    previewStart,
                    previewEnd,
                    Boolean(modelData.endIsDuration));
            }

            Text {
                x: 8
                width: root.labelWidth - 12
                anchors.verticalCenter: parent.verticalCenter
                text: String(intervalRow.modelData.label)
                color: Theme.textMuted
                font.pixelSize: 10
                elide: Text.ElideRight
            }

            Rectangle {
                id: bar
                x: root.xForTime(intervalRow.previewStart)
                width: Math.max(
                    12,
                    root.xForTime(intervalRow.previewEnd)
                        - root.xForTime(intervalRow.previewStart))
                anchors.verticalCenter: parent.verticalCenter
                height: 16
                radius: 8
                color: Theme.web
                border.color: Theme.textStrong
                opacity: 0.9

                DragHandler {
                    id: barDrag
                    enabled: root.enabled
                    target: null
                    xAxis.enabled: true
                    yAxis.enabled: false
                    property int initialStart: 0
                    property int initialEnd: 0
                    onActiveChanged: {
                        if (active) {
                            initialStart = intervalRow.previewStart;
                            initialEnd = intervalRow.previewEnd;
                        } else if (intervalRow.previewStart !== initialStart) {
                            intervalRow.commit();
                        }
                    }
                    onTranslationChanged: {
                        if (!active)
                            return;
                        const length = initialEnd - initialStart;
                        const delta = translation.x / root.trackWidth * root.durationMs;
                        let next = mediaflow.webTimelineController.snapSceneTimeMs(initialStart + delta);
                        next = Math.max(0, Math.min(root.durationMs - length - 1, next));
                        intervalRow.previewStart = next;
                        intervalRow.previewEnd = next + length;
                        intervalRow.preview();
                        root.seekRequested(mediaflow.webTimelineController.frameForSceneTime(next));
                    }
                    onCanceled: {
                        intervalRow.previewStart = initialStart;
                        intervalRow.previewEnd = initialEnd;
                        mediaflow.webTimelineController.cancelTimelinePreview();
                    }
                }

                Rectangle {
                    width: 7
                    height: parent.height + 4
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    radius: 3
                    color: Theme.textStrong
                    DragHandler {
                        id: leftHandle
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        property int initialStart: 0
                        onActiveChanged: {
                            if (active) {
                                initialStart = intervalRow.previewStart;
                            } else if (intervalRow.previewStart !== initialStart) {
                                intervalRow.commit();
                            }
                        }
                        onTranslationChanged: {
                            if (!active)
                                return;
                            const raw = initialStart
                                + translation.x / root.trackWidth * root.durationMs;
                            intervalRow.previewStart = Math.min(
                                intervalRow.previewEnd - 1,
                                mediaflow.webTimelineController.snapSceneTimeMs(raw));
                            intervalRow.preview();
                            root.seekRequested(mediaflow.webTimelineController.frameForSceneTime(
                                intervalRow.previewStart));
                        }
                        onCanceled: {
                            intervalRow.previewStart = initialStart;
                            mediaflow.webTimelineController.cancelTimelinePreview();
                        }
                    }
                }

                Rectangle {
                    width: 7
                    height: parent.height + 4
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    radius: 3
                    color: Theme.textStrong
                    DragHandler {
                        id: rightHandle
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        property int initialEnd: 0
                        onActiveChanged: {
                            if (active) {
                                initialEnd = intervalRow.previewEnd;
                            } else if (intervalRow.previewEnd !== initialEnd) {
                                intervalRow.commit();
                            }
                        }
                        onTranslationChanged: {
                            if (!active)
                                return;
                            const raw = initialEnd
                                + translation.x / root.trackWidth * root.durationMs;
                            intervalRow.previewEnd = Math.max(
                                intervalRow.previewStart + 1,
                                mediaflow.webTimelineController.snapSceneTimeMs(raw));
                            intervalRow.preview();
                            root.seekRequested(mediaflow.webTimelineController.frameForSceneTime(
                                intervalRow.previewEnd));
                        }
                        onCanceled: {
                            intervalRow.previewEnd = initialEnd;
                            mediaflow.webTimelineController.cancelTimelinePreview();
                        }
                    }
                }
            }
        }
    }

    Item {
        id: keyframeTrack
        visible: root.keyframes.length > 0
        x: 0
        y: 30 + root.intervals.length * 36
        width: root.width
        height: 30
        Text {
            x: 8
            width: root.labelWidth - 12
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("关键帧")
            color: Theme.textMuted
            font.pixelSize: 10
        }
        Repeater {
            model: root.keyframes
            delegate: Rectangle {
                id: marker
                required property var modelData
                property int previewTime: Number(modelData.timeMs)
                x: root.xForTime(previewTime) - 5
                anchors.verticalCenter: parent.verticalCenter
                width: 10
                height: 10
                rotation: 45
                radius: 1
                color: Theme.cut
                border.color: Theme.textStrong
                z: 4
                ToolTip.visible: markerHover.hovered
                ToolTip.text: String(modelData.sourceId)
                    + " · " + previewTime + " ms"
                HoverHandler { id: markerHover }
                DragHandler {
                    target: null
                    xAxis.enabled: true
                    yAxis.enabled: false
                    property int initialTime: 0
                    onActiveChanged: {
                        if (active) {
                            initialTime = marker.previewTime;
                        } else if (marker.previewTime !== initialTime) {
                            mediaflow.webTimelineController.moveTimelineKeyframe(
                                String(marker.modelData.target),
                                String(marker.modelData.sourceId),
                                initialTime,
                                mediaflow.webTimelineController.frameForSceneTime(marker.previewTime));
                        }
                    }
                    onTranslationChanged: {
                        if (!active)
                            return;
                        const raw = initialTime
                            + translation.x / root.trackWidth * root.durationMs;
                        marker.previewTime = mediaflow.webTimelineController.snapSceneTimeMs(raw);
                        const frame = mediaflow.webTimelineController.frameForSceneTime(
                            marker.previewTime);
                        mediaflow.webTimelineController.previewTimelineKeyframe(
                            String(marker.modelData.target),
                            String(marker.modelData.sourceId),
                            initialTime,
                            frame);
                        root.seekRequested(frame);
                    }
                    onCanceled: {
                        marker.previewTime = initialTime;
                        mediaflow.webTimelineController.cancelTimelinePreview();
                    }
                }
            }
        }
    }

    Rectangle {
        x: root.xForTime(mediaflow.webTimelineController.sceneTimeMsForFrame(root.playheadFrame))
        y: 24
        width: 2
        height: root.height - 28
        color: Theme.accent
        z: 10
    }
}
