import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"
AppScrollView {
    id: page
    property var settingsDialog
    readonly property var selectedLlmPreset: {
        const index = llmPreset.currentIndex
        return index >= 0 && index < settingsDialog.llmProviderPresets.length
            ? settingsDialog.llmProviderPresets[index] : null
    }

    function sync(data) {
        translationTargetLanguage.currentIndex = settingsDialog.indexOfValue(translationTargetLanguage.model, data.translationTargetLanguage)
        translationMode.currentIndex = settingsDialog.indexOfValue(translationMode.model, data.translationMode)
    }

    function loadLlmProvider() {
        var data = mediaflow.settingsController.selectedLlmProviderData
        llmName.text = data.name || ""
        llmBaseUrl.text = data.baseUrl || ""
        llmApiKey.text = data.apiKey || ""
        llmModel.text = data.model || ""
        llmEnabled.checked = data.providerId ? Boolean(data.enabled) : true
        var matched = -1
        var customIndex = -1
        const normalized = String(data.baseUrl || "").replace(/\/+$/, "")
        for (var index = 0; index < settingsDialog.llmProviderPresets.length; ++index) {
            const preset = settingsDialog.llmProviderPresets[index]
            if (preset.custom) {
                customIndex = index
            } else if (preset.baseUrl.replace(/\/+$/, "") === normalized) {
                matched = index
                break
            }
        }
        llmPreset.currentIndex = matched >= 0 ? matched : customIndex
        const preset = page.selectedLlmPreset
        providerReasoning.checked = Boolean(
            preset && preset.reasoningModel
            && data.model === preset.reasoningModel)
    }

    function applyLlmPreset() {
        const preset = settingsDialog.llmProviderPresets[llmPreset.currentIndex]
        if (!preset || preset.custom)
            return
        llmName.text = preset.text
        llmBaseUrl.text = preset.baseUrl
        providerReasoning.checked = false
        llmModel.text = preset.standardModel
    }
    clip: true
    ColumnLayout {
        width: page.availableWidth
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("OpenAI 兼容 LLM 提供商"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            AppButton {
                text: qsTr("新增提供商")
                onClicked: mediaflow.settingsController.selectLlmProvider("")
            }
        }
        ListView {
            id: providerList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(180, Math.max(58, contentHeight))
            clip: true
            spacing: 5
            model: mediaflow.settingsController.llmProvidersModel
            delegate: Rectangle {
                required property string providerId
                required property string name
                required property string baseUrl
                required property string model
                required property bool enabled
                required property bool active
                width: providerList.width
                height: 56
                radius: Theme.radiusSmall
                color: mediaflow.settingsController.selectedLlmProviderId === providerId
                       ? Theme.accentSoft : providerMouse.containsMouse
                       ? Theme.surfaceHover : Theme.surfaceRaised
                border.color: active ? Theme.accent : Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: name; color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold }
                        Text { Layout.fillWidth: true; text: model + " · " + baseUrl; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; elide: Text.ElideMiddle }
                    }
                    Text { visible: active; text: qsTr("当前"); color: Theme.accentHover; font.pixelSize: Theme.fontSizeCaption }
                    Text { visible: !enabled; text: qsTr("已停用"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                }
                MouseArea {
                    id: providerMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: mediaflow.settingsController.selectLlmProvider(providerId)
                }
            }
        }
        Panel {
            Layout.fillWidth: true
            implicitHeight: 322
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 9
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: qsTr("提供商预设")
                        color: Theme.textMuted
                        Layout.preferredWidth: 130
                    }
                    AppComboBox {
                        id: llmPreset
                        Layout.fillWidth: true
                        model: page.settingsDialog.llmProviderPresets
                        textRole: "text"; valueRole: "value"
                        onActivated: page.applyLlmPreset()
                    }
                }
                AppCheckBox {
                    id: providerReasoning
                    visible: Boolean(page.selectedLlmPreset
                                     && page.selectedLlmPreset.reasoningModel)
                    text: page.selectedLlmPreset
                          ? page.selectedLlmPreset.reasoningLabel : ""
                    onToggled: {
                        if (visible)
                            llmModel.text = checked
                                ? page.selectedLlmPreset.reasoningModel
                                : page.selectedLlmPreset.standardModel
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("名称"); color: Theme.textMuted; Layout.preferredWidth: 130 }
                    AppTextField { id: llmName; Layout.fillWidth: true; placeholderText: qsTr("例如 DeepSeek") }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("服务地址"); color: Theme.textMuted; Layout.preferredWidth: 130 }
                    AppTextField { id: llmBaseUrl; Layout.fillWidth: true; placeholderText: "https://api.example.com/v1" }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("API 密钥"); color: Theme.textMuted; Layout.preferredWidth: 130 }
                    AppTextField { id: llmApiKey; Layout.fillWidth: true; placeholderText: "sk-…"; echoMode: TextInput.Password }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("模型名称"); color: Theme.textMuted; Layout.preferredWidth: 130 }
                    AppTextField {
                        id: llmModel
                        Layout.fillWidth: true
                        placeholderText: page.selectedLlmPreset
                            && page.selectedLlmPreset.standardModel
                            ? page.selectedLlmPreset.standardModel
                            : qsTr("例如模型 ID")
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppCheckBox { id: llmEnabled; text: qsTr("启用") ; checked: true }
                    AppButton {
                        text: qsTr("移除")
                        enabled: mediaflow.settingsController.selectedLlmProviderId.length > 0
                        onClicked: mediaflow.settingsController.removeLlmProvider(mediaflow.settingsController.selectedLlmProviderId)
                    }
                    Item { Layout.fillWidth: true }
                    AppButton {
                        text: qsTr("保存")
                        enabled: llmBaseUrl.text.trim().length > 0 && llmModel.text.trim().length > 0
                        onClicked: mediaflow.settingsController.saveLlmProvider(
                            mediaflow.settingsController.selectedLlmProviderId,
                            llmName.text, llmBaseUrl.text, llmApiKey.text,
                            llmModel.text, llmEnabled.checked)
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton {
                Layout.fillWidth: true
                text: qsTr("设为当前提供商")
                enabled: mediaflow.settingsController.selectedLlmProviderId.length > 0
                onClicked: mediaflow.settingsController.setActiveLlmProvider(mediaflow.settingsController.selectedLlmProviderId)
            }
            AppButton {
                Layout.fillWidth: true
                text: qsTr("测试连接")
                enabled: mediaflow.settingsController.selectedLlmProviderId.length > 0
                onClicked: mediaflow.settingsController.testLlmProvider(mediaflow.settingsController.selectedLlmProviderId)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("默认翻译语言"); color: Theme.textMuted; Layout.preferredWidth: 150 }
            AppComboBox {
                id: translationTargetLanguage
                Layout.fillWidth: true
                textRole: "label"; valueRole: "value"
                model: mediaflow.subtitleViewController.translationLanguageOptions
                onActivated: page.settingsDialog.updateDraft("translationTargetLanguage", translationTargetLanguage.currentValue)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("默认处理模式"); color: Theme.textMuted; Layout.preferredWidth: 150 }
            AppComboBox {
                id: translationMode
                Layout.fillWidth: true
                textRole: "label"; valueRole: "value"
                model: mediaflow.subtitleViewController.translationModeOptions
                onActivated: page.settingsDialog.updateDraft("translationMode", translationMode.currentValue)
            }
        }
        Text { Layout.fillWidth: true; text: qsTr("翻译和高光分析使用当前启用的提供商。术语库在翻译页面维护。"); color: Theme.textMuted; wrapMode: Text.WordWrap }
    }
}
