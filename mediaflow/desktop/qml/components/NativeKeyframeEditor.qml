import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Panel {
    id: root
    objectName: "nativeKeyframeEditor"
    property bool canEdit: false
    property string clipId: ""
    property int clipStartFrame: 0
    property int clipDuration: 0
    property int playheadFrame: 0
    property real transformX: 0
    property real transformY: 0
    property real transformScaleX: 1
    property real transformScaleY: 1
    property real transformRotation: 0
    property real transformCropLeft: 0
    property real transformCropTop: 0
    property real transformCropRight: 0
    property real transformCropBottom: 0
    property real transformOpacity: 1
    property var keyframes: mediaflow.timelineKeyframeController.selectedTransformKeyframes
    property var selectedOffsets: []
    property string graphDragMode: ""
    property int graphDragIndex: -1
    property real graphDragStartX: 0
    property real graphDragStartY: 0
    property real graphDragX: 0
    property real graphDragY: 0
    signal seekRequested(int frame)
    implicitHeight: content.implicitHeight + 22

    readonly property var channelOptions: [
        {label: "X %", value: "x"},
        {label: "Y %", value: "y"},
        {label: qsTr("横向缩放"), value: "scale_x"},
        {label: qsTr("纵向缩放"), value: "scale_y"},
        {label: qsTr("旋转"), value: "rotation"},
        {label: qsTr("透明度"), value: "opacity"}
    ]

    function curveValue(curve, name, fallback) {
        return curve && curve[name] !== undefined ? Number(curve[name]) : fallback;
    }

    function interpolationControls(kind, curve) {
        if (kind === "ease_in") return [0.42, 0, 1, 1];
        if (kind === "ease_out") return [0, 0, 0.58, 1];
        if (kind === "ease_in_out") return [0.42, 0, 0.58, 1];
        if (kind === "linear") return [0, 0, 1, 1];
        return [root.curveValue(curve, "x1", 0.25),
                root.curveValue(curve, "y1", 0.1),
                root.curveValue(curve, "x2", 0.25),
                root.curveValue(curve, "y2", 1)];
    }

    function toggleOffset(offset, selected) {
        let next = selectedOffsets.slice();
        const index = next.indexOf(offset);
        if (selected && index < 0)
            next.push(offset);
        else if (!selected && index >= 0)
            next.splice(index, 1);
        next.sort((left, right) => left - right);
        selectedOffsets = next;
    }

    function setRowCurve(row, interpolation) {
        const curve = row.curve || {};
        mediaflow.timelineKeyframeController.setTransformKeyframe(
            root.clipId, Number(row.timelineFrame),
            Number(row.x), Number(row.y), Number(row.scale_x), Number(row.scale_y),
            Number(row.rotation), Number(row.crop_left), Number(row.crop_top),
            Number(row.crop_right), Number(row.crop_bottom), Number(row.opacity),
            String(interpolation), root.curveValue(curve, "x1", 0.25),
            root.curveValue(curve, "y1", 0.1), root.curveValue(curve, "x2", 0.25),
            root.curveValue(curve, "y2", 1));
    }

    function channelBounds(channel) {
        if (keyframes.length === 0) return [-0.5, 0.5];
        let minimum = Number(keyframes[0][channel]);
        let maximum = minimum;
        for (let index = 1; index < keyframes.length; ++index) {
            const value = Number(keyframes[index][channel]);
            minimum = Math.min(minimum, value); maximum = Math.max(maximum, value);
        }
        if (Math.abs(maximum - minimum) < 0.000001) {
            minimum -= 0.5; maximum += 0.5;
        }
        const padding = (maximum - minimum) * 0.08;
        return [minimum - padding, maximum + padding];
    }

    function graphPointX(offset, width) {
        return 8 + Number(offset) / Math.max(1, clipDuration - 1) * (width - 16);
    }

    function graphPointY(value, height, bounds) {
        return height - 8 - (Number(value) - bounds[0]) / (bounds[1] - bounds[0]) * (height - 16);
    }

    function selectedRow() {
        if (selectedOffsets.length !== 1) return null;
        const offset = Number(selectedOffsets[0]);
        for (const row of keyframes)
            if (Number(row.timelineOffset) === offset) return row;
        return null;
    }

    function applyBezierFields() {
        const row = selectedRow();
        if (!row) return;
        mediaflow.timelineKeyframeController.setTransformKeyframeCurve(
            clipId, Number(row.timelineOffset), Number(bezierX1.text),
            Number(bezierY1.text), Number(bezierX2.text), Number(bezierY2.text));
    }

    onKeyframesChanged: {
        const available = keyframes.map(item => Number(item.timelineOffset));
        selectedOffsets = selectedOffsets.filter(item => available.indexOf(item) >= 0);
        curveCanvas.requestPaint();
    }

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: qsTr("画面关键帧与曲线")
                color: Theme.text
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
            }
            Text {
                text: qsTr("%1 个").arg(root.keyframes.length)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
        }

        RowLayout {
            Layout.fillWidth: true
            AppComboBox {
                id: interpolation
                Layout.fillWidth: true
                textRole: "label"
                valueRole: "value"
                model: mediaflow.timelineKeyframeController.interpolationOptions
                currentIndex: 1
            }
            AppButton {
                objectName: "setTransformKeyframeButton"
                text: qsTr("在播放头保存")
                primary: true
                enabled: root.canEdit && root.playheadFrame >= root.clipStartFrame
                    && root.playheadFrame < root.clipStartFrame + root.clipDuration
                onClicked: mediaflow.timelineKeyframeController.setTransformKeyframe(
                    root.clipId, root.playheadFrame,
                    root.transformX, root.transformY, root.transformScaleX,
                    root.transformScaleY, root.transformRotation,
                    root.transformCropLeft, root.transformCropTop,
                    root.transformCropRight, root.transformCropBottom,
                    root.transformOpacity, String(interpolation.currentValue),
                    Number(bezierX1.text), Number(bezierY1.text),
                    Number(bezierX2.text), Number(bezierY2.text))
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 4
            visible: interpolation.currentValue === "bezier" || root.selectedOffsets.length === 1
            columnSpacing: 6
            PropertyField { id: bezierX1; Layout.fillWidth: true; label: "X1"; text: "0.25" }
            PropertyField { id: bezierY1; Layout.fillWidth: true; label: "Y1"; text: "0.1" }
            PropertyField { id: bezierX2; Layout.fillWidth: true; label: "X2"; text: "0.25" }
            PropertyField { id: bezierY2; Layout.fillWidth: true; label: "Y2"; text: "1" }
            AppButton {
                Layout.columnSpan: 4
                Layout.alignment: Qt.AlignRight
                text: qsTr("应用到所选曲线段")
                compact: true
                enabled: root.canEdit && root.selectedOffsets.length === 1
                onClicked: root.applyBezierFields()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            AppComboBox {
                id: graphChannel
                Layout.preferredWidth: 146
                textRole: "label"
                valueRole: "value"
                model: root.channelOptions
                onActivated: curveCanvas.requestPaint()
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("曲线按片段本地时间显示；贝塞尔控制点与导出使用同一合同。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
        }

        Canvas {
            id: curveCanvas
            objectName: "nativeKeyframeCurveCanvas"
            Layout.fillWidth: true
            Layout.preferredHeight: 132
            antialiasing: true
            onWidthChanged: requestPaint()
            onPaint: {
                const context = getContext("2d");
                context.reset();
                context.fillStyle = Theme.field;
                context.fillRect(0, 0, width, height);
                context.strokeStyle = Theme.border;
                context.lineWidth = 1;
                context.strokeRect(0.5, 0.5, width - 1, height - 1);
                if (root.keyframes.length === 0)
                    return;
                const channel = String(graphChannel.currentValue || "x");
                const bounds = root.channelBounds(channel);
                const px = frame => root.graphPointX(frame, width);
                const py = value => root.graphPointY(value, height, bounds);
                context.strokeStyle = Theme.accent;
                context.lineWidth = 2;
                context.beginPath();
                const first = root.keyframes[0];
                context.moveTo(px(Number(first.timelineOffset)), py(Number(first[channel])));
                for (let index = 0; index + 1 < root.keyframes.length; ++index) {
                    const start = root.keyframes[index];
                    const end = root.keyframes[index + 1];
                    const x0 = px(Number(start.timelineOffset));
                    const x3 = px(Number(end.timelineOffset));
                    const y0 = py(Number(start[channel]));
                    const y3 = py(Number(end[channel]));
                    const kind = String((start.curve || {}).interpolation || "linear");
                    if (kind === "hold") {
                        context.lineTo(x3, y0);
                        context.lineTo(x3, y3);
                    } else {
                        const controls = root.interpolationControls(kind, start.curve || {});
                        context.bezierCurveTo(
                            x0 + (x3 - x0) * controls[0],
                            y0 + (y3 - y0) * controls[1],
                            x0 + (x3 - x0) * controls[2],
                            y0 + (y3 - y0) * controls[3],
                            x3, y3);
                    }
                }
                context.stroke();
                context.fillStyle = Theme.accentHover;
                for (let index = 0; index < root.keyframes.length; ++index) {
                    const row = root.keyframes[index];
                    context.fillStyle = root.selectedOffsets.indexOf(Number(row.timelineOffset)) >= 0
                        ? Theme.warning : Theme.accentHover;
                    context.beginPath();
                    context.arc(px(Number(row.timelineOffset)), py(Number(row[channel])), 5, 0, Math.PI * 2);
                    context.fill();
                }
                if (root.selectedOffsets.length === 1) {
                    const selected = root.selectedRow();
                    const selectedIndex = root.keyframes.indexOf(selected);
                    if (selected && selectedIndex >= 0 && selectedIndex + 1 < root.keyframes.length) {
                        const next = root.keyframes[selectedIndex + 1];
                        const controls = root.interpolationControls(String((selected.curve || {}).interpolation || "linear"), selected.curve || {});
                        const x0 = px(selected.timelineOffset); const x3 = px(next.timelineOffset);
                        const y0 = py(selected[channel]); const y3 = py(next[channel]);
                        const hx1 = x0 + (x3 - x0) * controls[0]; const hy1 = y0 + (y3 - y0) * controls[1];
                        const hx2 = x0 + (x3 - x0) * controls[2]; const hy2 = y0 + (y3 - y0) * controls[3];
                        context.strokeStyle = Theme.textMuted; context.lineWidth = 1;
                        context.beginPath(); context.moveTo(x0, y0); context.lineTo(hx1, hy1);
                        context.moveTo(x3, y3); context.lineTo(hx2, hy2); context.stroke();
                        context.fillStyle = Theme.warning;
                        context.fillRect(hx1 - 4, hy1 - 4, 8, 8); context.fillRect(hx2 - 4, hy2 - 4, 8, 8);
                    }
                }
                if (root.graphDragMode === "select") {
                    context.strokeStyle = Theme.warning; context.lineWidth = 1;
                    context.setLineDash([4, 3]);
                    context.strokeRect(Math.min(root.graphDragStartX, root.graphDragX),
                        Math.min(root.graphDragStartY, root.graphDragY),
                        Math.abs(root.graphDragX - root.graphDragStartX),
                        Math.abs(root.graphDragY - root.graphDragStartY));
                    context.setLineDash([]);
                }
            }
            MouseArea {
                anchors.fill: parent
                enabled: root.canEdit && root.keyframes.length > 0
                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.CrossCursor
                onPressed: mouse => {
                    const channel = String(graphChannel.currentValue || "x");
                    const bounds = root.channelBounds(channel);
                    root.graphDragStartX = mouse.x; root.graphDragStartY = mouse.y;
                    root.graphDragX = mouse.x; root.graphDragY = mouse.y;
                    root.graphDragIndex = -1; root.graphDragMode = "select";
                    const selected = root.selectedRow();
                    const selectedIndex = root.keyframes.indexOf(selected);
                    if (selected && selectedIndex >= 0 && selectedIndex + 1 < root.keyframes.length) {
                        const next = root.keyframes[selectedIndex + 1];
                        const controls = root.interpolationControls(String((selected.curve || {}).interpolation || "linear"), selected.curve || {});
                        const x0 = root.graphPointX(selected.timelineOffset, width); const x3 = root.graphPointX(next.timelineOffset, width);
                        const y0 = root.graphPointY(selected[channel], height, bounds); const y3 = root.graphPointY(next[channel], height, bounds);
                        const handles = [
                            {mode: "handle1", x: x0 + (x3 - x0) * controls[0], y: y0 + (y3 - y0) * controls[1]},
                            {mode: "handle2", x: x0 + (x3 - x0) * controls[2], y: y0 + (y3 - y0) * controls[3]}
                        ];
                        for (const handle of handles)
                            if (Math.hypot(mouse.x - handle.x, mouse.y - handle.y) <= 10) root.graphDragMode = handle.mode;
                    }
                    if (root.graphDragMode === "select") {
                        let closest = 10;
                        for (let index = 0; index < root.keyframes.length; ++index) {
                            const row = root.keyframes[index];
                            const distance = Math.hypot(mouse.x - root.graphPointX(row.timelineOffset, width),
                                mouse.y - root.graphPointY(row[channel], height, bounds));
                            if (distance < closest) { closest = distance; root.graphDragIndex = index; root.graphDragMode = "point"; }
                        }
                        if (root.graphDragMode === "point") {
                            const offset = Number(root.keyframes[root.graphDragIndex].timelineOffset);
                            if (!(mouse.modifiers & Qt.ControlModifier)) root.selectedOffsets = [offset];
                            else root.toggleOffset(offset, root.selectedOffsets.indexOf(offset) < 0);
                        }
                    }
                    curveCanvas.requestPaint();
                }
                onPositionChanged: mouse => {
                    if (!pressed) return;
                    root.graphDragX = Math.max(8, Math.min(width - 8, mouse.x));
                    root.graphDragY = mouse.y;
                    curveCanvas.requestPaint();
                }
                onReleased: mouse => {
                    const channel = String(graphChannel.currentValue || "x");
                    const bounds = root.channelBounds(channel);
                    if (root.graphDragMode === "point" && root.graphDragIndex >= 0) {
                        const row = root.keyframes[root.graphDragIndex];
                        const offset = Math.max(0, Math.min(root.clipDuration - 1,
                            Math.round((root.graphDragX - 8) / Math.max(1, width - 16) * Math.max(1, root.clipDuration - 1))));
                        const value = bounds[1] - (root.graphDragY - 8) / Math.max(1, height - 16) * (bounds[1] - bounds[0]);
                        mediaflow.timelineKeyframeController.setTransformKeyframeChannel(
                            root.clipId, Number(row.timelineOffset), offset, channel, value);
                        root.selectedOffsets = [offset];
                    } else if (root.graphDragMode === "handle1" || root.graphDragMode === "handle2") {
                        const row = root.selectedRow(); const index = root.keyframes.indexOf(row); const next = root.keyframes[index + 1];
                        const x0 = root.graphPointX(row.timelineOffset, width); const x3 = root.graphPointX(next.timelineOffset, width);
                        const y0 = root.graphPointY(row[channel], height, bounds); const y3 = root.graphPointY(next[channel], height, bounds);
                        const controls = root.interpolationControls(String((row.curve || {}).interpolation || "linear"), row.curve || {});
                        const controlX = Math.max(0, Math.min(1, (root.graphDragX - x0) / Math.max(1, x3 - x0)));
                        const controlY = (root.graphDragY - y0) / ((Math.abs(y3 - y0) < 0.001) ? 1 : (y3 - y0));
                        if (root.graphDragMode === "handle1") { controls[0] = controlX; controls[1] = controlY; }
                        else { controls[2] = controlX; controls[3] = controlY; }
                        mediaflow.timelineKeyframeController.setTransformKeyframeCurve(
                            root.clipId, Number(row.timelineOffset), controls[0], controls[1], controls[2], controls[3]);
                    } else if (root.graphDragMode === "select") {
                        const left = Math.min(root.graphDragStartX, root.graphDragX); const right = Math.max(root.graphDragStartX, root.graphDragX);
                        const top = Math.min(root.graphDragStartY, root.graphDragY); const bottom = Math.max(root.graphDragStartY, root.graphDragY);
                        const selected = [];
                        for (const row of root.keyframes) {
                            const x = root.graphPointX(row.timelineOffset, width); const y = root.graphPointY(row[channel], height, bounds);
                            if (x >= left && x <= right && y >= top && y <= bottom) selected.push(Number(row.timelineOffset));
                        }
                        root.selectedOffsets = selected;
                    }
                    root.graphDragMode = ""; root.graphDragIndex = -1; curveCanvas.requestPaint();
                }
            }
        }

        Repeater {
            model: root.keyframes
            delegate: Rectangle {
                id: keyframeCard
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: row.implicitHeight + 10
                radius: Theme.radiusSmall
                color: root.selectedOffsets.indexOf(Number(modelData.timelineOffset)) >= 0
                    ? Theme.selectionSoft : Theme.surfaceRaised
                border.color: Theme.borderSubtle
                RowLayout {
                    id: row
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 5
                    spacing: 5
                    AppCheckBox {
                        checked: root.selectedOffsets.indexOf(Number(keyframeCard.modelData.timelineOffset)) >= 0
                        onToggled: root.toggleOffset(Number(keyframeCard.modelData.timelineOffset), checked)
                    }
                    Text {
                        text: qsTr("片段帧 %1").arg(keyframeCard.modelData.timelineOffset)
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeCaption
                        font.family: Theme.monoFontFamily
                    }
                    AppComboBox {
                        Layout.fillWidth: true
                        textRole: "label"
                        valueRole: "value"
                        model: mediaflow.timelineKeyframeController.interpolationOptions
                        Component.onCompleted: currentIndex = Math.max(0,
                            indexOfValue(String((keyframeCard.modelData.curve || {}).interpolation || "linear")))
                        onActivated: root.setRowCurve(keyframeCard.modelData, currentValue)
                    }
                    AppIconButton {
                        iconName: "minus"
                        enabled: root.canEdit && Number(keyframeCard.modelData.timelineOffset) > 0
                        onClicked: mediaflow.timelineKeyframeController.moveTransformKeyframe(
                            root.clipId, Number(keyframeCard.modelData.timelineOffset),
                            Number(keyframeCard.modelData.timelineOffset) - 1)
                        toolTipText: qsTr("向前移动一帧")
                    }
                    AppIconButton {
                        iconName: "add"
                        enabled: root.canEdit && Number(keyframeCard.modelData.timelineOffset) + 1 < root.clipDuration
                        onClicked: mediaflow.timelineKeyframeController.moveTransformKeyframe(
                            root.clipId, Number(keyframeCard.modelData.timelineOffset),
                            Number(keyframeCard.modelData.timelineOffset) + 1)
                        toolTipText: qsTr("向后移动一帧")
                    }
                    AppIconButton {
                        iconName: "play"
                        onClicked: root.seekRequested(Number(keyframeCard.modelData.timelineFrame))
                        toolTipText: qsTr("跳到关键帧")
                    }
                    AppIconButton {
                        iconName: "delete"
                        danger: true
                        enabled: root.canEdit
                        onClicked: mediaflow.timelineKeyframeController.removeTransformKeyframe(
                            root.clipId, Number(keyframeCard.modelData.timelineOffset))
                        toolTipText: qsTr("移除关键帧")
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.keyframes.length > 0
            AppButton {
                text: qsTr("全选")
                compact: true
                onClicked: root.selectedOffsets = root.keyframes.map(item => Number(item.timelineOffset))
            }
            AppButton {
                text: qsTr("复制")
                compact: true
                enabled: root.selectedOffsets.length > 0
                onClicked: mediaflow.timelineKeyframeController.copyTransformKeyframes(
                    root.clipId, root.selectedOffsets)
            }
            AppButton {
                text: qsTr("粘贴到播放头")
                compact: true
                enabled: root.canEdit
                onClicked: mediaflow.timelineKeyframeController.pasteTransformKeyframes(
                    root.clipId, root.playheadFrame, Number(pasteScale.text))
            }
            PropertyField {
                id: pasteScale
                Layout.preferredWidth: 78
                label: qsTr("时间倍率")
                text: "1"
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.selectedOffsets.length > 0
            PropertyField {
                id: retimeAnchor
                Layout.fillWidth: true
                label: qsTr("缩放锚点（片段帧）")
                text: root.selectedOffsets.length > 0 ? String(root.selectedOffsets[0]) : "0"
            }
            PropertyField {
                id: retimeScale
                Layout.fillWidth: true
                label: qsTr("缩放倍率")
                text: "1"
            }
            AppButton {
                text: qsTr("应用时间缩放")
                enabled: root.canEdit
                onClicked: mediaflow.timelineKeyframeController.retimeTransformKeyframes(
                    root.clipId, root.selectedOffsets,
                    Number(retimeAnchor.text), Number(retimeScale.text))
            }
        }
    }
}
