import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import ".."

Panel {
    id: root
    objectName: "reviewPanel"
    property bool canEdit: false
    property int playheadFrame: 0
    property var threads: []
    signal seekRequested(int frame)
    implicitHeight: content.implicitHeight + 22

    FileDialog {
        id: reviewPackageImport
        title: qsTr("导入审阅包")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("审阅包 (*.mfr *.zip)")]
        onAccepted: mediaflow.timelineReviewController.importPackage(selectedFile.toString())
    }
    FileDialog {
        id: reviewPackageExport
        title: qsTr("导出审阅包")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "mfr"
        nameFilters: [qsTr("审阅包 (*.mfr)")]
        onAccepted: mediaflow.timelineReviewController.exportPackage(selectedFile.toString())
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
            AppSwitch {
                id: reviewMode
                objectName: "reviewModeSwitch"
                checked: mediaflow.timelineReviewController.reviewMode
                onToggled: mediaflow.timelineReviewController.reviewMode = checked
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("审阅 · %1 条待处理").arg(
                    mediaflow.timelineReviewController.openCount)
                color: Theme.text
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
            }
            AppButton {
                text: qsTr("导入")
                compact: true
                enabled: root.canEdit
                onClicked: reviewPackageImport.open()
            }
            AppButton {
                text: qsTr("导出")
                compact: true
                onClicked: reviewPackageExport.open()
            }
            Text {
                visible: mediaflow.timelineReviewController.blockingOpenCount > 0
                text: qsTr("%1 条阻断").arg(
                    mediaflow.timelineReviewController.blockingOpenCount)
                color: Theme.danger
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: reviewMode.checked
            spacing: 7

            RowLayout {
                Layout.fillWidth: true
                AppComboBox {
                    id: reviewFilter
                    objectName: "reviewStatusFilter"
                    Layout.fillWidth: true
                    textRole: "label"
                    valueRole: "value"
                    model: mediaflow.timelineReviewController.filterOptions
                    Component.onCompleted: currentIndex = Math.max(
                        0, indexOfValue(mediaflow.timelineReviewController.statusFilter))
                    onActivated: mediaflow.timelineReviewController.statusFilter = String(currentValue)
                }
                AppComboBox {
                    id: reviewPriority
                    Layout.fillWidth: true
                    textRole: "label"
                    valueRole: "value"
                    model: mediaflow.timelineReviewController.priorityOptions
                }
            }
            PropertyField {
                id: reviewSubject
                Layout.fillWidth: true
                label: qsTr("主题（可选）")
            }
            RowLayout {
                Layout.fillWidth: true
                PropertyField {
                    id: reviewEndFrame
                    Layout.fillWidth: true
                    label: qsTr("结束帧（留空为单点）")
                    placeholderText: qsTr("播放头 %1").arg(root.playheadFrame)
                }
                AppButton {
                    text: qsTr("取当前帧")
                    compact: true
                    onClicked: reviewEndFrame.text = String(root.playheadFrame)
                }
            }
            AppTextArea {
                id: reviewBody
                objectName: "newReviewBody"
                Layout.fillWidth: true
                Layout.preferredHeight: 68
                placeholderText: qsTr("写下需要修改、确认或交付前处理的问题")
                wrapMode: TextEdit.Wrap
            }
            AppButton {
                objectName: "addReviewButton"
                Layout.fillWidth: true
                text: qsTr("在播放头添加批注")
                enabled: root.canEdit && reviewBody.text.trim().length > 0
                onClicked: {
                    const parsedEnd = reviewEndFrame.text.trim().length > 0
                        ? Number(reviewEndFrame.text) : -1;
                    mediaflow.timelineReviewController.addThread(
                        root.playheadFrame, parsedEnd, reviewSubject.text,
                        String(reviewPriority.currentValue || "normal"), reviewBody.text);
                    reviewBody.text = "";
                    reviewSubject.text = "";
                    reviewEndFrame.text = "";
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.threads.length === 0
                text: qsTr("当前筛选下没有批注。归档只会隐藏批注，不会删除内容。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }

            Repeater {
                model: root.threads
                delegate: Rectangle {
                    id: reviewCard
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: threadContent.implicitHeight + 14
                    radius: Theme.radiusSmall
                    color: Theme.surfaceRaised
                    border.color: String(reviewCard.modelData.priority) === "blocking"
                        ? Theme.danger : Theme.borderSubtle

                    ColumnLayout {
                        id: threadContent
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 7
                        spacing: 5

                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: Number(reviewCard.modelData.endFrame) > 0
                                    ? qsTr("帧 %1–%2").arg(
                                        reviewCard.modelData.startFrame).arg(
                                        reviewCard.modelData.endFrame)
                                    : qsTr("帧 %1").arg(reviewCard.modelData.startFrame)
                                compact: true
                                onClicked: root.seekRequested(
                                    Number(reviewCard.modelData.startFrame))
                            }
                            Text {
                                Layout.fillWidth: true
                                text: String(reviewCard.modelData.subject || qsTr("无主题批注"))
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeCaption
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            Text {
                                text: String(reviewCard.modelData.status) === "open"
                                    ? qsTr("待处理")
                                    : String(reviewCard.modelData.status) === "resolved"
                                        ? qsTr("已解决") : qsTr("已归档")
                                color: String(reviewCard.modelData.status) === "open"
                                    ? Theme.warning : Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("绑定项目修订 %1 · 序列修订 %2")
                                .arg(reviewCard.modelData.projectRevision)
                                .arg(reviewCard.modelData.sequenceTimelineRevision)
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: mediaflow.timelineReviewController.snapshotRunning
                                    ? qsTr("截图中…") : qsTr("截取当前帧")
                                compact: true
                                enabled: root.canEdit
                                    && !mediaflow.timelineReviewController.snapshotRunning
                                    && String(reviewCard.modelData.status) !== "archived"
                                onClicked: mediaflow.timelineReviewController.captureSnapshot(
                                    String(reviewCard.modelData.threadId), root.playheadFrame)
                            }
                            AppComboBox {
                                id: markupKind
                                Layout.fillWidth: true
                                textRole: "label"
                                valueRole: "value"
                                model: [
                                    {label: qsTr("自由画笔"), value: "freehand"},
                                    {label: qsTr("箭头"), value: "arrow"},
                                    {label: qsTr("矩形"), value: "rectangle"},
                                    {label: qsTr("椭圆"), value: "ellipse"}
                                ]
                            }
                        }

                        Repeater {
                            model: reviewCard.modelData.snapshots || []
                            delegate: Rectangle {
                                id: snapshotCard
                                required property var modelData
                                property var draftMarkup: modelData.markup || []
                                property var activePoints: []
                                Layout.fillWidth: true
                                implicitHeight: Math.min(260, Math.max(120,
                                    width * Number(modelData.height) / Math.max(1, Number(modelData.width))))
                                color: "#080a0e"
                                radius: Theme.radiusSmall
                                border.color: Theme.borderSubtle
                                clip: true

                                Image {
                                    anchors.fill: parent
                                    source: String(snapshotCard.modelData.fileUrl)
                                    fillMode: Image.Stretch
                                    cache: false
                                }
                                Canvas {
                                    id: markupCanvas
                                    anchors.fill: parent
                                    onPaint: {
                                        const ctx = getContext("2d"); ctx.reset();
                                        const shapes = snapshotCard.draftMarkup.slice();
                                        if (snapshotCard.activePoints.length > 0)
                                            shapes.push({kind: String(markupKind.currentValue || "freehand"), points: snapshotCard.activePoints, color: "#ffcc55", width: 3});
                                        for (const shape of shapes) {
                                            const points = shape.points || [];
                                            if (points.length === 0) continue;
                                            ctx.strokeStyle = String(shape.color || "#ffcc55");
                                            ctx.fillStyle = String(shape.color || "#ffcc55");
                                            ctx.lineWidth = Number(shape.width || 3);
                                            ctx.lineCap = "round"; ctx.lineJoin = "round";
                                            const first = points[0]; const last = points[points.length - 1];
                                            if (String(shape.kind) === "rectangle") {
                                                ctx.strokeRect(Number(first.x) * width, Number(first.y) * height,
                                                    (Number(last.x) - Number(first.x)) * width,
                                                    (Number(last.y) - Number(first.y)) * height);
                                            } else if (String(shape.kind) === "ellipse") {
                                                const x1 = Number(first.x) * width; const y1 = Number(first.y) * height;
                                                const x2 = Number(last.x) * width; const y2 = Number(last.y) * height;
                                                ctx.beginPath(); ctx.ellipse((x1 + x2) / 2, (y1 + y2) / 2,
                                                    Math.abs(x2 - x1) / 2, Math.abs(y2 - y1) / 2, 0, 0, Math.PI * 2); ctx.stroke();
                                            } else {
                                                ctx.beginPath(); ctx.moveTo(Number(first.x) * width, Number(first.y) * height);
                                                for (let index = 1; index < points.length; ++index)
                                                    ctx.lineTo(Number(points[index].x) * width, Number(points[index].y) * height);
                                                ctx.stroke();
                                                if (String(shape.kind) === "arrow" && points.length >= 2) {
                                                    const angle = Math.atan2((Number(last.y) - Number(first.y)) * height,
                                                        (Number(last.x) - Number(first.x)) * width);
                                                    const x = Number(last.x) * width; const y = Number(last.y) * height;
                                                    ctx.beginPath(); ctx.moveTo(x, y);
                                                    ctx.lineTo(x - 14 * Math.cos(angle - 0.45), y - 14 * Math.sin(angle - 0.45));
                                                    ctx.moveTo(x, y); ctx.lineTo(x - 14 * Math.cos(angle + 0.45), y - 14 * Math.sin(angle + 0.45)); ctx.stroke();
                                                }
                                            }
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: root.canEdit && String(reviewCard.modelData.status) !== "archived"
                                        cursorShape: Qt.CrossCursor
                                        onPressed: mouse => {
                                            snapshotCard.activePoints = [{x: mouse.x / width, y: mouse.y / height}];
                                            markupCanvas.requestPaint();
                                        }
                                        onPositionChanged: mouse => {
                                            if (!pressed) return;
                                            const point = {x: Math.max(0, Math.min(1, mouse.x / width)), y: Math.max(0, Math.min(1, mouse.y / height))};
                                            const next = snapshotCard.activePoints.slice();
                                            if (String(markupKind.currentValue) === "freehand") next.push(point);
                                            else if (next.length === 1) next.push(point); else next[next.length - 1] = point;
                                            snapshotCard.activePoints = next; markupCanvas.requestPaint();
                                        }
                                        onReleased: {
                                            if (snapshotCard.activePoints.length < 2) { snapshotCard.activePoints = []; markupCanvas.requestPaint(); return; }
                                            const next = snapshotCard.draftMarkup.slice();
                                            next.push({kind: String(markupKind.currentValue || "freehand"), points: snapshotCard.activePoints,
                                                color: "#ffcc55", width: 3, text: ""});
                                            snapshotCard.draftMarkup = next; snapshotCard.activePoints = [];
                                            mediaflow.timelineReviewController.setSnapshotMarkup(
                                                String(reviewCard.modelData.threadId),
                                                String(snapshotCard.modelData.snapshotId), next);
                                            markupCanvas.requestPaint();
                                        }
                                    }
                                }
                                Text {
                                    anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 6
                                    text: qsTr("截图帧 %1").arg(snapshotCard.modelData.frame)
                                    color: "white"; font.pixelSize: Theme.fontSizeCaption
                                    style: Text.Outline; styleColor: "black"
                                }
                            }
                        }

                        Repeater {
                            model: reviewCard.modelData.messages || []
                            delegate: ColumnLayout {
                                id: reviewMessage
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    text: String(reviewMessage.modelData.author)
                                    color: Theme.textSubtle
                                    font.pixelSize: Theme.fontSizeCaption
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: String(reviewMessage.modelData.body)
                                    color: Theme.text
                                    font.pixelSize: Theme.fontSizeBodySmall
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        AppTextArea {
                            id: replyBody
                            Layout.fillWidth: true
                            Layout.preferredHeight: 52
                            visible: String(reviewCard.modelData.status) === "open"
                            placeholderText: qsTr("回复这条批注")
                            wrapMode: TextEdit.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                visible: String(reviewCard.modelData.status) === "open"
                                text: qsTr("回复")
                                compact: true
                                enabled: root.canEdit && replyBody.text.trim().length > 0
                                onClicked: {
                                    mediaflow.timelineReviewController.replyThread(
                                        String(reviewCard.modelData.threadId), replyBody.text);
                                    replyBody.text = "";
                                }
                            }
                            AppButton {
                                visible: String(reviewCard.modelData.status) === "open"
                                text: qsTr("标记为已解决")
                                compact: true
                                enabled: root.canEdit
                                onClicked: mediaflow.timelineReviewController.resolveThread(
                                    String(reviewCard.modelData.threadId))
                            }
                            AppButton {
                                visible: String(reviewCard.modelData.status) === "resolved"
                                text: qsTr("重新打开")
                                compact: true
                                enabled: root.canEdit
                                onClicked: mediaflow.timelineReviewController.reopenThread(
                                    String(reviewCard.modelData.threadId))
                            }
                            Item { Layout.fillWidth: true }
                            AppButton {
                                visible: String(reviewCard.modelData.status) !== "archived"
                                text: qsTr("归档")
                                compact: true
                                enabled: root.canEdit
                                onClicked: mediaflow.timelineReviewController.archiveThread(
                                    String(reviewCard.modelData.threadId))
                            }
                            AppButton {
                                visible: String(reviewCard.modelData.status) === "archived"
                                text: qsTr("恢复")
                                compact: true
                                enabled: root.canEdit
                                onClicked: mediaflow.timelineReviewController.restoreThread(
                                    String(reviewCard.modelData.threadId))
                            }
                        }
                    }
                }
            }
        }
    }
}
