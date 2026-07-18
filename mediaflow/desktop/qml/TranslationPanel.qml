import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    objectName: "translationPanel"
    spacing: 10

    function selectValue(control, value) {
        for (var index = 0; index < control.model.length; ++index) {
            if (control.model[index].value === value) {
                control.currentIndex = index
                return
            }
        }
        control.currentIndex = 0
    }

    function syncDefaults() {
        root.selectValue(targetLanguage, settingsController.defaultTranslationLanguage)
        root.selectValue(translationMode, settingsController.settingsData.translationMode || "standard")
    }

    function loadGlossaryTerm() {
        const data = settingsController.selectedGlossaryTermData
        termSource.text = data.source || ""
        termTarget.text = data.target || ""
        termNote.text = data.note || ""
        termCategory.text = data.category || "general"
    }

    Component.onCompleted: syncDefaults()
    Connections {
        target: settingsController
        function onSettingsChanged() { root.syncDefaults() }
        function onSelectionChanged() { root.loadGlossaryTerm() }
    }

    Text {
        text: qsTr("翻译与校对")
        color: Theme.text
        font.pixelSize: Theme.fontSizeSection
        font.weight: Font.DemiBold
    }

    TabBar {
        id: translationTabs
        Layout.fillWidth: true
        TabButton { text: qsTr("翻译") }
        TabButton { text: qsTr("术语库") }
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: translationTabs.currentIndex

        ColumnLayout {
            spacing: 9
            Text {
                Layout.fillWidth: true
                text: qsTr("标准模式严格保持分段；智能模式可按语义重分段；校对模式保留原语言并修正 ASR 文本。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
            ListView {
                id: translationDocuments
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: subtitleController.subtitleDocumentsModel
                delegate: Rectangle {
                    required property string documentId
                    required property string language
                    required property bool isSource
                    required property int segmentCount
                    width: translationDocuments.width
                    height: 52
                    radius: Theme.radiusSmall
                    color: subtitleController.selectedDocumentId === documentId
                           ? Theme.accentSoft : translationMouse.containsMouse
                           ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: subtitleController.selectedDocumentId === documentId
                                  ? Theme.accent : Theme.border
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 9
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: language
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBodySmall
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: (isSource ? qsTr("源文档") : qsTr("译文"))
                                      + " · " + segmentCount + qsTr(" 条")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                    }
                    MouseArea {
                        id: translationMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: subtitleController.selectSubtitleDocument(documentId)
                    }
                }
                EmptyState {
                    anchors.fill: parent
                    visible: translationDocuments.count === 0
                    iconText: "译"
                    title: qsTr("还没有字幕文档")
                    description: qsTr("先转录媒体或导入 SRT。")
                }
            }
            Text {
                text: qsTr("处理模式")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppComboBox {
                id: translationMode
                Layout.fillWidth: true
                model: subtitleController.translationModeOptions
                textRole: "label"
                valueRole: "value"
            }
            Text {
                text: translationMode.currentValue === "proofread"
                      ? qsTr("校对保持源文档语言") : qsTr("目标语言")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppComboBox {
                id: targetLanguage
                objectName: "translationTargetLanguage"
                Layout.fillWidth: true
                enabled: translationMode.currentValue !== "proofread"
                model: subtitleController.translationLanguageOptions
                textRole: "label"
                valueRole: "value"
            }
            AppButton {
                Layout.fillWidth: true
                primary: true
                text: translationMode.currentValue === "proofread"
                      ? qsTr("校对所选文档") : qsTr("翻译所选文档")
                enabled: subtitleController.selectedDocumentId.length > 0
                    && (translationMode.currentValue === "proofread"
                        || targetLanguage.currentValue.length > 0)
                onClicked: subtitleController.translateDocument(
                    subtitleController.selectedDocumentId,
                    targetLanguage.currentValue,
                    translationMode.currentValue)
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
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("新建术语")
                    onClicked: settingsController.selectGlossaryTerm("")
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
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 5
                model: settingsController.glossaryTermsModel
                delegate: Rectangle {
                    required property string termId
                    required property string source
                    required property string target
                    required property string category
                    width: glossaryList.width
                    height: 52
                    radius: Theme.radiusSmall
                    color: settingsController.selectedGlossaryTermId === termId
                           ? Theme.accentSoft : termMouse.containsMouse
                           ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: settingsController.selectedGlossaryTermId === termId
                                  ? Theme.accent : Theme.border
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
                        onClicked: settingsController.selectGlossaryTerm(termId)
                    }
                }
                EmptyState {
                    anchors.fill: parent
                    visible: glossaryList.count === 0
                    iconText: "术"
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
                            enabled: settingsController.selectedGlossaryTermId.length > 0
                            onClicked: settingsController.removeGlossaryTerm(
                                settingsController.selectedGlossaryTermId)
                        }
                        Item { Layout.fillWidth: true }
                        AppButton {
                            primary: true
                            text: qsTr("保存术语")
                            enabled: termSource.text.trim().length > 0
                                && termTarget.text.trim().length > 0
                            onClicked: settingsController.saveGlossaryTerm(
                                settingsController.selectedGlossaryTermId,
                                termSource.text,
                                termTarget.text,
                                termNote.text,
                                termCategory.text)
                        }
                    }
                }
            }
        }
    }
}
