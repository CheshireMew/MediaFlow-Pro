pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: subtitleOverlayLayer
    required property var view
    required property var timelineCanvas
    required property real tracksHeight
    property var placementRows: []
    property bool rowsSyncPending: false
    property string contextPlacementId: ""
    objectName: "subtitleOverlayLayer"
    x: 0
    y: 28
    width: timelineCanvas.contentWidth
    height: tracksHeight
    z: 6

    function synchronizeRows() {
        rowsSyncPending = false;
        placementRows = subtitleController.subtitlePlacementsModel.snapshot();
        subtitleCanvas.requestPaint();
    }

    function scheduleRowsSync() {
        if (rowsSyncPending)
            return;
        rowsSyncPending = true;
        Qt.callLater(synchronizeRows);
    }

    function placementAt(viewportX, viewportY) {
        const contentX = viewportX + timelineCanvas.contentX;
        for (let index = placementRows.length - 1; index >= 0; --index) {
            const row = placementRows[index];
            if (Number(row.audioTrackPosition) < 0)
                continue;
            const left = Number(row.startFrame) * view.pixelsPerFrame;
            const right = left + Math.max(
                12,
                (Number(row.endFrame) - Number(row.startFrame)) * view.pixelsPerFrame);
            const top = Number(row.audioTrackPosition) * view.trackPitch + 25;
            if (contentX >= left && contentX <= right
                    && viewportY >= top && viewportY <= top + 28)
                return row;
        }
        return {};
    }

    Component.onCompleted: synchronizeRows()
    onPlacementRowsChanged: subtitleCanvas.requestPaint()

    Connections {
        target: subtitleController.subtitlePlacementsModel
        ignoreUnknownSignals: true
        function onModelReset() { subtitleOverlayLayer.scheduleRowsSync(); }
        function onRowsInserted() { subtitleOverlayLayer.scheduleRowsSync(); }
        function onRowsRemoved() { subtitleOverlayLayer.scheduleRowsSync(); }
        function onRowsMoved() { subtitleOverlayLayer.scheduleRowsSync(); }
        function onDataChanged() { subtitleOverlayLayer.scheduleRowsSync(); }
    }

    Connections {
        target: subtitleController
        function onSelectionChanged() { subtitleCanvas.requestPaint(); }
    }

    Canvas {
        id: subtitleCanvas
        objectName: "subtitleOverviewCanvas"
        x: timelineCanvas.contentX
        width: timelineCanvas.width
        height: subtitleOverlayLayer.height
        z: 1
        antialiasing: false
        property real scrollX: timelineCanvas.contentX
        property real pixelsScale: view.pixelsPerFrame
        property color normalFill: Theme.subtitleSoft
        property color selectedFill: Theme.selectionSoft
        property color borderColor: Theme.borderStrong
        property color selectedBorderColor: Theme.accentHover
        property color overrideColor: Theme.warning
        property color labelColor: Theme.textStrong
        onScrollXChanged: requestPaint()
        onPixelsScaleChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onNormalFillChanged: requestPaint()
        onSelectedFillChanged: requestPaint()
        onBorderColorChanged: requestPaint()
        onSelectedBorderColorChanged: requestPaint()
        onOverrideColorChanged: requestPaint()
        onLabelColorChanged: requestPaint()
        onPaint: {
            const context = getContext("2d");
            context.clearRect(0, 0, width, height);
            context.font = Theme.canvasMonospaceFont(Theme.fontSizeCaption);
            context.textBaseline = "middle";
            const selectedId = subtitleController.selectedSubtitlePlacementId;
            for (let index = 0; index < subtitleOverlayLayer.placementRows.length; ++index) {
                const row = subtitleOverlayLayer.placementRows[index];
                const audioPosition = Number(row.audioTrackPosition);
                if (audioPosition < 0)
                    continue;
                const itemX = Number(row.startFrame) * pixelsScale - scrollX;
                const itemWidth = Math.max(
                    12,
                    (Number(row.endFrame) - Number(row.startFrame)) * pixelsScale);
                if (itemX + itemWidth < 0 || itemX > width)
                    continue;
                const itemY = audioPosition * view.trackPitch + 25;
                const selected = String(row.placementId) === selectedId;
                context.fillStyle = selected ? selectedFill : normalFill;
                context.strokeStyle = selected ? selectedBorderColor : borderColor;
                context.lineWidth = selected ? 2 : 1;
                context.fillRect(itemX, itemY, itemWidth, 28);
                context.strokeRect(itemX, itemY, itemWidth, 28);
                context.fillStyle = Theme.subtitle;
                context.fillRect(itemX, itemY, 3, 28);
                if (row.timingOverridden) {
                    context.fillStyle = overrideColor;
                    context.fillRect(itemX + itemWidth - 6, itemY + 3, 4, 4);
                }
                if (itemWidth >= 44) {
                    context.save();
                    context.beginPath();
                    context.rect(itemX + 6, itemY, itemWidth - 10, 28);
                    context.clip();
                    context.fillStyle = labelColor;
                    context.fillText("CC · " + String(row.text), itemX + 7, itemY + 14);
                    context.restore();
                }
            }
        }

        MouseArea {
            id: overviewMouse
            objectName: "subtitleOverviewMouse"
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            propagateComposedEvents: true
            property var hoveredPlacement: ({})
            cursorShape: Object.keys(hoveredPlacement).length > 0
                ? Qt.PointingHandCursor : Qt.ArrowCursor
            Accessible.name: qsTr("序列字幕，共 %1 条。点击可选择，双击可播放。")
                .arg(subtitleOverlayLayer.placementRows.length)
            Accessible.role: Accessible.List
            onPositionChanged: function (mouse) {
                hoveredPlacement = subtitleOverlayLayer.placementAt(mouse.x, mouse.y);
            }
            onExited: hoveredPlacement = ({})
            onPressed: function (mouse) {
                const placement = subtitleOverlayLayer.placementAt(mouse.x, mouse.y);
                if (!placement.placementId) {
                    mouse.accepted = false;
                    return;
                }
                subtitleController.selectSubtitlePlacement(String(placement.placementId));
                if (mouse.button === Qt.RightButton) {
                    subtitleOverlayLayer.contextPlacementId = String(placement.placementId);
                    overviewMenu.popup();
                } else {
                    view.seekToFrame(Number(placement.startFrame));
                }
            }
            onDoubleClicked: function (mouse) {
                const placement = subtitleOverlayLayer.placementAt(mouse.x, mouse.y);
                if (placement.placementId)
                    subtitleController.previewSubtitlePlacement(String(placement.placementId));
                else
                    mouse.accepted = false;
            }
            ToolTip.visible: containsMouse && hoveredPlacement.placementId !== undefined
            ToolTip.text: String(hoveredPlacement.text || "")
                + qsTr("\n点击选择；双击播放")
        }
    }

    AppMenu {
        id: overviewMenu
        AppMenuItem {
            text: qsTr("播放这一条")
            onTriggered: subtitleController.previewSubtitlePlacement(
                subtitleOverlayLayer.contextPlacementId)
        }
        AppMenuItem {
            text: qsTr("恢复字幕文档时间")
            enabled: view.canEdit
            onTriggered: subtitleController.resetSubtitlePlacementTiming(
                subtitleOverlayLayer.contextPlacementId)
        }
    }

    Rectangle {
        id: subtitleOverlay
        property var placement: subtitleController.selectedSubtitlePlacementData
        property string placementId: String(placement.placementId || "")
        property int audioTrackPosition: Number(placement.audioTrackPosition ?? -1)
        property int startFrame: Number(placement.startFrame || 0)
        property int endFrame: Number(placement.endFrame || 0)
        property string subtitleText: String(placement.text || "")
        property bool timingOverridden: Boolean(placement.timingOverridden)
        property real moveOffset: 0
        property real leftTrimOffset: 0
        property real rightTrimOffset: 0

        objectName: "subtitleWaveformOverlay"
        visible: placementId.length > 0 && audioTrackPosition >= 0
        x: startFrame * view.pixelsPerFrame + moveOffset + leftTrimOffset
        y: audioTrackPosition * view.trackPitch + 25
        width: Math.max(
            12,
            (endFrame - startFrame) * view.pixelsPerFrame
                - leftTrimOffset + rightTrimOffset)
        height: 28
        radius: 4
        color: Theme.selectionSoft
        border.width: 2
        border.color: Theme.accentHover
        clip: true
        z: 2
        activeFocusOnTab: true
        Accessible.name: qsTr("字幕：%1。拖动可移动，拖动两侧可调整时间。")
            .arg(subtitleText)
        Accessible.role: Accessible.ListItem
        Keys.onReturnPressed: subtitleController.previewSubtitlePlacement(placementId)
        Keys.onSpacePressed: subtitleController.previewSubtitlePlacement(placementId)

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 3
            color: Theme.subtitle
        }
        Text {
            anchors.fill: parent
            anchors.leftMargin: 6
            anchors.rightMargin: 6
            text: "CC · " + subtitleOverlay.subtitleText
            color: Theme.textStrong
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
                if (mouse.button === Qt.RightButton || !view.canEdit)
                    return;
                const point = subtitleOverlay.mapToItem(
                    subtitleOverlayLayer, mouse.x, mouse.y);
                pressContentX = point.x;
            }
            onPositionChanged: function (mouse) {
                if (!view.canEdit || !pressed
                        || (mouse.buttons & Qt.LeftButton) === 0)
                    return;
                const point = subtitleOverlay.mapToItem(
                    subtitleOverlayLayer, mouse.x, mouse.y);
                subtitleOverlay.moveOffset = Math.max(
                    -subtitleOverlay.startFrame * view.pixelsPerFrame,
                    point.x - pressContentX);
            }
            onReleased: function (mouse) {
                if (mouse.button === Qt.RightButton) {
                    subtitleOverlayMenu.popup();
                    return;
                }
                if (!view.canEdit) {
                    view.seekToFrame(subtitleOverlay.startFrame);
                    return;
                }
                const delta = Math.round(
                    subtitleOverlay.moveOffset / view.pixelsPerFrame);
                if (delta !== 0)
                    subtitleController.moveSubtitlePlacement(
                        subtitleOverlay.placementId,
                        subtitleOverlay.startFrame + delta,
                        view.pixelsPerFrame,
                        view.playheadFrame,
                        view.snapEnabled
                            && (mouse.modifiers & Qt.ShiftModifier) === 0);
                else
                    view.seekToFrame(subtitleOverlay.startFrame);
                subtitleOverlay.moveOffset = 0;
            }
            onCanceled: subtitleOverlay.moveOffset = 0
            onDoubleClicked: subtitleController.previewSubtitlePlacement(
                subtitleOverlay.placementId)
            ToolTip.visible: containsMouse
            ToolTip.text: subtitleOverlay.subtitleText
                + qsTr("\n拖动移动；双击播放；按住 Shift 临时关闭吸附")
        }
        AppMenu {
            id: subtitleOverlayMenu
            AppMenuItem {
                text: qsTr("播放这一条")
                onTriggered: subtitleController.previewSubtitlePlacement(
                    subtitleOverlay.placementId)
            }
            AppMenuItem {
                text: qsTr("恢复字幕文档时间")
                enabled: view.canEdit && subtitleOverlay.timingOverridden
                onTriggered: subtitleController.resetSubtitlePlacementTiming(
                    subtitleOverlay.placementId)
            }
        }
        Rectangle {
            id: subtitleLeftHandle
            objectName: "subtitleLeftTrimHandle"
            anchors.left: parent.left
            width: 7
            height: parent.height
            color: leftHover.hovered ? Theme.cutHover : Theme.transparent
            z: 9
            HoverHandler {
                id: leftHover
            }
            DragHandler {
                enabled: view.canEdit
                target: null
                xAxis.enabled: true
                yAxis.enabled: false
                onTranslationChanged: subtitleOverlay.leftTrimOffset = Math.max(
                    -subtitleOverlay.startFrame * view.pixelsPerFrame,
                    Math.min(
                        (subtitleOverlay.endFrame - subtitleOverlay.startFrame)
                            * view.pixelsPerFrame - 8,
                        translation.x))
                onActiveChanged: if (!active && subtitleOverlay.leftTrimOffset !== 0) {
                    const delta = Math.round(
                        subtitleOverlay.leftTrimOffset / view.pixelsPerFrame);
                    subtitleController.resizeSubtitlePlacement(
                        subtitleOverlay.placementId,
                        subtitleOverlay.startFrame + delta,
                        subtitleOverlay.endFrame,
                        view.pixelsPerFrame,
                        view.playheadFrame,
                        view.snapEnabled);
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
            color: rightHover.hovered ? Theme.cutHover : Theme.transparent
            z: 9
            HoverHandler {
                id: rightHover
            }
            DragHandler {
                enabled: view.canEdit
                target: null
                xAxis.enabled: true
                yAxis.enabled: false
                onTranslationChanged: subtitleOverlay.rightTrimOffset = Math.max(
                    -((subtitleOverlay.endFrame - subtitleOverlay.startFrame)
                        * view.pixelsPerFrame - 8),
                    translation.x)
                onActiveChanged: if (!active && subtitleOverlay.rightTrimOffset !== 0) {
                    const delta = Math.round(
                        subtitleOverlay.rightTrimOffset / view.pixelsPerFrame);
                    subtitleController.resizeSubtitlePlacement(
                        subtitleOverlay.placementId,
                        subtitleOverlay.startFrame,
                        subtitleOverlay.endFrame + delta,
                        view.pixelsPerFrame,
                        view.playheadFrame,
                        view.snapEnabled);
                    subtitleOverlay.rightTrimOffset = 0;
                }
            }
        }
    }
}
