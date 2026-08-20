import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    objectName: "translationPanel"
    spacing: 10
    property var comparisonData: ({})
    property var taskData: ({})
    property var selectedRowIds: []
    property var translationDrafts: ({})
    property string observedDocumentId: ""
    property int sectionIndex: 0
    property bool showSectionTabs: true
    readonly property bool hasDocuments: sourceDocument.count > 0
    readonly property bool taskActive: taskData.status === "pending"
        || taskData.status === "running" || taskData.status === "paused"
    readonly property bool canEdit: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
    signal modeRequested(string mode)
    signal importRequested

    function selectValue(control, value) {
        for (var index = 0; index < control.model.length; ++index) {
            if (control.model[index].value === value) {
                control.currentIndex = index;
                return;
            }
        }
        control.currentIndex = 0;
    }

    function syncDefaults() {
        root.selectValue(targetLanguage, mediaflow.settingsController.defaultTranslationLanguage);
        root.selectValue(translationMode, mediaflow.settingsController.settingsData.translationMode || "standard");
        root.syncDocumentSelector();
        root.refreshComparison();
    }

    function syncDocumentSelector() {
        const row = mediaflow.subtitleViewController.subtitleDocumentsModel.findRow(
            "documentId", mediaflow.subtitleViewController.selectedDocumentId);
        if (row >= 0)
            sourceDocument.currentIndex = row;
    }

    function translationDraftKey(targetDocumentId, targetSegmentId) {
        return String(targetDocumentId || "") + "\u001f" + String(targetSegmentId || "");
    }

    function translationDraftText(row) {
        const key = translationDraftKey(
            comparisonData.targetDocumentId, row.targetSegmentId);
        return translationDrafts[key] === undefined
            ? String(row.targetText || "") : String(translationDrafts[key]);
    }

    function storeTranslationDraft(targetDocumentId, targetSegmentId, text) {
        if (!targetSegmentId)
            return;
        const next = Object.assign({}, translationDrafts);
        next[translationDraftKey(targetDocumentId, targetSegmentId)] = String(text);
        translationDrafts = next;
    }

    function clearTranslationDraft(targetDocumentId, targetSegmentId) {
        const key = translationDraftKey(targetDocumentId, targetSegmentId);
        if (translationDrafts[key] === undefined)
            return;
        const next = Object.assign({}, translationDrafts);
        delete next[key];
        translationDrafts = next;
    }

    function reconcileTranslationDrafts(data) {
        const rows = data.rows || [];
        const next = Object.assign({}, translationDrafts);
        let changed = false;
        for (let index = 0; index < rows.length; ++index) {
            const row = rows[index];
            const key = translationDraftKey(data.targetDocumentId, row.targetSegmentId);
            if (row.targetSegmentId && next[key] !== undefined
                    && String(next[key]) === String(row.targetText || "")) {
                delete next[key];
                changed = true;
            }
        }
        if (changed)
            translationDrafts = next;
    }

    function refreshComparison() {
        const documentId = String(mediaflow.subtitleViewController.selectedDocumentId || "");
        const refreshed = mediaflow.subtitleTranslationController.translationComparison(
            documentId, String(targetLanguage.currentValue || ""));
        reconcileTranslationDrafts(refreshed);
        comparisonData = refreshed;
        const contextId = String(comparisonData.sourceDocumentId || documentId);
        taskData = mediaflow.taskController.latestTask("translate", contextId);
    }

    function rowSelected(rowId) {
        return selectedRowIds.indexOf(String(rowId)) >= 0;
    }

    function toggleRow(rowId) {
        const key = String(rowId);
        const next = selectedRowIds.slice();
        const index = next.indexOf(key);
        if (index >= 0)
            next.splice(index, 1);
        else
            next.push(key);
        selectedRowIds = next;
    }

    function selectedSourceSegmentIds() {
        const wantedRows = selectedRowIds;
        const rows = comparisonData.rows || [];
        const ids = [];
        for (let rowIndex = 0; rowIndex < rows.length; ++rowIndex) {
            const row = rows[rowIndex];
            if (wantedRows.indexOf(String(row.rowId)) < 0)
                continue;
            const sourceIds = row.sourceSegmentIds || [];
            for (let idIndex = 0; idIndex < sourceIds.length; ++idIndex) {
                const id = String(sourceIds[idIndex]);
                if (ids.indexOf(id) < 0)
                    ids.push(id);
            }
        }
        return ids;
    }

    function loadGlossaryTerm() {
        const data = mediaflow.settingsController.selectedGlossaryTermData;
        termSource.text = data.source || "";
        termTarget.text = data.target || "";
        termNote.text = data.note || "";
        termCategory.text = data.category || "general";
    }

    Component.onCompleted: {
        observedDocumentId = String(mediaflow.subtitleViewController.selectedDocumentId || "");
        syncDefaults();
    }
    Connections {
        target: mediaflow.settingsController
        function onSettingsChanged() {
            root.syncDefaults();
        }
        function onSelectionChanged() {
            root.loadGlossaryTerm();
        }
    }
    Connections {
        target: mediaflow.subtitleViewController
        function onSelectionChanged() {
            const documentId = String(mediaflow.subtitleViewController.selectedDocumentId || "");
            if (documentId !== root.observedDocumentId) {
                root.selectedRowIds = [];
                root.observedDocumentId = documentId;
            }
            root.syncDocumentSelector();
            root.refreshComparison();
        }
        function onProjectStateChanged() { root.refreshComparison(); }
    }
    Connections {
        target: mediaflow.taskController
        function onTasksChanged() { root.refreshComparison(); }
    }

    AppTabBar {
        id: translationTabs
        Layout.fillWidth: true
        visible: root.showSectionTabs
        currentIndex: root.sectionIndex
        onCurrentIndexChanged: {
            if (root.showSectionTabs)
                root.sectionIndex = currentIndex;
        }
        AppTabButton {
            text: qsTr("翻译")
        }
        AppTabButton {
            text: qsTr("术语库")
        }
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: root.sectionIndex

        ColumnLayout {
            spacing: 9

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !root.hasDocuments
                spacing: 8
                EmptyState {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 68
                    iconVisible: false
                    iconName: "translate"
                    title: qsTr("还没有可翻译的字幕")
                    description: qsTr("先识别时间轴声音，或导入已有字幕，再生成译文。")
                }
                AppButton {
                    objectName: "translationStartTranscriptionButton"
                    Layout.fillWidth: true
                primary: true
                text: qsTr("识别时间轴声音")
                enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canStartTasks)
                    onClicked: root.modeRequested("transcript")
                }
                AppButton {
                    objectName: "translationImportFileButton"
                Layout.fillWidth: true
                text: qsTr("导入字幕文件")
                enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canImport)
                    onClicked: root.importRequested()
                }
                Item { Layout.fillHeight: true }
            }

            AppComboBox {
                id: sourceDocument
                objectName: "translationDocumentSelector"
                Layout.fillWidth: true
                visible: root.hasDocuments
                model: mediaflow.subtitleViewController.subtitleDocumentsModel
                textRole: "language"
                valueRole: "documentId"
                onActivated: mediaflow.subtitleViewController.selectSubtitleDocument(String(currentValue))
            }

            RowLayout {
                Layout.fillWidth: true
                visible: root.hasDocuments
                AppComboBox {
                    id: translationMode
                    Layout.fillWidth: true
                    model: mediaflow.subtitleViewController.translationModeOptions
                    textRole: "label"
                    valueRole: "value"
                }
                AppComboBox {
                    id: targetLanguage
                    objectName: "translationTargetLanguage"
                    Layout.fillWidth: true
                    enabled: translationMode.currentValue !== "proofread"
                    model: mediaflow.subtitleViewController.translationLanguageOptions
                    textRole: "label"
                    valueRole: "value"
                    onCurrentValueChanged: root.refreshComparison()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: root.hasDocuments
                AppButton {
                    objectName: "translateWholeDocumentButton"
                    Layout.fillWidth: true
                    primary: true
                    text: translationMode.currentValue === "proofread"
                        ? qsTr("校对整篇") : qsTr("翻译整篇")
                    enabled: root.canEdit && Boolean(root.comparisonData.sourceDocumentId)
                        && !root.taskActive
                        && (translationMode.currentValue === "proofread"
                            || targetLanguage.currentValue.length > 0)
                    onClicked: mediaflow.subtitleTranslationController.translateDocument(
                        root.comparisonData.sourceDocumentId,
                        targetLanguage.currentValue,
                        translationMode.currentValue)
                }
                AppButton {
                    objectName: "translateSelectedRowsButton"
                    text: qsTr("重译所选 %1").arg(root.selectedRowIds.length)
                    enabled: root.canEdit && root.selectedRowIds.length > 0
                        && Boolean(root.comparisonData.targetDocumentId)
                        && !root.taskActive
                    onClicked: mediaflow.subtitleTranslationController.translateComparisonSegments(
                        root.comparisonData.sourceDocumentId,
                        root.comparisonData.targetDocumentId,
                        root.selectedSourceSegmentIds(),
                        targetLanguage.currentValue,
                        translationMode.currentValue)
                }
            }

            ContextTaskCard {
                objectName: "translationTaskPanel"
                Layout.fillWidth: true
                taskData: root.taskData
                fallbackTitle: qsTr("翻译任务")
                visible: root.hasDocuments && Boolean(taskData.taskId)
            }

            Panel {
                objectName: "translationComparisonSummary"
                Layout.fillWidth: true
                implicitHeight: 70
                visible: Boolean(root.comparisonData.sourceDocumentId)
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Text {
                            text: qsTr("%1 → %2").arg(
                                root.comparisonData.sourceLanguage || "—").arg(
                                root.comparisonData.targetLanguage || targetLanguage.currentValue)
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodySmall
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: root.comparisonData.targetDocumentId
                                ? qsTr("双语对照 · %1 行 · 命中 %2 个术语").arg(
                                    (root.comparisonData.rows || []).length).arg(
                                    root.comparisonData.glossaryHitCount || 0)
                                : qsTr("还没有这个语言的译文，先翻译整篇。")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    AppButton {
                        visible: Boolean(root.comparisonData.targetDocumentId)
                        text: qsTr("放入序列")
                        enabled: root.canEdit
                        onClicked: mediaflow.subtitlePlacementController.placeSubtitleDocument(
                            root.comparisonData.targetDocumentId)
                    }
                }
            }

            ListView {
                id: comparisonList
                objectName: "translationComparisonList"
                property var panel: root
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: root.hasDocuments
                clip: true
                spacing: 7
                model: root.comparisonData.rows || []
                delegate: Rectangle {
                    id: comparisonRow
                    required property var modelData
                    width: comparisonList.width
                    height: Math.max(142, comparisonContent.implicitHeight + 18)
                    radius: Theme.radiusSmall
                    color: root.rowSelected(modelData.rowId)
                        ? Theme.accentSoft : Theme.surfaceRaised
                    border.color: modelData.status === "missing"
                        ? Theme.warning : root.rowSelected(modelData.rowId)
                        ? Theme.accent : Theme.border

                    ColumnLayout {
                        id: comparisonContent
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 9
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            AppCheckBox {
                                checked: root.rowSelected(comparisonRow.modelData.rowId)
                                onClicked: root.toggleRow(comparisonRow.modelData.rowId)
                            }
                            Text {
                                Layout.fillWidth: true
                                text: qsTr("%1–%2 帧").arg(
                                    comparisonRow.modelData.startFrame).arg(
                                    comparisonRow.modelData.endFrame)
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                text: comparisonRow.modelData.status === "missing"
                                    ? qsTr("待翻译") : qsTr("已翻译")
                                color: comparisonRow.modelData.status === "missing"
                                    ? Theme.warning : Theme.success
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: comparisonRow.modelData.sourceText || qsTr("（空白源文本）")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodySmall
                            wrapMode: Text.WordWrap
                        }
                        AppTextArea {
                            id: targetEditor
                            objectName: "translationTargetEditor"
                            collaborationPath: "/subtitles/documents/"
                                + root.comparisonData.targetDocumentId
                                + "/segments/"
                                + comparisonRow.modelData.targetSegmentId
                                + "/text"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.max(48, implicitHeight)
                            text: root.translationDraftText(comparisonRow.modelData)
                            placeholderText: qsTr("尚未生成译文")
                            enabled: root.canEdit && Boolean(comparisonRow.modelData.targetSegmentId)
                            onTextChanged: {
                                if (activeFocus)
                                    root.storeTranslationDraft(
                                        root.comparisonData.targetDocumentId,
                                        comparisonRow.modelData.targetSegmentId,
                                        text);
                            }
                            wrapMode: TextEdit.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Item { Layout.fillWidth: true }
                            AppButton {
                                objectName: "translationSaveSegmentButton"
                                visible: Boolean(comparisonRow.modelData.targetSegmentId)
                                text: qsTr("保存译文")
                                enabled: root.canEdit
                                    && targetEditor.text !== comparisonRow.modelData.targetText
                                onClicked: {
                                    const targetDocumentId = String(
                                        comparisonList.panel.comparisonData.targetDocumentId || "");
                                    const targetSegmentId = String(
                                        comparisonRow.modelData.targetSegmentId || "");
                                    if (mediaflow.subtitleTranslationController.updateTranslationSegment(
                                            targetDocumentId, targetSegmentId,
                                            targetEditor.text)) {
                                        comparisonList.panel.clearTranslationDraft(
                                            targetDocumentId, targetSegmentId);
                                        comparisonList.panel.refreshComparison();
                                    }
                                }
                            }
                        }
                    }
                }
                EmptyState {
                    anchors.fill: parent
                    visible: comparisonList.count === 0
                    iconName: "translate"
                    title: mediaflow.subtitleViewController.selectedDocumentId.length > 0
                        ? qsTr("没有可对照的字幕段") : qsTr("还没有字幕文档")
                    description: qsTr("先转录媒体或导入 SRT，再从这里生成译文。")
                }
            }
        }

        ColumnLayout {
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("翻译术语")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                }
                Item {
                    Layout.fillWidth: true
                }
                AppButton {
                    text: qsTr("新建术语")
                    onClicked: mediaflow.settingsController.selectGlossaryTerm("")
                }
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("只有命中源字幕的术语才会随翻译请求发送，并要求模型严格采用指定译法。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
            ListView {
                id: glossaryList
                objectName: "translationGlossaryList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 72
                clip: true
                spacing: 5
                model: mediaflow.settingsController.glossaryTermsModel
                delegate: Rectangle {
                    required property string termId
                    required property string source
                    required property string target
                    required property string category
                    width: glossaryList.width
                    height: 52
                    radius: Theme.radiusSmall
                    color: mediaflow.settingsController.selectedGlossaryTermId === termId ? Theme.accentSoft : termMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: mediaflow.settingsController.selectedGlossaryTermId === termId ? Theme.accent : Theme.border
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        Text {
                            Layout.fillWidth: true
                            text: source + "  →  " + target
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodySmall
                            elide: Text.ElideRight
                        }
                        Text {
                            text: category
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    MouseArea {
                        id: termMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: mediaflow.settingsController.selectGlossaryTerm(termId)
                    }
                }
                EmptyState {
                    anchors.fill: parent
                    visible: glossaryList.count === 0
                    iconName: "transcript"
                    title: qsTr("术语库为空")
                    description: qsTr("添加人名、产品名、缩写和固定译法。")
                }
            }
            Panel {
                Layout.fillWidth: true
                implicitHeight: 226
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 9
                    spacing: 7
                    RowLayout {
                        Layout.fillWidth: true
                        AppTextField {
                            id: termSource
                            Layout.fillWidth: true
                            placeholderText: qsTr("源术语")
                        }
                        AppTextField {
                            id: termTarget
                            Layout.fillWidth: true
                            placeholderText: qsTr("指定译法")
                        }
                    }
                    AppTextField {
                        id: termCategory
                        Layout.fillWidth: true
                        placeholderText: qsTr("分类，例如 product")
                        text: "general"
                    }
                    AppTextField {
                        id: termNote
                        Layout.fillWidth: true
                        placeholderText: qsTr("备注（可选）")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            text: qsTr("移除")
                            enabled: mediaflow.settingsController.selectedGlossaryTermId.length > 0
                            onClicked: mediaflow.settingsController.removeGlossaryTerm(mediaflow.settingsController.selectedGlossaryTermId)
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        AppButton {
                            primary: true
                            text: qsTr("保存术语")
                            enabled: termSource.text.trim().length > 0 && termTarget.text.trim().length > 0
                            onClicked: mediaflow.settingsController.saveGlossaryTerm(mediaflow.settingsController.selectedGlossaryTermId, termSource.text, termTarget.text, termNote.text, termCategory.text)
                        }
                    }
                }
            }
        }
    }
}
