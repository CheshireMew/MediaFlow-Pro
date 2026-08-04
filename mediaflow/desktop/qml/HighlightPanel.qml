import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

AppScrollView {
    id: root
    objectName: "highlightPanel"
    clip: true
    contentWidth: availableWidth
    property int playheadFrame: 0
    property var taskData: ({})
    property bool manualCandidateOpen: false
    readonly property bool taskActive: taskData.status === "pending"
        || taskData.status === "running" || taskData.status === "paused"
    readonly property bool canEdit: Boolean(workspaceController.actionCapabilities.canEdit)
    readonly property bool canStartTasks: Boolean(workspaceController.actionCapabilities.canStartTasks)

    function syncDocumentSelector() {
        const documentId = String(subtitleController.selectedDocumentId || "");
        const row = subtitleController.subtitleDocumentsModel.findRow(
            "documentId", documentId);
        if (row >= 0) {
            sourceDocument.currentIndex = row;
        } else if (sourceDocument.count > 0 && documentId.length === 0) {
            sourceDocument.currentIndex = 0;
            subtitleController.selectSubtitleDocument(
                String(sourceDocument.currentValue || ""));
        }
    }

    function refreshTask() {
        const analysis = taskController.latestCommandTask(
            "analyze_highlights", subtitleController.selectedDocumentId);
        const exporting = taskController.latestCommandTask(
            "export_highlights", workspaceController.activeSequenceId);
        const analysisActive = analysis.status === "pending" || analysis.status === "running"
            || analysis.status === "paused";
        const exportActive = exporting.status === "pending" || exporting.status === "running"
            || exporting.status === "paused";
        taskData = analysisActive ? analysis : exportActive ? exporting
            : Number(analysis.createdAt || 0) >= Number(exporting.createdAt || 0)
            ? analysis : exporting;
    }

    Connections {
        target: taskController
        function onTasksChanged() { root.refreshTask(); }
    }
    Connections {
        target: subtitleController
        function onSelectionChanged() {
            root.syncDocumentSelector();
            root.refreshTask();
        }
    }
    Component.onCompleted: Qt.callLater(function () {
        root.syncDocumentSelector();
        root.refreshTask();
    })

    ColumnLayout {
        width: root.availableWidth
        spacing: 10

        FolderDialog {
            id: batchExportFolder
            title: qsTr("选择批量导出文件夹")
            onAccepted: {
                if (root.canStartTasks)
                    highlightController.exportSelectedHighlights(selectedFolder.toString());
            }
        }
        RowLayout {
            objectName: "highlightToolbar"
            Layout.fillWidth: true
            spacing: 6
            AppComboBox {
                id: sourceDocument
                objectName: "highlightSourceDocument"
                Layout.fillWidth: true
                model: subtitleController.subtitleDocumentsModel
                textRole: "language"
                valueRole: "documentId"
                displayText: count > 0 ? currentText : qsTr("需要先生成字幕")
                onActivated: subtitleController.selectSubtitleDocument(currentValue)
                onCountChanged: Qt.callLater(root.syncDocumentSelector)
            }
            AppButton {
                objectName: "analyzeHighlightsButton"
                text: qsTr("分析")
                primary: true
                enabled: root.canStartTasks
                    && String(sourceDocument.currentValue || "").length > 0
                    && !root.taskActive
                onClicked: highlightController.analyzeHighlights(
                    String(sourceDocument.currentValue || ""))
            }
        }
    Text {
        visible: sourceDocument.count === 0
        Layout.fillWidth: true
        text: qsTr("AI 分析需要字幕；没有字幕时仍可手动添加候选。")
        color: Theme.warning
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }
    Text {
        Layout.fillWidth: true
        text: qsTr("候选区间保存在项目中，可直接生成独立的 9:16 短视频序列。")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }
    ContextTaskCard {
        objectName: "highlightTaskPanel"
        Layout.fillWidth: true
        taskData: root.taskData
        fallbackTitle: qsTr("高光任务")
    }
    AppButton {
        Layout.fillWidth: true
        checkable: true
        checked: root.manualCandidateOpen
        text: root.manualCandidateOpen ? qsTr("收起手动添加") : qsTr("手动添加候选")
        onClicked: root.manualCandidateOpen = checked
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 154
        visible: root.manualCandidateOpen
        enabled: root.canEdit
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            Text {
                text: qsTr("添加手动候选")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            AppTextField {
                id: manualTitle
                Layout.fillWidth: true
                placeholderText: qsTr("片段标题（可选）")
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("播放头→开始")
                    onClicked: manualStart.value = Math.min(
                        root.playheadFrame, manualEnd.value - 1)
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("播放头→结束")
                    onClicked: manualEnd.value = Math.max(
                        root.playheadFrame, manualStart.value + 1)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("开始帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: manualStart
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, Number(mediaController.selectedAssetData.durationFrames || 2147483647))
                    editable: true
                }
                Text {
                    text: qsTr("结束帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: manualEnd
                    Layout.fillWidth: true
                    from: 1
                    to: Math.max(1, Number(mediaController.selectedAssetData.durationFrames || 2147483647))
                    value: Math.max(1, Math.min(to, 450))
                    editable: true
                }
                AppButton {
                    text: qsTr("添加候选")
                    enabled: root.canEdit && mediaController.selectedAssetId.length > 0 && manualEnd.value > manualStart.value
                    onClicked: highlightController.addManualHighlight(manualStart.value, manualEnd.value, manualTitle.text)
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        visible: highlightList.count > 0
        AppButton {
            Layout.fillWidth: true
            text: qsTr("生成短视频")
            enabled: root.canEdit && highlightList.count > 0
            onClicked: highlightController.createAllHighlightShorts()
        }
        AppButton {
            Layout.fillWidth: true
            primary: true
            text: qsTr("快速导出")
            enabled: root.canStartTasks && highlightList.count > 0 && !root.taskActive
            onClicked: highlightController.exportSelectedHighlightsToDefaultLocation()
        }
        AppButton {
            text: qsTr("另存为…")
            enabled: root.canStartTasks && highlightList.count > 0 && !root.taskActive
            onClicked: batchExportFolder.open()
        }
    }
    Text {
        Layout.fillWidth: true
        visible: highlightList.count > 0
        text: qsTr("快速导出沿用当前序列已保存的编码、分辨率、字幕样式、水印和音频设置；每个候选片段单独输出。")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }
    ListView {
        id: highlightList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.max(240, Math.min(520, contentHeight))
        clip: true
        spacing: 8
        model: highlightController.highlightsModel
        delegate: Rectangle {
            required property string highlightId
            required property string sequenceId
            required property string sourceSequenceId
            required property int startFrame
            required property int endFrame
            required property string title
            required property string reason
            required property real score
            required property bool selected
            width: highlightList.width
            height: highlightBody.implicitHeight + 24
            radius: Theme.radius
            opacity: selected ? 1.0 : 0.55
            color: highlightController.selectedHighlightId === highlightId ? Theme.accentSoft : highlightMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: highlightController.selectedHighlightId === highlightId ? Theme.accent : Theme.border
            ColumnLayout {
                id: highlightBody
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 11
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: title
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Text {
                        text: Math.round(score * 100) + "%"
                        color: Theme.accentHover
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
                Text {
                    text: startFrame + " – " + endFrame
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    font.family: Theme.monoFontFamily
                }
                Text {
                    Layout.fillWidth: true
                    text: reason
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        Layout.fillWidth: true
                        text: qsTr("预览")
                        onClicked: highlightController.previewHighlight(highlightId)
                    }
                    AppButton {
                        Layout.fillWidth: true
                        visible: sourceSequenceId.length === 0
                        text: qsTr("添加到主序列")
                        enabled: root.canEdit
                        onClicked: highlightController.addHighlightToMainSequence(highlightId)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        Layout.fillWidth: true
                        text: selected ? qsTr("已纳入导出") : qsTr("纳入导出")
                        enabled: root.canEdit
                        onClicked: highlightController.setHighlightSelected(highlightId, !selected)
                    }
                    AppButton {
                        text: qsTr("删除")
                        enabled: root.canEdit
                        onClicked: highlightController.deleteHighlight(highlightId)
                    }
                }
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: sequenceId.length > 0 ? qsTr("打开短视频序列") : qsTr("创建短视频序列")
                    enabled: sequenceId.length > 0 || root.canEdit
                    onClicked: {
                        if (sequenceId.length > 0)
                            workspaceController.selectSequence(sequenceId);
                        else
                            highlightController.createShortFromHighlight(highlightId);
                    }
                }
            }
            MouseArea {
                id: highlightMouse
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                propagateComposedEvents: true
                onClicked: {
                    highlightController.selectHighlight(highlightId);
                    mouse.accepted = false;
                }
            }
        }
        EmptyState {
            anchors.fill: parent
            visible: highlightList.count === 0
            iconName: "highlight"
            title: qsTr("还没有高光候选")
            description: qsTr("选择字幕文档并运行分析。候选结果会显示在这里。")
        }
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 132
        visible: highlightController.selectedHighlightId.length > 0
        enabled: root.canEdit
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            Text {
                text: qsTr("编辑候选")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            AppTextField {
                id: editTitle
                collaborationPath: "/highlights/"
                    + highlightController.selectedHighlightId + "/title"
                Layout.fillWidth: true
                text: highlightController.selectedHighlightData.title || ""
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("开始帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: editStart
                    Layout.fillWidth: true
                    from: 0
                    to: 2147483647
                    value: Number(highlightController.selectedHighlightData.startFrame || 0)
                    editable: true
                }
                Text {
                    text: qsTr("结束帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: editEnd
                    Layout.fillWidth: true
                    from: 1
                    to: 2147483647
                    value: Number(highlightController.selectedHighlightData.endFrame || 1)
                    editable: true
                }
                AppButton {
                    text: qsTr("保存候选")
                    primary: true
                    onClicked: highlightController.updateHighlight(highlightController.selectedHighlightId, editStart.value, editEnd.value, editTitle.text)
                }
            }
        }
    }
    }
}
