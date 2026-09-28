import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Panel {
    id: root
    objectName: "maskStackPanel"
    property bool canEdit: false
    property var masks: []
    property var shapeOptions: []
    property int playheadFrame: 0
    signal seekRequested(int frame)
    implicitHeight: content.implicitHeight + 22

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
                text: qsTr("蒙版与局部效果")
                color: Theme.text
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
            }
            AppComboBox {
                id: shapeKind
                objectName: "maskShapeKind"
                Layout.preferredWidth: 112
                textRole: "label"
                valueRole: "value"
                model: root.shapeOptions
            }
            AppButton {
                objectName: "addMaskButton"
                text: qsTr("添加")
                compact: true
                enabled: root.canEdit && shapeKind.currentValue
                onClicked: mediaflow.timelineMaskController.addSelectedClipMask(
                    String(shapeKind.currentValue))
            }
        }

        Text {
            Layout.fillWidth: true
            visible: root.masks.length === 0
            text: qsTr("添加蒙版后，可将任一视觉效果限制在蒙版区域内。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: root.masks
            delegate: Rectangle {
                id: maskCard
                required property var modelData
                property real draftCenterX: Number(modelData.centerX)
                property real draftCenterY: Number(modelData.centerY)
                property real draftWidth: Number(modelData.width)
                property real draftHeight: Number(modelData.height)
                property var draftPoints: JSON.parse(String(modelData.pointsJson || "[]"))
                property int dragPoint: -1
                property string dragPart: ""
                Layout.fillWidth: true
                implicitHeight: maskContent.implicitHeight + 14
                radius: Theme.radiusSmall
                color: Theme.surfaceRaised
                border.color: Theme.borderSubtle

                function commitGeometry() {
                    mediaflow.timelineMaskController.updateSelectedClipMask(
                        String(modelData.maskId), maskName.text,
                        maskEnabled.checked, String(maskCombine.currentValue),
                        maskInverted.checked, Number(maskFeather.text),
                        Number(maskFeatherPasses.text), Number(maskOpacity.text),
                        draftCenterX, draftCenterY, draftWidth, draftHeight,
                        Number(maskRotation.text), JSON.stringify(draftPoints));
                }

                function updatePathPoint(index, fieldX, fieldY, x, y) {
                    const next = draftPoints.slice();
                    const point = Object.assign({}, next[index]);
                    point[fieldX] = Math.max(0, Math.min(1, x));
                    point[fieldY] = Math.max(0, Math.min(1, y));
                    next[index] = point;
                    draftPoints = next;
                    maskCanvas.requestPaint();
                }

                ColumnLayout {
                    id: maskContent
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 7
                    spacing: 5

                    RowLayout {
                        Layout.fillWidth: true
                        AppSwitch {
                            id: maskEnabled
                            checked: Boolean(maskCard.modelData.enabled)
                            enabled: root.canEdit
                        }
                        Text {
                            Layout.fillWidth: true
                            text: String(maskCard.modelData.kind) === "bezier"
                                ? qsTr("贝塞尔路径")
                                : String(maskCard.modelData.kind) === "polygon"
                                ? qsTr("多边形")
                                : String(maskCard.modelData.kind) === "rectangle"
                                    ? qsTr("矩形") : qsTr("椭圆")
                            color: Theme.textSubtle
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        AppIconButton {
                            iconName: "chevron-up"
                            enabled: root.canEdit && Number(maskCard.modelData.position) > 0
                            onClicked: mediaflow.timelineMaskController.moveSelectedClipMask(
                                String(maskCard.modelData.maskId), Number(maskCard.modelData.position) - 1)
                            toolTipText: qsTr("上移蒙版")
                        }
                        AppIconButton {
                            iconName: "chevron-down"
                            enabled: root.canEdit && Number(maskCard.modelData.position) + 1 < root.masks.length
                            onClicked: mediaflow.timelineMaskController.moveSelectedClipMask(
                                String(maskCard.modelData.maskId), Number(maskCard.modelData.position) + 1)
                            toolTipText: qsTr("下移蒙版")
                        }
                        AppIconButton {
                            iconName: "delete"
                            danger: true
                            enabled: root.canEdit
                            onClicked: mediaflow.timelineMaskController.removeSelectedClipMask(
                                String(maskCard.modelData.maskId))
                            toolTipText: qsTr("移除蒙版")
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 170
                        color: "#090b10"
                        radius: Theme.radiusSmall
                        border.color: Theme.borderSubtle
                        clip: true

                        Canvas {
                            id: maskCanvas
                            anchors.fill: parent
                            anchors.margins: 8
                            onPaint: {
                                const ctx = getContext("2d");
                                ctx.reset();
                                ctx.strokeStyle = "#70d6ff";
                                ctx.fillStyle = "rgba(112,214,255,0.13)";
                                ctx.lineWidth = 2;
                                const kind = String(maskCard.modelData.kind);
                                const points = maskCard.draftPoints || [];
                                if (kind === "polygon" || kind === "bezier") {
                                    if (points.length < 3) return;
                                    ctx.beginPath();
                                    ctx.moveTo(Number(points[0].x) * width, Number(points[0].y) * height);
                                    for (let index = 1; index <= points.length; ++index) {
                                        const previous = points[index - 1];
                                        const point = points[index % points.length];
                                        if (kind === "bezier") {
                                            ctx.bezierCurveTo(
                                                Number(previous.outgoing_x === undefined || previous.outgoing_x === null ? previous.x : previous.outgoing_x) * width,
                                                Number(previous.outgoing_y === undefined || previous.outgoing_y === null ? previous.y : previous.outgoing_y) * height,
                                                Number(point.incoming_x === undefined || point.incoming_x === null ? point.x : point.incoming_x) * width,
                                                Number(point.incoming_y === undefined || point.incoming_y === null ? point.y : point.incoming_y) * height,
                                                Number(point.x) * width, Number(point.y) * height);
                                        } else {
                                            ctx.lineTo(Number(point.x) * width, Number(point.y) * height);
                                        }
                                    }
                                    ctx.fill();
                                    ctx.stroke();
                                    for (let index = 0; index < points.length; ++index) {
                                        const point = points[index];
                                        if (kind === "bezier") {
                                            const ix = Number(point.incoming_x === undefined || point.incoming_x === null ? point.x : point.incoming_x) * width;
                                            const iy = Number(point.incoming_y === undefined || point.incoming_y === null ? point.y : point.incoming_y) * height;
                                            const ox = Number(point.outgoing_x === undefined || point.outgoing_x === null ? point.x : point.outgoing_x) * width;
                                            const oy = Number(point.outgoing_y === undefined || point.outgoing_y === null ? point.y : point.outgoing_y) * height;
                                            ctx.strokeStyle = "#8d9bad";
                                            ctx.beginPath();
                                            ctx.moveTo(Number(point.x) * width, Number(point.y) * height);
                                            ctx.lineTo(ix, iy); ctx.moveTo(Number(point.x) * width, Number(point.y) * height); ctx.lineTo(ox, oy); ctx.stroke();
                                            ctx.fillStyle = "#ffca70";
                                            ctx.fillRect(ix - 3, iy - 3, 6, 6); ctx.fillRect(ox - 3, oy - 3, 6, 6);
                                        }
                                        ctx.fillStyle = "#70d6ff";
                                        ctx.beginPath(); ctx.arc(Number(point.x) * width, Number(point.y) * height, 5, 0, Math.PI * 2); ctx.fill();
                                    }
                                    return;
                                }
                                const cx = maskCard.draftCenterX * width;
                                const cy = maskCard.draftCenterY * height;
                                const w = maskCard.draftWidth * width;
                                const h = maskCard.draftHeight * height;
                                ctx.save(); ctx.translate(cx, cy); ctx.rotate(Number(maskRotation.text || 0) * Math.PI / 180);
                                ctx.beginPath();
                                if (kind === "ellipse") ctx.ellipse(0, 0, w / 2, h / 2, 0, 0, Math.PI * 2);
                                else ctx.rect(-w / 2, -h / 2, w, h);
                                ctx.fill(); ctx.stroke();
                                ctx.fillStyle = "#70d6ff"; ctx.fillRect(w / 2 - 5, h / 2 - 5, 10, 10); ctx.restore();
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: root.canEdit
                                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.CrossCursor
                                onPressed: mouse => {
                                    maskCard.dragPoint = -1;
                                    maskCard.dragPart = "center";
                                    const kind = String(maskCard.modelData.kind);
                                    const points = maskCard.draftPoints || [];
                                    if (kind === "polygon" || kind === "bezier") {
                                        let best = 12;
                                        for (let index = 0; index < points.length; ++index) {
                                            const point = points[index];
                                            const candidates = [{part: "anchor", x: point.x, y: point.y}];
                                            if (kind === "bezier") {
                                                candidates.push({part: "incoming", x: point.incoming_x ?? point.x, y: point.incoming_y ?? point.y});
                                                candidates.push({part: "outgoing", x: point.outgoing_x ?? point.x, y: point.outgoing_y ?? point.y});
                                            }
                                            for (const candidate of candidates) {
                                                const distance = Math.hypot(mouse.x - Number(candidate.x) * width, mouse.y - Number(candidate.y) * height);
                                                if (distance < best) { best = distance; maskCard.dragPoint = index; maskCard.dragPart = candidate.part; }
                                            }
                                        }
                                    } else {
                                        const cornerX = (maskCard.draftCenterX + maskCard.draftWidth / 2) * width;
                                        const cornerY = (maskCard.draftCenterY + maskCard.draftHeight / 2) * height;
                                        if (Math.hypot(mouse.x - cornerX, mouse.y - cornerY) < 15) maskCard.dragPart = "resize";
                                    }
                                }
                                onPositionChanged: mouse => {
                                    if (!pressed) return;
                                    const x = Math.max(0, Math.min(1, mouse.x / width));
                                    const y = Math.max(0, Math.min(1, mouse.y / height));
                                    if (maskCard.dragPoint >= 0) {
                                        const part = maskCard.dragPart;
                                        maskCard.updatePathPoint(maskCard.dragPoint,
                                            part === "anchor" ? "x" : part + "_x",
                                            part === "anchor" ? "y" : part + "_y", x, y);
                                    } else if (maskCard.dragPart === "resize") {
                                        maskCard.draftWidth = Math.max(0.01, Math.min(2, (x - maskCard.draftCenterX) * 2));
                                        maskCard.draftHeight = Math.max(0.01, Math.min(2, (y - maskCard.draftCenterY) * 2));
                                        maskCanvas.requestPaint();
                                    } else {
                                        maskCard.draftCenterX = x; maskCard.draftCenterY = y; maskCanvas.requestPaint();
                                    }
                                }
                                onReleased: maskCard.commitGeometry()
                            }
                        }
                    }

                    PropertyField {
                        id: maskName
                        Layout.fillWidth: true
                        label: qsTr("名称")
                        text: String(maskCard.modelData.name)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppComboBox {
                            id: maskCombine
                            Layout.preferredWidth: 112
                            textRole: "label"
                            valueRole: "value"
                            model: mediaflow.timelineMaskController.combineOptions
                            currentIndex: Math.max(0, indexOfValue(String(maskCard.modelData.combineMode || "replace")))
                        }
                        AppSwitch {
                            id: maskInverted
                            text: qsTr("反转")
                            checked: Boolean(maskCard.modelData.inverted)
                            enabled: root.canEdit
                        }
                        PropertyField {
                            id: maskOpacity
                            Layout.fillWidth: true
                            label: qsTr("不透明度")
                            text: String(maskCard.modelData.opacity)
                        }
                        PropertyField {
                            id: maskFeather
                            Layout.fillWidth: true
                            label: qsTr("羽化像素")
                            text: String(maskCard.modelData.feather)
                        }
                        PropertyField {
                            id: maskFeatherPasses
                            Layout.fillWidth: true
                            label: qsTr("羽化次数")
                            text: String(maskCard.modelData.featherPasses)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        PropertyField {
                            id: maskCenterX
                            Layout.fillWidth: true
                            label: qsTr("中心 X")
                            text: String(maskCard.draftCenterX)
                        }
                        PropertyField {
                            id: maskCenterY
                            Layout.fillWidth: true
                            label: qsTr("中心 Y")
                            text: String(maskCard.draftCenterY)
                        }
                        PropertyField {
                            id: maskWidth
                            Layout.fillWidth: true
                            label: qsTr("宽度")
                            text: String(maskCard.draftWidth)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        PropertyField {
                            id: maskHeight
                            Layout.fillWidth: true
                            label: qsTr("高度")
                            text: String(maskCard.draftHeight)
                        }
                        PropertyField {
                            id: maskRotation
                            Layout.fillWidth: true
                            label: qsTr("旋转")
                            text: String(maskCard.modelData.rotation)
                        }
                        AppButton {
                            text: qsTr("保存蒙版")
                            compact: true
                            enabled: root.canEdit
                            onClicked: mediaflow.timelineMaskController.updateSelectedClipMask(
                                String(maskCard.modelData.maskId), maskName.text,
                                maskEnabled.checked, String(maskCombine.currentValue), maskInverted.checked,
                                Number(maskFeather.text), Number(maskFeatherPasses.text),
                                Number(maskOpacity.text), maskCard.draftCenterX,
                                maskCard.draftCenterY, maskCard.draftWidth,
                                maskCard.draftHeight, Number(maskRotation.text),
                                maskPoints.text)
                        }
                    }
                    PropertyField {
                        id: maskPoints
                        Layout.fillWidth: true
                        visible: String(maskCard.modelData.kind) === "polygon"
                            || String(maskCard.modelData.kind) === "bezier"
                        label: qsTr("路径点（高级 JSON）")
                        text: JSON.stringify(maskCard.draftPoints)
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("%1 个蒙版关键帧").arg(
                                (maskCard.modelData.keyframes || []).length)
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        AppButton {
                            text: qsTr("在播放头保存")
                            compact: true
                            enabled: root.canEdit
                                && root.playheadFrame >= Number(
                                    mediaflow.timelineViewController.selectedClipData.startFrame || 0)
                                && root.playheadFrame < Number(
                                    mediaflow.timelineViewController.selectedClipData.endFrame || 0)
                            onClicked: mediaflow.timelineMaskController.setMaskKeyframe(
                                String(maskCard.modelData.maskId), root.playheadFrame,
                                Number(maskCenterX.text), Number(maskCenterY.text),
                                Number(maskWidth.text), Number(maskHeight.text),
                                Number(maskRotation.text), maskPoints.text,
                                "linear", 0.25, 0.1, 0.25, 1)
                        }
                        AppButton {
                            objectName: "trackMaskButton"
                            text: qsTr("跟踪")
                            compact: true
                            enabled: root.canEdit
                            onClicked: mediaflow.timelineMaskController.trackSelectedMask(
                                String(maskCard.modelData.maskId))
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: (maskCard.modelData.keyframes || []).length > 1
                        PropertyField {
                            id: maskRetimeScale
                            Layout.fillWidth: true
                            label: qsTr("全部时间缩放")
                            text: "1"
                        }
                        AppButton {
                            text: qsTr("应用")
                            compact: true
                            enabled: root.canEdit
                            onClicked: {
                                const values = maskCard.modelData.keyframes || [];
                                const offsets = values.map(item => Number(item.timelineOffset));
                                mediaflow.timelineMaskController.retimeMaskKeyframes(
                                    String(maskCard.modelData.maskId), offsets,
                                    offsets[0], Number(maskRetimeScale.text));
                            }
                        }
                    }
                    Repeater {
                        model: maskCard.modelData.keyframes || []
                        delegate: RowLayout {
                            id: maskKeyframeRow
                            required property var modelData
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                text: qsTr("片段帧 %1 · %2").arg(
                                    maskKeyframeRow.modelData.timelineOffset).arg(
                                    String(maskKeyframeRow.modelData.source) === "subject_tracking"
                                        ? qsTr("跟踪") : qsTr("手动"))
                                color: Theme.textSubtle
                                font.pixelSize: Theme.fontSizeCaption
                                font.family: Theme.monoFontFamily
                            }
                            AppIconButton {
                                iconName: "minus"
                                enabled: root.canEdit
                                    && Number(maskKeyframeRow.modelData.timelineOffset) > 0
                                onClicked: mediaflow.timelineMaskController.moveMaskKeyframe(
                                    String(maskCard.modelData.maskId),
                                    Number(maskKeyframeRow.modelData.timelineOffset),
                                    Number(maskKeyframeRow.modelData.timelineOffset) - 1)
                                toolTipText: qsTr("向前移动一帧")
                            }
                            AppIconButton {
                                iconName: "add"
                                enabled: root.canEdit
                                    && Number(maskKeyframeRow.modelData.timelineOffset) + 1
                                        < Number(mediaflow.timelineViewController.selectedClipData.durationFrames || 0)
                                onClicked: mediaflow.timelineMaskController.moveMaskKeyframe(
                                    String(maskCard.modelData.maskId),
                                    Number(maskKeyframeRow.modelData.timelineOffset),
                                    Number(maskKeyframeRow.modelData.timelineOffset) + 1)
                                toolTipText: qsTr("向后移动一帧")
                            }
                            AppIconButton {
                                iconName: "play"
                                onClicked: root.seekRequested(
                                    Number(maskKeyframeRow.modelData.timelineFrame))
                                toolTipText: qsTr("跳到关键帧")
                            }
                            AppIconButton {
                                iconName: "delete"
                                danger: true
                                enabled: root.canEdit
                                onClicked: mediaflow.timelineMaskController.removeMaskKeyframe(
                                    String(maskCard.modelData.maskId),
                                    Number(maskKeyframeRow.modelData.timelineOffset))
                                toolTipText: qsTr("移除蒙版关键帧")
                            }
                        }
                    }
                }
            }
        }
    }
}
