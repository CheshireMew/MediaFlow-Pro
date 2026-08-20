import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

AppScrollView {
    id: subtitleScroll
    objectName: "subtitlePanel"
    clip: true
    contentWidth: availableWidth
    property int playheadFrame: 0
    property bool playbackActive: false
    signal seekRequested(int frame)
    signal modeRequested(string mode)
    signal importRequested

    function formatTimecode(frame) {
        const numerator = Math.max(1, Number(mediaflow.workspaceViewController.profileFpsNumerator || 30));
        const denominator = Math.max(1, Number(mediaflow.workspaceViewController.profileFpsDenominator || 1));
        const nominalFps = Math.max(1, Math.round(numerator / denominator));
        const value = Math.max(0, Number(frame));
        const totalSeconds = Math.floor(value / nominalFps);
        const frames = Math.floor(value % nominalFps);
        const seconds = totalSeconds % 60;
        const minutes = Math.floor(totalSeconds / 60) % 60;
        const hours = Math.floor(totalSeconds / 3600);
        function pad(number) { return String(number).padStart(2, "0"); }
        return pad(hours) + ":" + pad(minutes) + ":" + pad(seconds) + ":" + pad(frames);
    }

    onPlayheadFrameChanged: {
        if (playbackActive)
            mediaflow.subtitleViewController.followSubtitleAtFrame(playheadFrame);
    }

    Connections {
        target: mediaflow.subtitleViewController
        function onSelectionChanged() {
            const row = mediaflow.subtitleViewController.subtitleSegmentsModel.findRow(
                "segmentId", mediaflow.subtitleViewController.selectedSubtitleSegmentId);
            if (row >= 0)
                segmentList.positionViewAtIndex(row, ListView.Contain);
        }
    }

    ColumnLayout {
        id: root
        width: subtitleScroll.availableWidth
        spacing: 9
        property bool showSearch: false
        property var searchMatches: []
        property int searchMatchIndex: -1
        property var segmentDrafts: ({})
        property bool loadingSegmentDraft: false
        property string loadedSegmentId: ""
        readonly property bool canEdit: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)

        function segmentDraftKey(segmentId) {
            return String(mediaflow.subtitleViewController.selectedDocumentId || "") + "\u001f" + String(segmentId || "");
        }

        function storeSelectedSegmentDraft() {
            const segmentId = String(mediaflow.subtitleViewController.selectedSubtitleSegmentId || "");
            if (loadingSegmentDraft || segmentId.length === 0 || loadedSegmentId !== segmentId)
                return;
            const next = Object.assign({}, segmentDrafts);
            next[segmentDraftKey(segmentId)] = {
                "startFrame": segmentStart.value,
                "endFrame": segmentEnd.value,
                "text": segmentText.text
            };
            segmentDrafts = next;
        }

        function clearSelectedSegmentDraft(segmentId) {
            const key = segmentDraftKey(segmentId);
            if (segmentDrafts[key] === undefined)
                return;
            const next = Object.assign({}, segmentDrafts);
            delete next[key];
            segmentDrafts = next;
        }

        function refreshSearch() {
            root.searchMatches = mediaflow.subtitleEditingController.findSubtitleMatches(findText.text, matchCase.checked);
            if (root.searchMatches.length === 0)
                root.searchMatchIndex = -1;
            else if (root.searchMatchIndex < 0 || root.searchMatchIndex >= root.searchMatches.length)
                root.searchMatchIndex = 0;
        }

        function activateSearchMatch(index) {
            if (root.searchMatches.length === 0)
                return;
            const count = root.searchMatches.length;
            root.searchMatchIndex = ((index % count) + count) % count;
            mediaflow.subtitleViewController.selectSubtitleSegment(String(root.searchMatches[root.searchMatchIndex].segmentId), false);
        }

        function loadSelectedSegment() {
            const segmentId = String(mediaflow.subtitleViewController.selectedSubtitleSegmentId || "");
            const data = mediaflow.subtitleViewController.selectedSubtitleSegmentData;
            const draft = segmentDrafts[segmentDraftKey(segmentId)];
            loadingSegmentDraft = true;
            loadedSegmentId = segmentId;
            segmentStart.value = Number(draft ? draft.startFrame : data.startFrame || 0);
            segmentEnd.value = Number(draft ? draft.endFrame : data.endFrame || 1);
            segmentText.text = draft ? String(draft.text || "") : String(data.text || "");
            loadingSegmentDraft = false;
        }

        Component.onCompleted: Qt.callLater(loadSelectedSegment)

        Connections {
            target: mediaflow.subtitleViewController
            function onSelectionChanged() {
                root.loadSelectedSegment();
            }
        }

        FileDialog {
            id: exportSubtitleDialog
            title: qsTr("导出字幕文档")
            fileMode: FileDialog.SaveFile
            nameFilters: [qsTr("SRT 字幕 (*.srt)")]
            onAccepted: mediaflow.subtitleEditingController.exportSubtitleDocument(mediaflow.subtitleViewController.selectedDocumentId, selectedFile.toString())
        }

        Text {
            text: qsTr("字幕文档")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }
        ListView {
            id: documentList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(116, Math.max(52, contentHeight))
            visible: count > 0
            clip: true
            spacing: 5
            model: mediaflow.subtitleViewController.subtitleDocumentsModel
            delegate: Rectangle {
                required property string documentId
                required property string language
                required property bool isSource
                required property int segmentCount
                width: documentList.width
                height: 48
                radius: Theme.radiusSmall
                color: mediaflow.subtitleViewController.selectedDocumentId === documentId ? Theme.accentSoft : docMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                border.color: mediaflow.subtitleViewController.selectedDocumentId === documentId ? Theme.accent : Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 7
                    Text {
                        text: language
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: isSource ? qsTr("源字幕") : qsTr("翻译")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Text {
                        text: segmentCount + qsTr(" 条")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }

                MouseArea {
                    id: docMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: mediaflow.subtitleViewController.selectSubtitleDocument(documentId)
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: documentList.count === 0
            spacing: 8
            EmptyState {
                Layout.fillWidth: true
                Layout.preferredHeight: 120
                iconName: "subtitle"
                title: qsTr("还没有字幕文档")
                description: qsTr("识别当前时间轴的声音，或导入已有的 SRT、WebVTT、ASS 字幕。")
            }
            AppButton {
                objectName: "subtitleStartTranscriptionButton"
                Layout.fillWidth: true
                primary: true
                text: qsTr("识别时间轴声音")
                enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canStartTasks)
                onClicked: subtitleScroll.modeRequested("transcript")
            }
            AppButton {
                objectName: "subtitleImportFileButton"
                Layout.fillWidth: true
                text: qsTr("导入字幕文件")
                enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canImport)
                onClicked: subtitleScroll.importRequested()
            }
        }

        AppTabBar {
            id: subtitleTabs
            Layout.fillWidth: true
            visible: documentList.count > 0
            AppTabButton {
                text: qsTr("文档编辑")
            }
            AppTabButton {
                text: qsTr("序列字幕")
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: currentIndex === 0 ? documentEditor.implicitHeight : sequenceEditor.implicitHeight
            currentIndex: subtitleTabs.currentIndex
            visible: documentList.count > 0

            ColumnLayout {
                id: documentEditor
                spacing: 7

                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        text: qsTr("添加")
                        enabled: root.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
                        onClicked: mediaflow.subtitleEditingController.addSubtitleSegment()
                    }
                    AppButton {
                        text: qsTr("合并")
                        enabled: root.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length >= 2
                        onClicked: mediaflow.subtitleEditingController.mergeSelectedSubtitleSegments()
                    }
                    AppButton {
                        text: qsTr("删除")
                        enabled: root.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
                        onClicked: mediaflow.subtitleEditingController.deleteSelectedSubtitleSegments()
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    AppMenuButton {
                        id: subtitleMoreButton
                        text: qsTr("更多")
                        onClicked: subtitleMoreMenu.open()
                        AppMenu {
                            id: subtitleMoreMenu
                            y: subtitleMoreButton.height + 4
                            AppMenuItem {
                                text: root.showSearch ? qsTr("关闭查找替换") : qsTr("查找替换")
                                onTriggered: root.showSearch = !root.showSearch
                            }
                            AppMenuItem {
                                text: qsTr("导出 SRT")
                                enabled: mediaflow.subtitleViewController.selectedDocumentId.length > 0
                                onTriggered: exportSubtitleDialog.open()
                            }
                            AppMenuSeparator {}
                            AppMenuItem {
                                text: qsTr("翻译所选")
                                enabled: root.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
                                onTriggered: mediaflow.subtitleTranslationController.translateSelectedSubtitleSegments()
                            }
                            AppMenuItem {
                                text: qsTr("复制 SRT")
                                enabled: root.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
                                onTriggered: mediaflow.subtitleEditingController.copySelectedSubtitleSegments()
                            }
                            AppMenuItem {
                                text: qsTr("粘贴替换")
                                enabled: root.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
                                onTriggered: mediaflow.subtitleEditingController.pasteReplaceSelectedSubtitleSegments()
                            }
                            AppMenuItem {
                                text: qsTr("打开文件夹")
                                enabled: mediaflow.subtitleViewController.selectedDocumentId.length > 0
                                onTriggered: mediaflow.subtitleEditingController.openSubtitleFolder()
                            }
                        }
                    }
                }

                Panel {
                    Layout.fillWidth: true
                    implicitHeight: root.showSearch ? 152 : 0
                    visible: root.showSearch
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            AppTextField {
                                id: findText
                                Layout.fillWidth: true
                                placeholderText: qsTr("查找")
                                onTextChanged: root.refreshSearch()
                            }
                            AppTextField {
                                id: replaceText
                                Layout.fillWidth: true
                                placeholderText: qsTr("替换为")
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppCheckBox {
                                id: matchCase
                                text: qsTr("区分大小写")
                                onToggled: root.refreshSearch()
                            }
                            Text {
                                text: qsTr("找到 %1 处").arg(root.searchMatches.length)
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Item {
                                Layout.fillWidth: true
                            }
                            AppButton {
                                text: qsTr("全部替换")
                                primary: true
                                enabled: root.canEdit && findText.text.length > 0 && root.searchMatches.length > 0
                                onClicked: {
                                    mediaflow.subtitleEditingController.replaceSubtitleText(findText.text, replaceText.text, matchCase.checked);
                                    root.searchMatches = mediaflow.subtitleEditingController.findSubtitleMatches(findText.text, matchCase.checked);
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: qsTr("上一个")
                                enabled: root.searchMatches.length > 0
                                onClicked: root.activateSearchMatch(root.searchMatchIndex - 1)
                            }
                            AppButton {
                                text: qsTr("下一个")
                                enabled: root.searchMatches.length > 0
                                onClicked: root.activateSearchMatch(root.searchMatchIndex + 1)
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.searchMatchIndex >= 0 ? qsTr("当前 %1 / %2").arg(root.searchMatchIndex + 1).arg(root.searchMatches.length) : qsTr("没有当前匹配")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            AppButton {
                                text: qsTr("替换当前")
                                primary: true
                                enabled: root.canEdit && root.searchMatchIndex >= 0
                                onClicked: {
                                    const match = root.searchMatches[root.searchMatchIndex];
                                    mediaflow.subtitleEditingController.replaceSubtitleMatch(String(match.segmentId), Number(match.start), Number(match.end), findText.text, replaceText.text, matchCase.checked);
                                    Qt.callLater(function () {
                                        root.refreshSearch();
                                        root.activateSearchMatch(root.searchMatchIndex);
                                    });
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: qsTr("字幕段")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 1 ? qsTr("已选 %1 条").arg(mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length) : ""
                        color: Theme.accentHover
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Text {
                        text: qsTr("长度阈值")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppSpinBox {
                        id: smartSplitLimit
                        Layout.preferredWidth: 90
                        from: 1
                        to: 200
                        value: 24
                        editable: true
                    }
                    AppButton {
                        text: qsTr("智能拆分")
                        enabled: root.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
                        onClicked: mediaflow.subtitleEditingController.smartSplitSubtitleDocument(smartSplitLimit.value)
                    }
                    AppButton {
                        text: qsTr("修复重叠")
                        enabled: root.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
                        onClicked: mediaflow.subtitleEditingController.fixSubtitleOverlaps()
                    }
                }

                ListView {
                    id: segmentList
                    objectName: "subtitleSegmentList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(160, Math.min(360, contentHeight))
                    clip: true
                    spacing: 5
                    model: mediaflow.subtitleViewController.subtitleSegmentsModel
                    delegate: Rectangle {
                        required property string segmentId
                        required property int startFrame
                        required property int endFrame
                        required property string text
                        required property bool hasOverlap
                        width: segmentList.width
                        height: segmentPreviewText.implicitHeight + 31
                        radius: Theme.radiusSmall
                        color: mediaflow.subtitleViewController.isSubtitleSegmentSelected(segmentId) ? Theme.accentSoft : segmentMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                        border.color: hasOverlap ? Theme.danger : mediaflow.subtitleViewController.isSubtitleSegmentSelected(segmentId) ? Theme.accent : Theme.border
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 3
                            Text {
                                text: subtitleScroll.formatTimecode(startFrame)
                                    + " – " + subtitleScroll.formatTimecode(endFrame)
                                color: hasOverlap ? Theme.danger : Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                font.family: Theme.monoFontFamily
                            }
                            Text {
                                id: segmentPreviewText
                                Layout.fillWidth: true
                                text: parent.parent.text
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                        MouseArea {
                            id: segmentMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: function (mouse) {
                                if (mouse.button === Qt.RightButton) {
                                    if (!mediaflow.subtitleViewController.isSubtitleSegmentSelected(segmentId))
                                        mediaflow.subtitleViewController.selectSubtitleSegment(segmentId, false);
                                    segmentContextMenu.popup();
                                    return;
                                }
                                mediaflow.subtitleViewController.selectSubtitleSegment(
                                    segmentId,
                                    (mouse.modifiers & Qt.ControlModifier) !== 0
                                );
                                subtitleScroll.seekRequested(
                                    mediaflow.subtitleViewController.subtitleSegmentTimelineFrame(
                                        segmentId, startFrame));
                            }
                            onDoubleClicked: mediaflow.subtitleViewController.previewSubtitleSegment(segmentId)
                        }
                        AppMenu {
                            id: segmentContextMenu
                            AppMenuItem {
                                text: qsTr("播放这一条")
                                onTriggered: mediaflow.subtitleViewController.previewSubtitleSegment(segmentId)
                            }
                            AppMenuSeparator {}
                            AppMenuItem {
                                text: qsTr("翻译所选字幕")
                                enabled: root.canEdit
                                onTriggered: mediaflow.subtitleTranslationController.translateSelectedSubtitleSegments()
                            }
                            AppMenuItem {
                                text: qsTr("复制所选字幕")
                                onTriggered: mediaflow.subtitleEditingController.copySelectedSubtitleSegments()
                            }
                            AppMenuItem {
                                text: qsTr("合并所选字幕")
                                enabled: root.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 1
                                onTriggered: mediaflow.subtitleEditingController.mergeSelectedSubtitleSegments()
                            }
                            AppMenuItem {
                                text: qsTr("按中点拆分")
                                enabled: root.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length === 1
                                onTriggered: mediaflow.subtitleEditingController.splitSubtitleSegment(segmentId, -1)
                            }
                            AppMenuSeparator {}
                            AppMenuItem {
                                text: qsTr("删除所选字幕")
                                enabled: root.canEdit
                                onTriggered: mediaflow.subtitleEditingController.deleteSelectedSubtitleSegments()
                            }
                        }
                    }
                    EmptyState {
                        anchors.fill: parent
                        visible: segmentList.count === 0
                        iconName: "subtitle"
                        title: qsTr("还没有字幕")
                        description: qsTr("转录媒体或导入 SRT 后，可以在这里逐条编辑。")
                    }
                }

                Panel {
                    Layout.fillWidth: true
                    implicitHeight: 214
                    visible: mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length === 1
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: qsTr("开始帧")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            AppSpinBox {
                                id: segmentStart
                                objectName: "subtitleSegmentStartEditor"
                                Layout.fillWidth: true
                                from: 0
                                to: 2147483647
                                editable: true
                                enabled: root.canEdit
                                onValueModified: root.storeSelectedSegmentDraft()
                            }
                            Text {
                                text: qsTr("结束帧")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            AppSpinBox {
                                id: segmentEnd
                                objectName: "subtitleSegmentEndEditor"
                                Layout.fillWidth: true
                                from: 1
                                to: 2147483647
                                editable: true
                                enabled: root.canEdit
                                onValueModified: root.storeSelectedSegmentDraft()
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                Layout.fillWidth: true
                                text: qsTr("播放头设为开始")
                                enabled: root.canEdit
                                onClicked: {
                                    segmentStart.value = Math.min(
                                        subtitleScroll.playheadFrame,
                                        segmentEnd.value - 1);
                                    root.storeSelectedSegmentDraft();
                                }
                            }
                            AppButton {
                                Layout.fillWidth: true
                                text: qsTr("播放头设为结束")
                                enabled: root.canEdit
                                onClicked: {
                                    segmentEnd.value = Math.max(
                                        subtitleScroll.playheadFrame,
                                        segmentStart.value + 1);
                                    root.storeSelectedSegmentDraft();
                                }
                            }
                        }
                        AppTextArea {
                            id: segmentText
                            objectName: "subtitleSegmentTextEditor"
                            collaborationPath: "/subtitles/documents/"
                                + mediaflow.subtitleViewController.selectedDocumentId
                                + "/segments/"
                                + mediaflow.subtitleViewController.selectedSubtitleSegmentId
                                + "/text"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            wrapMode: TextEdit.Wrap
                            readOnly: !root.canEdit
                            onTextChanged: root.storeSelectedSegmentDraft()
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                Layout.fillWidth: true
                                text: qsTr("按中点拆分")
                                enabled: root.canEdit
                                onClicked: mediaflow.subtitleEditingController.splitSubtitleSegment(mediaflow.subtitleViewController.selectedSubtitleSegmentId, -1)
                            }
                            AppButton {
                                Layout.fillWidth: true
                                primary: true
                                objectName: "subtitleSegmentSaveButton"
                                text: qsTr("保存修改")
                                enabled: root.canEdit
                                onClicked: {
                                    const segmentId = String(mediaflow.subtitleViewController.selectedSubtitleSegmentId || "");
                                    if (mediaflow.subtitleEditingController.updateSubtitleSegment(
                                            segmentId, segmentStart.value,
                                            segmentEnd.value, segmentText.text)) {
                                        root.clearSelectedSegmentDraft(segmentId);
                                        Qt.callLater(root.loadSelectedSegment);
                                    }
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                id: sequenceEditor
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: qsTr("序列字幕")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: Font.DemiBold
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    AppButton {
                        text: qsTr("放入当前序列")
                        primary: true
                        enabled: root.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
                        onClicked: mediaflow.subtitlePlacementController.placeSubtitleDocument(mediaflow.subtitleViewController.selectedDocumentId)
                    }
                }
                ListView {
                    id: placementList
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(160, Math.min(360, contentHeight))
                    clip: true
                    spacing: 4
                    model: mediaflow.subtitleViewController.subtitlePlacementsModel
                    delegate: Rectangle {
                        required property string placementId
                        required property int startFrame
                        required property int endFrame
                        required property string text
                        required property bool hasOverride
                        width: placementList.width
                        height: 48
                        radius: Theme.radiusSmall
                        color: mediaflow.subtitleViewController.selectedSubtitlePlacementId === placementId ? Theme.accentSoft : placementMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                        border.color: hasOverride ? Theme.accent : Theme.border
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 7
                            Text {
                                text: startFrame + "–" + endFrame
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                Layout.fillWidth: true
                                text: parent.parent.text
                                color: Theme.text
                                elide: Text.ElideRight
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                visible: hasOverride
                                text: qsTr("序列覆盖")
                                color: Theme.accentHover
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                        MouseArea {
                            id: placementMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: mediaflow.subtitleViewController.selectSubtitlePlacement(placementId)
                        }
                    }
                    EmptyState {
                        anchors.fill: parent
                        visible: placementList.count === 0
                        iconName: "subtitle"
                        title: qsTr("序列中还没有字幕")
                        description: qsTr("选择字幕文档并放入当前序列。")
                    }
                }
                Panel {
                    Layout.fillWidth: true
                    implicitHeight: 152
                    visible: mediaflow.subtitleViewController.selectedSubtitlePlacementId.length > 0
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 6
                        AppTextArea {
                            id: placementText
                            collaborationPath: "/subtitles/placements/"
                                + mediaflow.subtitleViewController.selectedSubtitlePlacementId
                                + "/text"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            text: mediaflow.subtitleViewController.selectedSubtitlePlacementData.text || ""
                            wrapMode: TextEdit.Wrap
                            readOnly: !root.canEdit
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                Layout.fillWidth: true
                                primary: true
                                text: qsTr("保存为序列覆盖")
                                enabled: root.canEdit
                                onClicked: mediaflow.subtitlePlacementController.updateSubtitlePlacementText(mediaflow.subtitleViewController.selectedSubtitlePlacementId, placementText.text, false)
                            }
                            AppButton {
                                Layout.fillWidth: true
                                text: qsTr("应用到文档")
                                enabled: root.canEdit
                                onClicked: mediaflow.subtitlePlacementController.updateSubtitlePlacementText(mediaflow.subtitleViewController.selectedSubtitlePlacementId, placementText.text, true)
                            }
                        }
                    }
                }
            }
        }
    }
}
