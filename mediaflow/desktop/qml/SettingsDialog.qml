import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Dialog {
    id: root
    modal: true
    title: qsTr("设置")
    closePolicy: Popup.CloseOnEscape

    function indexOfValue(model, value) {
        for (var i = 0; i < model.length; ++i) {
            if (model[i].value === value)
                return i
        }
        return 0
    }

    function syncFromController() {
        var data = projectController.settingsData
        language.currentIndex = root.indexOfValue(language.model, data.language)
        theme.currentIndex = root.indexOfValue(theme.model, data.theme)
        autoContinue.checked = data.autoContinue
        defaultImportDirectory.text = data.defaultImportDirectory
        resolution.text = data.downloadResolution
        cookieFile.text = data.cookieFile
        browserCookies.currentIndex = root.indexOfValue(browserCookies.model, data.browserCookies)
        asrModel.text = data.asrModel
        asrDevice.currentIndex = root.indexOfValue(asrDevice.model, data.asrDevice)
        computeType.text = data.asrComputeType
        asrLanguage.text = data.asrLanguage
        translationTargetLanguage.currentIndex = root.indexOfValue(
            translationTargetLanguage.model, data.translationTargetLanguage)
        automaticProxy.checked = data.automaticProxy
        previewQuality.currentIndex = root.indexOfValue(previewQuality.model, data.previewQuality)
        hdrPreview.checked = data.hdrPreview
        loudnessTarget.value = Math.round(data.loudnessTarget * 10)
        truePeak.value = Math.round(data.truePeak * 10)
        audioLayout.currentIndex = root.indexOfValue(audioLayout.model, data.audioLayout)
        llmName.text = data.llmName
        llmBaseUrl.text = data.llmBaseUrl
        llmApiKey.text = data.llmApiKey
        llmModel.text = data.llmModel
    }

    onOpened: syncFromController()

    contentItem: ColumnLayout {
        implicitWidth: 680
        implicitHeight: Math.min(720, root.parent ? root.parent.height - 100 : 720)
        spacing: 10

        TabBar {
            id: tabs
            Layout.fillWidth: true
            TabButton { text: qsTr("常规") }
            TabButton { text: qsTr("下载与媒体") }
            TabButton { text: qsTr("AI") }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabs.currentIndex

            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 12
                    Text { text: qsTr("界面"); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("语言"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        ComboBox {
                            id: language
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: "简体中文", value: "zh_CN"}, {text: "English", value: "en"}, {text: "日本語", value: "ja"}]
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("主题"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        ComboBox {
                            id: theme
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("深色"), value: "dark"}, {text: qsTr("高对比度"), value: "high_contrast"}]
                        }
                    }
                    CheckBox { id: autoContinue; text: qsTr("工作流自动继续（遇到缺少 API、语言不明确或离线素材时仍会停止）") }
                    TextField {
                        id: defaultImportDirectory
                        Layout.fillWidth: true
                        placeholderText: qsTr("默认导入目录（可选）")
                        color: Theme.text
                    }
                    Text { text: qsTr("预览与代理"); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    CheckBox { id: automaticProxy; text: qsTr("自动为高分辨率、高码率、VFR、10-bit/HDR 或持续掉帧素材生成代理") }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("预览质量"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        ComboBox {
                            id: previewQuality
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("自动"), value: "auto"}, {text: qsTr("原始素材"), value: "source"}, {text: qsTr("代理"), value: "proxy"}]
                        }
                    }
                    CheckBox { id: hdrPreview; text: qsTr("在设备支持时启用 HDR 预览") }
                    Text { text: qsTr("音频默认值"); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    RowLayout {
                        Text { text: qsTr("响度目标 ×10"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        SpinBox { id: loudnessTarget; from: -300; to: -50; value: -140; editable: true }
                        Text { text: qsTr("True Peak ×10"); color: Theme.textMuted }
                        SpinBox { id: truePeak; from: -100; to: 0; value: -10; editable: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("声道布局"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        ComboBox {
                            id: audioLayout
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: "Mono", value: "mono"}, {text: "Stereo", value: "stereo"}, {text: "5.1", value: "5.1"}]
                        }
                    }
                }
            }

            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 12
                    Text { text: qsTr("yt-dlp 下载"); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    TextField { id: resolution; Layout.fillWidth: true; placeholderText: qsTr("格式，例如 best 或 1080p"); color: Theme.text }
                    TextField { id: cookieFile; Layout.fillWidth: true; placeholderText: qsTr("cookies.txt 路径（可选）"); color: Theme.text }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("读取浏览器 Cookie"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        ComboBox {
                            id: browserCookies
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("不读取"), value: ""}, {text: "Chrome", value: "chrome"}, {text: "Edge", value: "edge"}]
                        }
                    }
                    Text { Layout.fillWidth: true; text: qsTr("不会启动浏览器窗口。浏览器 Cookie 由 yt-dlp 直接读取。"); color: Theme.textMuted; wrapMode: Text.WordWrap }
                    Text { text: qsTr("转录"); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    TextField { id: asrModel; Layout.fillWidth: true; placeholderText: qsTr("faster-whisper 模型"); color: Theme.text }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("设备"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        ComboBox {
                            id: asrDevice
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("自动"), value: "auto"}, {text: "CUDA", value: "cuda"}, {text: "CPU", value: "cpu"}]
                        }
                    }
                    TextField { id: computeType; Layout.fillWidth: true; placeholderText: qsTr("计算类型，例如 float16 / int8"); color: Theme.text }
                    TextField { id: asrLanguage; Layout.fillWidth: true; placeholderText: qsTr("语言代码，auto 为自动识别"); color: Theme.text }
                }
            }

            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 12
                    Text { text: qsTr("OpenAI 兼容 LLM 提供商"); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    TextField { id: llmName; Layout.fillWidth: true; placeholderText: qsTr("名称"); color: Theme.text }
                    TextField { id: llmBaseUrl; Layout.fillWidth: true; placeholderText: "https://api.example.com/v1"; color: Theme.text }
                    TextField { id: llmApiKey; Layout.fillWidth: true; placeholderText: "API Key"; echoMode: TextInput.Password; color: Theme.text }
                    TextField { id: llmModel; Layout.fillWidth: true; placeholderText: qsTr("模型名称"); color: Theme.text }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("默认翻译语言"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        ComboBox {
                            id: translationTargetLanguage
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [
                                {text: qsTr("选择目标语言"), value: ""},
                                {text: qsTr("简体中文"), value: "zh_CN"},
                                {text: "English", value: "en"},
                                {text: qsTr("日语"), value: "ja"},
                                {text: qsTr("繁体中文"), value: "zh_TW"},
                                {text: qsTr("韩语"), value: "ko"},
                                {text: qsTr("西班牙语"), value: "es"}
                            ]
                        }
                    }
                    Text { Layout.fillWidth: true; text: qsTr("翻译和高光分析使用同一强类型配置。留空全部字段可禁用 LLM。"); color: Theme.textMuted; wrapMode: Text.WordWrap }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton { text: qsTr("取消"); onClicked: root.close() }
            AppButton {
                primary: true
                text: qsTr("保存设置")
                onClicked: {
                    projectController.saveSettings({
                        language: language.currentValue,
                        theme: theme.currentValue,
                        autoContinue: autoContinue.checked,
                        defaultImportDirectory: defaultImportDirectory.text,
                        downloadResolution: resolution.text,
                        cookieFile: cookieFile.text,
                        browserCookies: browserCookies.currentValue,
                        asrModel: asrModel.text,
                        asrDevice: asrDevice.currentValue,
                        asrComputeType: computeType.text,
                        asrLanguage: asrLanguage.text,
                        translationTargetLanguage: translationTargetLanguage.currentValue,
                        automaticProxy: automaticProxy.checked,
                        previewQuality: previewQuality.currentValue,
                        hdrPreview: hdrPreview.checked,
                        loudnessTarget: loudnessTarget.value / 10.0,
                        truePeak: truePeak.value / 10.0,
                        audioLayout: audioLayout.currentValue,
                        llmName: llmName.text,
                        llmBaseUrl: llmBaseUrl.text,
                        llmApiKey: llmApiKey.text,
                        llmModel: llmModel.text
                    })
                    root.close()
                }
            }
        }
    }
}
