import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    objectName: "translationPanel"
    spacing: 10
    readonly property var comparisonData:
        mediaflow.subtitleTranslationController.comparisonData
    readonly property var taskData:
        mediaflow.subtitleTranslationController.taskData
    readonly property var selectedRowIds:
        mediaflow.subtitleTranslationController.selectedRowIds
    property int sectionIndex: 0
    property bool showSectionTabs: true
    readonly property bool hasDocuments: sourceDocument.count > 0
    readonly property bool taskActive: taskData.status === "pending"
        || taskData.status === "running" || taskData.status === "paused"
    readonly property bool canEdit: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
    readonly property bool modalOpen: removeGlossaryDialog.opened
    signal modeRequested(string mode)
    signal importRequested

    AppConfirmationDialog {
        id: removeGlossaryDialog
        onConfirmed: function (termId) {
            if (termId.length > 0)
                mediaflow.languageSettingsController.removeGlossaryTerm(termId)
        }
    }

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

    function refreshComparison() {
        const documentId = String(mediaflow.subtitleViewController.selectedDocumentId || "");
        mediaflow.subtitleTranslationController.refreshComparison(
            documentId, String(targetLanguage.currentValue || ""));
    }

    function loadGlossaryTerm() {
        const data = mediaflow.languageSettingsController.selectedGlossaryTermData;
        termSource.text = data.source || "";
        termTarget.text = data.target || "";
        termNote.text = data.note || "";
        termCategory.text = data.category || "general";
    }

    Component.onCompleted: {
        syncDefaults();
    }
    Connections {
        target: mediaflow.settingsController
        function onSettingsChanged() {
            root.syncDefaults();
        }
    }
    Connections {
        target: mediaflow.languageSettingsController
        function onSelectionChanged() {
            root.loadGlossaryTerm();
        }
    }
    Connections {
        target: mediaflow.subtitleViewController
        function onSelectionChanged() {
            root.syncDocumentSelector();
            root.refreshComparison();
        }
        function onProjectStateChanged() { root.refreshComparison(); }
    }
    Connections {
        target: mediaflow.taskController
        function onTasksChanged() {
            mediaflow.subtitleTranslationController.refreshTaskData();
        }
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
                    color: mediaflow.subtitleTranslationController.rowSelected(modelData.rowId)
                        ? Theme.accentSoft : Theme.surfaceRaised
                    border.color: modelData.status === "missing"
                        ? Theme.warning
                        : mediaflow.subtitleTranslationController.rowSelected(modelData.rowId)
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
                                checked: mediaflow.subtitleTranslationController.rowSelected(
                                    comparisonRow.modelData.rowId)
                                onClicked: mediaflow.subtitleTranslationController.toggleRow(
                                    comparisonRow.modelData.rowId)
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
                            text: comparisonRow.modelData.draftText
                            placeholderText: qsTr("尚未生成译文")
                            enabled: root.canEdit && Boolean(comparisonRow.modelData.targetSegmentId)
                            onTextChanged: {
                                if (activeFocus)
                                    mediaflow.subtitleTranslationController.storeTranslationDraft(
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
                                onClicked:
                                    mediaflow.subtitleTranslationController.saveTranslationSegment(
                                        String(comparisonRow.modelData.targetSegmentId || ""))
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
                    onClicked: mediaflow.languageSettingsController.selectGlossaryTerm("")
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
                model: mediaflow.languageSettingsController.glossaryTermsModel
                delegate: Rectangle {
                    required property string termId
                    required property string source
                    required property string target
                    required property string category
                    width: glossaryList.width
                    height: 52
                    radius: Theme.radiusSmall
                    color: mediaflow.languageSettingsController.selectedGlossaryTermId === termId ? Theme.accentSoft : termMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: mediaflow.languageSettingsController.selectedGlossaryTermId === termId ? Theme.accent : Theme.border
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
                        onClicked: mediaflow.languageSettingsController.selectGlossaryTerm(termId)
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
                            enabled: mediaflow.languageSettingsController.selectedGlossaryTermId.length > 0
                            onClicked: removeGlossaryDialog.request(
                                mediaflow.languageSettingsController.selectedGlossaryTermId,
                                qsTr("移除这个术语？"),
                                qsTr("术语及其翻译规则会永久移除，无法撤销。"),
                                qsTr("永久移除"))
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        AppButton {
                            primary: true
                            text: qsTr("保存术语")
                            enabled: termSource.text.trim().length > 0 && termTarget.text.trim().length > 0
                            onClicked: mediaflow.languageSettingsController.saveGlossaryTerm(mediaflow.languageSettingsController.selectedGlossaryTermId, termSource.text, termTarget.text, termNote.text, termCategory.text)
                        }
                    }
                }
            }
        }
    }
}
