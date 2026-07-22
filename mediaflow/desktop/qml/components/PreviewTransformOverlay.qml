import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: root
    objectName: "previewTransformOverlay"
    property int previewPosition: 0
    property real draftX: 0
    property real draftY: 0
    property real draftScaleX: 1
    property real draftScaleY: 1
    property real draftRotation: 0
    property bool snapVertical: false
    property bool snapHorizontal: false
    property bool interactionVisible: true
    readonly property var clipData: timelineController.selectedClipData
    readonly property bool clipIsVisible: timelineController.selectedClipId.length > 0
        && previewPosition >= Number(clipData.startFrame ?? 0)
        && previewPosition < Number(clipData.endFrame ?? 0)

    visible: interactionVisible && clipIsVisible
    enabled: visible && !workspaceController.readOnly

    function reload() {
        draftX = Number(clipData.x ?? 0)
        draftY = Number(clipData.y ?? 0)
        draftScaleX = Math.max(0.01, Number(clipData.scaleX ?? 1))
        draftScaleY = Math.max(0.01, Number(clipData.scaleY ?? 1))
        draftRotation = Number(clipData.rotation ?? 0)
        snapVertical = false
        snapHorizontal = false
    }

    function commit() {
        if (!timelineController.selectedClipId)
            return
        timelineController.setClipTransform(
            timelineController.selectedClipId,
            draftX, draftY, draftScaleX, draftScaleY, draftRotation,
            Number(clipData.cropLeft ?? 0), Number(clipData.cropTop ?? 0),
            Number(clipData.cropRight ?? 0), Number(clipData.cropBottom ?? 0),
            Number(clipData.opacity ?? 1))
    }

    Connections {
        target: timelineController
        function onSelectionChanged() { root.reload() }
    }
    Component.onCompleted: reload()

    Rectangle {
        id: verticalGuide
        visible: root.snapVertical
        x: Math.round(root.width / 2)
        y: 0
        width: 1
        height: root.height
        color: Theme.accent
        opacity: 0.9
    }
    Rectangle {
        id: horizontalGuide
        visible: root.snapHorizontal
        x: 0
        y: Math.round(root.height / 2)
        width: root.width
        height: 1
        color: Theme.accent
        opacity: 0.9
    }

    Rectangle {
        id: bounds
        objectName: "previewTransformBounds"
        x: root.draftX * root.width / 100
        y: root.draftY * root.height / 100
        width: Math.max(8, root.draftScaleX * root.width)
        height: Math.max(8, root.draftScaleY * root.height)
        color: "transparent"
        border.width: 2
        border.color: Theme.accent
        transform: Rotation {
            origin.x: bounds.width / 2
            origin.y: bounds.height / 2
            angle: root.draftRotation
        }

        DragHandler {
            id: moveHandler
            target: null
            acceptedButtons: Qt.LeftButton
            property real startX: 0
            property real startY: 0
            property bool changed: false
            onActiveChanged: {
                if (active) {
                    startX = root.draftX
                    startY = root.draftY
                    changed = true
                } else if (changed) {
                    root.snapVertical = false
                    root.snapHorizontal = false
                    root.commit()
                    changed = false
                }
            }
            onTranslationChanged: {
                if (!active)
                    return
                var nextX = startX + translation.x * 100 / Math.max(1, root.width)
                var nextY = startY + translation.y * 100 / Math.max(1, root.height)
                var thresholdX = 800 / Math.max(1, root.width)
                var thresholdY = 800 / Math.max(1, root.height)
                var centerX = nextX + root.draftScaleX * 50
                var centerY = nextY + root.draftScaleY * 50
                root.snapVertical = Math.abs(centerX - 50) <= thresholdX
                root.snapHorizontal = Math.abs(centerY - 50) <= thresholdY
                root.draftX = root.snapVertical ? 50 - root.draftScaleX * 50 : nextX
                root.draftY = root.snapHorizontal ? 50 - root.draftScaleY * 50 : nextY
            }
        }

        Rectangle {
            id: scaleHandle
            objectName: "previewScaleHandle"
            width: 16
            height: 16
            radius: 4
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.rightMargin: -8
            anchors.bottomMargin: -8
            color: Theme.accent
            border.color: "white"
            border.width: 1
            z: 3
            DragHandler {
                target: null
                property real startScaleX: 1
                property real startScaleY: 1
                property bool changed: false
                onActiveChanged: {
                    if (active) {
                        startScaleX = root.draftScaleX
                        startScaleY = root.draftScaleY
                        changed = true
                    } else if (changed) {
                        root.commit()
                        changed = false
                    }
                }
                onTranslationChanged: {
                    if (!active)
                        return
                    root.draftScaleX = Math.max(
                        0.01, startScaleX + translation.x / Math.max(1, root.width))
                    root.draftScaleY = Math.max(
                        0.01, startScaleY + translation.y / Math.max(1, root.height))
                }
            }
        }

        Rectangle {
            id: rotationHandle
            objectName: "previewRotationHandle"
            width: 16
            height: 16
            radius: 8
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.top
            anchors.bottomMargin: 18
            color: Theme.surfaceRaised
            border.color: Theme.accent
            border.width: 2
            z: 3
            DragHandler {
                target: null
                property real startRotation: 0
                property bool changed: false
                onActiveChanged: {
                    if (active) {
                        startRotation = root.draftRotation
                        changed = true
                    } else if (changed) {
                        root.commit()
                        changed = false
                    }
                }
                onTranslationChanged: {
                    if (active)
                        root.draftRotation = startRotation + translation.x * 0.5
                }
            }
        }
    }
}
