import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

AppDialog {
    id: root
    objectName: "settingsDialog"
    modal: true
    title: qsTr("设置")
    closePolicy: Popup.CloseOnEscape
    anchors.centerIn: parent
    width: Math.min(780, parent ? parent.width - 48 : 780)
    height: Math.min(820, parent ? parent.height - 48 : 820)
    property var llmProviderPresets: settingsController.llmProviderPresets
    property bool syncingFromController: false
    property var settingsBaseline: ({})

    function indexOfValue(model, value) {
        for (var i = 0; i < model.length; ++i) {
            if (model[i].value === value)
                return i
        }
        return 0
    }

    function syncFromController() {
        var data = settingsController.settingsData
        syncForm(data)
        settingsBaseline = settingsPayload()
        loadLlmProvider()
    }

    function syncForm(data) {
        language.currentIndex = root.indexOfValue(language.model, data.language)
        theme.currentIndex = root.indexOfValue(theme.model, data.theme)
        autoContinue.checked = data.autoContinue
        defaultProjectDirectory.text = data.defaultProjectDirectory
        defaultImportDirectory.text = data.defaultImportDirectory
        resolution.text = data.downloadResolution
        downloadDirectory.text = data.downloadDirectory
        downloadProxy.text = data.downloadProxy
        cookieFile.text = data.cookieFile
        browserCookies.currentIndex = root.indexOfValue(browserCookies.model, data.browserCookies)
        downloadSubtitles.checked = data.downloadSubtitles
        subtitleLanguages.text = data.subtitleLanguages
        downloadCodec.currentIndex = root.indexOfValue(downloadCodec.model, data.downloadCodec)
        asrEngine.currentIndex = root.indexOfValue(asrEngine.model, data.asrEngine)
        asrCliPath.text = data.asrCliPath
        asrModel.text = data.asrModel
        asrDevice.currentIndex = root.indexOfValue(asrDevice.model, data.asrDevice)
        computeType.text = data.asrComputeType
        asrLanguage.text = data.asrLanguage
        asrSmartSplitLimit.value = Number(data.asrSmartSplitLimit ?? 42)
        asrParallelChunks.currentIndex = root.indexOfValue(
            asrParallelChunks.model, Number(data.asrParallelChunks ?? 0))
        translationTargetLanguage.currentIndex = root.indexOfValue(
            translationTargetLanguage.model, data.translationTargetLanguage)
        translationMode.currentIndex = root.indexOfValue(
            translationMode.model, data.translationMode)
        automaticProxy.checked = data.automaticProxy
        previewQuality.currentIndex = root.indexOfValue(previewQuality.model, data.previewQuality)
        hdrPreview.checked = data.hdrPreview
        loudnessTarget.value = Math.round(data.loudnessTarget * 10)
        truePeak.value = Math.round(data.truePeak * 10)
        audioLayout.currentIndex = root.indexOfValue(audioLayout.model, data.audioLayout)
    }

    function sameValue(left, right) {
        return JSON.stringify(left) === JSON.stringify(right)
    }

    function mergeControllerSettings() {
        if (!visible || syncingFromController)
            return
        const form = settingsPayload()
        const incoming = settingsController.settingsData
        const merged = {}
        for (const key of Object.keys(form)) {
            merged[key] = sameValue(form[key], settingsBaseline[key])
                ? incoming[key] : form[key]
        }
        syncingFromController = true
        syncForm(merged)
        settingsBaseline = settingsPayload()
        Qt.callLater(function() {
            root.syncingFromController = false
        })
    }

    function loadLlmProvider() {
        var data = settingsController.selectedLlmProviderData
        llmName.text = data.name || ""
        llmBaseUrl.text = data.baseUrl || ""
        llmApiKey.text = data.apiKey || ""
        llmModel.text = data.model || ""
        llmEnabled.checked = data.providerId ? Boolean(data.enabled) : true
        var matched = root.llmProviderPresets.length - 1
        const normalized = String(data.baseUrl || "").replace(/\/+$/, "")
        for (var index = 0; index < root.llmProviderPresets.length - 1; ++index) {
            if (root.llmProviderPresets[index].baseUrl.replace(/\/+$/, "") === normalized) {
                matched = index
                break
            }
        }
        llmPreset.currentIndex = matched
        deepSeekReasoning.checked = matched === 0 && data.model === "deepseek-reasoner"
    }

    function applyLlmPreset() {
        const preset = root.llmProviderPresets[llmPreset.currentIndex]
        if (!preset || preset.value === "custom-local")
            return
        llmName.text = preset.text
        llmBaseUrl.text = preset.baseUrl
        deepSeekReasoning.checked = false
        llmModel.text = preset.model
    }

    function settingsPayload() {
        return {
            language: language.currentValue,
            theme: theme.currentValue,
            autoContinue: autoContinue.checked,
            defaultProjectDirectory: defaultProjectDirectory.text,
            defaultImportDirectory: defaultImportDirectory.text,
            downloadResolution: resolution.text,
            downloadDirectory: downloadDirectory.text,
            downloadProxy: downloadProxy.text,
            cookieFile: cookieFile.text,
            browserCookies: browserCookies.currentValue,
            downloadSubtitles: downloadSubtitles.checked,
            subtitleLanguages: subtitleLanguages.text,
            downloadCodec: downloadCodec.currentValue,
            asrEngine: asrEngine.currentValue,
            asrCliPath: asrCliPath.text,
            asrModel: asrModel.text,
            asrDevice: asrDevice.currentValue,
            asrComputeType: computeType.text,
            asrLanguage: asrLanguage.text,
            asrSmartSplitLimit: asrSmartSplitLimit.value,
            asrParallelChunks: asrParallelChunks.currentValue,
            translationTargetLanguage: translationTargetLanguage.currentValue,
            translationMode: translationMode.currentValue,
            automaticProxy: automaticProxy.checked,
            previewQuality: previewQuality.currentValue,
            hdrPreview: hdrPreview.checked,
            loudnessTarget: loudnessTarget.value / 10.0,
            truePeak: truePeak.value / 10.0,
            audioLayout: audioLayout.currentValue
        }
    }

    function scheduleSettingsSave() {
        if (!syncingFromController && visible)
            settingsSaveTimer.restart()
    }

    function saveSettingsNow() {
        if (syncingFromController)
            return
        settingsSaveTimer.stop()
        settingsController.saveSettings(
            settingsPayload(),
            settingsBaseline)
    }

    onOpened: {
        syncingFromController = true
        syncFromController()
        Qt.callLater(function() { root.syncingFromController = false })
    }
    onClosed: saveSettingsNow()

    Timer {
        id: settingsSaveTimer
        interval: 450
        repeat: false
        onTriggered: root.saveSettingsNow()
    }

    FolderDialog {
        id: projectDirectoryDialog
        title: qsTr("选择默认项目保存目录")
        currentFolder: workspaceController.defaultProjectDirectoryUrl
        onAccepted: {
            defaultProjectDirectory.text = selectedFolder.toLocalFile()
            root.scheduleSettingsSave()
        }
    }
    FolderDialog {
        id: downloadDirectoryDialog
        title: qsTr("选择媒体默认保存位置")
        onAccepted: {
            downloadDirectory.text = selectedFolder.toLocalFile()
            root.scheduleSettingsSave()
        }
    }
    Connections {
        target: settingsController
        function onSelectionChanged() { root.loadLlmProvider() }
        function onSettingsChanged() {
            root.mergeControllerSettings()
        }
    }

    contentItem: ColumnLayout {
        implicitWidth: Math.max(
            600, Math.min(780, root.parent ? root.parent.width - 80 : 780))
        implicitHeight: Math.min(720, root.parent ? root.parent.height - 100 : 720)
        spacing: 10

        AppTabBar {
            id: tabs
            objectName: "settingsTabs"
            Layout.fillWidth: true
            AppTabButton { text: qsTr("常规") }
            AppTabButton { text: qsTr("下载与媒体") }
            AppTabButton { text: qsTr("AI") }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabs.currentIndex

            AppScrollView {
                id: generalSettingsScroll
                clip: true
                ColumnLayout {
                    width: generalSettingsScroll.availableWidth
                    spacing: 12
                    Text { text: qsTr("界面"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("语言"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: language
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: "简体中文", value: "zh_CN"}, {text: "English", value: "en"}, {text: "日本語", value: "ja"}]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("主题"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: theme
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("深色"), value: "dark"}, {text: qsTr("高对比度"), value: "high_contrast"}]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    AppCheckBox {
                        id: autoContinue
                        objectName: "autoContinueSetting"
                        text: qsTr("工作流自动继续（遇到缺少 API、语言不明确或离线素材时仍会停止）")
                        onToggled: root.scheduleSettingsSave()
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("默认项目位置")
                            color: Theme.textMuted
                            Layout.preferredWidth: 150
                        }
                        AppTextField {
                            id: defaultProjectDirectory
                            objectName: "defaultProjectDirectorySetting"
                            Layout.fillWidth: true
                            placeholderText: qsTr("默认项目保存目录")
                            readOnly: true
                        }
                        AppButton { text: qsTr("选择"); onClicked: projectDirectoryDialog.open() }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("默认导入位置")
                            color: Theme.textMuted
                            Layout.preferredWidth: 150
                        }
                        AppTextField {
                            id: defaultImportDirectory
                            Layout.fillWidth: true
                            placeholderText: qsTr("默认导入目录（可选）")
                            color: Theme.text
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    Text { text: qsTr("预览性能"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
                    AppCheckBox {
                        id: automaticProxy
                        text: qsTr("需要时自动创建轻量预览文件（不会修改原视频）")
                        onToggled: root.scheduleSettingsSave()
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("预览质量"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: previewQuality
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("自动"), value: "auto"}, {text: qsTr("原始素材"), value: "source"}, {text: qsTr("轻量预览"), value: "proxy"}]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    AppCheckBox {
                        id: hdrPreview
                        text: qsTr("在设备支持时启用 HDR 预览")
                        onToggled: root.scheduleSettingsSave()
                    }
                    Text { text: qsTr("音频默认值"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("响度目标（LUFS）")
                            color: Theme.textMuted
                            Layout.preferredWidth: 150
                        }
                        AppSpinBox {
                            id: loudnessTarget
                            Layout.fillWidth: true
                            from: -300
                            to: -50
                            value: -140
                            editable: true
                            textFromValue: function(value, locale) {
                                return Number(value / 10).toLocaleString(locale, "f", 1)
                            }
                            valueFromText: function(text, locale) {
                                return Math.round(Number.fromLocaleString(locale, text) * 10)
                            }
                            onValueChanged: root.scheduleSettingsSave()
                        }
                        Text {
                            text: qsTr("True Peak（dBTP）")
                            color: Theme.textMuted
                        }
                        AppSpinBox {
                            id: truePeak
                            Layout.fillWidth: true
                            from: -100
                            to: 0
                            value: -10
                            editable: true
                            textFromValue: function(value, locale) {
                                return Number(value / 10).toLocaleString(locale, "f", 1)
                            }
                            valueFromText: function(text, locale) {
                                return Math.round(Number.fromLocaleString(locale, text) * 10)
                            }
                            onValueChanged: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("声道布局"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: audioLayout
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [
                                {text: qsTr("单声道"), value: "mono"},
                                {text: qsTr("立体声"), value: "stereo"},
                                {text: "5.1", value: "5.1"}
                            ]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                }
            }

            AppScrollView {
                id: downloadSettingsScroll
                clip: true
                ColumnLayout {
                    width: downloadSettingsScroll.availableWidth
                    spacing: 12
                    Text { text: qsTr("yt-dlp 下载"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("媒体默认保存位置")
                            color: Theme.textMuted
                            Layout.preferredWidth: 150
                        }
                        PathDisplay {
                            id: downloadDirectory
                            objectName: "defaultMediaDirectorySetting"
                            Layout.fillWidth: true
                            placeholderText: qsTr("媒体默认保存目录")
                        }
                        AppButton {
                            text: qsTr("恢复默认")
                            visible: downloadDirectory.text !== settingsController.builtInMediaDirectory
                            onClicked: {
                                downloadDirectory.text = settingsController.builtInMediaDirectory
                                root.scheduleSettingsSave()
                            }
                        }
                        AppButton { text: qsTr("选择文件夹"); onClicked: downloadDirectoryDialog.open() }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("媒体会保存到应用目录下的 WorkSpace，与 Project 项目目录分开。")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("下载格式"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: resolution
                            Layout.fillWidth: true
                            placeholderText: qsTr("例如 best 或 1080p")
                            color: Theme.text
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("代理服务器（可选）"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: downloadProxy
                            Layout.fillWidth: true
                            placeholderText: "http://127.0.0.1:7890"
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("Cookie 文件（可选）"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: cookieFile
                            Layout.fillWidth: true
                            placeholderText: qsTr("cookies.txt 路径")
                            color: Theme.text
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("读取浏览器 Cookie"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppComboBox {
                            id: browserCookies
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("不读取"), value: ""}, {text: "Chrome", value: "chrome"}, {text: "Edge", value: "edge"}]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    Panel {
                        Layout.fillWidth: true
                        implicitHeight: 282
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 7
                            Text { text: qsTr("按网站保存 Cookie"); color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: qsTr("网站域名"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                                AppTextField {
                                    id: cookieDomain
                                    Layout.fillWidth: true
                                    placeholderText: qsTr("例如 douyin.com")
                                }
                            }
                            Text {
                                text: qsTr("Cookie JSON")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            AppTextArea {
                                id: cookieJsonText
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                placeholderText: qsTr("粘贴浏览器导出的 Cookie JSON 数组")
                                wrapMode: TextEdit.WrapAnywhere
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    Layout.fillWidth: true
                                    text: settingsController.managedCookieStatus.exists
                                          ? (settingsController.managedCookieStatus.valid
                                             ? qsTr("有效 · %1 小时").arg(settingsController.managedCookieStatus.ageHours)
                                             : qsTr("已过期 · %1 小时").arg(settingsController.managedCookieStatus.ageHours))
                                          : qsTr("未保存")
                                    color: settingsController.managedCookieStatus.valid ? Theme.success : Theme.textMuted
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                                AppButton { text: qsTr("检查"); onClicked: settingsController.inspectManagedCookies(cookieDomain.text) }
                                AppButton { text: qsTr("清除"); onClicked: settingsController.clearManagedCookies(cookieDomain.text) }
                                AppButton {
                                    primary: true
                                    text: qsTr("保存")
                                    enabled: cookieDomain.text.trim().length > 0 && cookieJsonText.text.trim().length > 0
                                    onClicked: settingsController.saveManagedCookies(cookieDomain.text, cookieJsonText.text)
                                }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("视频编码"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppComboBox {
                            id: downloadCodec
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [
                                {text: qsTr("最佳可用编码"), value: "best"},
                                {text: qsTr("优先 H.264 / AVC"), value: "avc"}
                            ]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    AppCheckBox {
                        id: downloadSubtitles
                        text: qsTr("默认同时下载字幕并转换为 SRT")
                        onToggled: root.scheduleSettingsSave()
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("字幕语言"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: subtitleLanguages
                            Layout.fillWidth: true
                            placeholderText: qsTr("逗号分隔，例如 en,zh")
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    Text { Layout.fillWidth: true; text: qsTr("不会启动浏览器窗口。浏览器 Cookie 由 yt-dlp 直接读取。"); color: Theme.textMuted; wrapMode: Text.WordWrap }
                    Panel {
                        Layout.fillWidth: true
                        implicitHeight: runtimeTools.implicitHeight + 18
                        ColumnLayout {
                            id: runtimeTools
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 7
                            Text { text: qsTr("运行时工具"); color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold }
                            Text {
                                Layout.fillWidth: true
                                text: qsTr("yt-dlp：%1 · Faster-Whisper XXL：%2")
                                    .arg(settingsController.runtimeToolStatus.ytDlpVersion || qsTr("未检测"))
                                    .arg(settingsController.runtimeToolStatus.cliInstalled ? qsTr("已安装") : qsTr("未安装"))
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: settingsController.runtimeToolStatus.cudaSummary || qsTr("尚未检测 CUDA")
                                color: settingsController.runtimeToolStatus.cudaStatus === "ready" ? Theme.success : Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                wrapMode: Text.WordWrap
                            }
                            AppProgressBar {
                                Layout.fillWidth: true
                                visible: settingsController.runtimeToolStatus.busy
                                from: 0; to: 100
                                indeterminate: settingsController.runtimeToolStatus.progressMode !== "determinate"
                                value: Number(settingsController.runtimeToolStatus.progressValue || 0)
                            }
                            Text {
                                visible: settingsController.runtimeToolStatus.busy
                                text: settingsController.runtimeToolStatus.message || qsTr("正在处理")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppButton { Layout.fillWidth: true; enabled: !settingsController.runtimeToolStatus.busy; text: qsTr("检测 CUDA"); onClicked: settingsController.inspectRuntimeTools() }
                                AppButton { Layout.fillWidth: true; enabled: !settingsController.runtimeToolStatus.busy; text: qsTr("更新 yt-dlp"); onClicked: settingsController.updateYtDlp() }
                                AppButton { Layout.fillWidth: true; enabled: !settingsController.runtimeToolStatus.busy; text: qsTr("安装 XXL"); onClicked: settingsController.installAsrCli() }
                                AppButton { Layout.fillWidth: true; enabled: !settingsController.runtimeToolStatus.busy && settingsController.runtimeToolStatus.cliInstalled; text: qsTr("预热 XXL"); onClicked: settingsController.prewarmAsrCli() }
                                AppButton { visible: settingsController.runtimeToolStatus.busy; danger: true; text: qsTr("取消"); onClicked: settingsController.cancelRuntimeToolOperation() }
                            }
                        }
                    }
                    Text { text: qsTr("转录"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("转录引擎"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: asrEngine
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [
                                {text: qsTr("内置 faster-whisper"), value: "builtin"},
                                {text: qsTr("Faster-Whisper XXL CLI"), value: "faster_whisper_cli"}
                            ]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: asrEngine.currentValue === "faster_whisper_cli"
                        Text { text: qsTr("转录程序路径"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: asrCliPath
                            Layout.fillWidth: true
                            placeholderText: qsTr("留空使用 D 盘运行时安装目录")
                            color: Theme.text
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("模型"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: asrModel
                            Layout.fillWidth: true
                            placeholderText: qsTr("例如 large-v3-turbo")
                            color: Theme.text
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("设备"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: asrDevice
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: [{text: qsTr("自动"), value: "auto"}, {text: "CUDA", value: "cuda"}, {text: "CPU", value: "cpu"}]
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("计算类型"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: computeType
                            Layout.fillWidth: true
                            placeholderText: qsTr("例如 float16 / int8")
                            color: Theme.text
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("语言代码"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppTextField {
                            id: asrLanguage
                            Layout.fillWidth: true
                            placeholderText: qsTr("auto 为自动识别")
                            color: Theme.text
                            onEditingFinished: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("智能断句字符上限"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppSpinBox {
                            id: asrSmartSplitLimit
                            Layout.fillWidth: true
                            from: 1
                            to: 200
                            value: 42
                            editable: true
                            onValueChanged: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("长音频并行分块"); color: Theme.textMuted; Layout.preferredWidth: 180 }
                        AppComboBox {
                            id: asrParallelChunks
                            Layout.fillWidth: true
                            textRole: "text"; valueRole: "value"
                            model: settingsController.asrParallelOptions
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                }
            }

            AppScrollView {
                id: aiSettingsScroll
                clip: true
                ColumnLayout {
                    width: aiSettingsScroll.availableWidth
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("OpenAI 兼容 LLM 提供商"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
                        Item { Layout.fillWidth: true }
                        AppButton {
                            text: qsTr("新增提供商")
                            onClicked: settingsController.selectLlmProvider("")
                        }
                    }
                    ListView {
                        id: providerList
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(180, Math.max(58, contentHeight))
                        clip: true
                        spacing: 5
                        model: settingsController.llmProvidersModel
                        delegate: Rectangle {
                            required property string providerId
                            required property string name
                            required property string baseUrl
                            required property string model
                            required property bool active
                            width: providerList.width
                            height: 56
                            radius: Theme.radiusSmall
                            color: settingsController.selectedLlmProviderId === providerId
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
                                Text { visible: !model.enabled; text: qsTr("已停用"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                            }
                            MouseArea {
                                id: providerMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: settingsController.selectLlmProvider(providerId)
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
                                    model: root.llmProviderPresets
                                    textRole: "text"; valueRole: "value"
                                    onActivated: root.applyLlmPreset()
                                }
                            }
                            AppCheckBox {
                                id: deepSeekReasoning
                                visible: llmPreset.currentValue === "deepseek"
                                text: qsTr("DeepSeek 推理模式（deepseek-reasoner）")
                                onToggled: {
                                    if (visible)
                                        llmModel.text = checked ? "deepseek-reasoner" : "deepseek-chat"
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
                                AppTextField { id: llmModel; Layout.fillWidth: true; placeholderText: qsTr("例如 deepseek-chat") }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppCheckBox { id: llmEnabled; text: qsTr("启用") ; checked: true }
                                AppButton {
                                    text: qsTr("移除")
                                    enabled: settingsController.selectedLlmProviderId.length > 0
                                    onClicked: settingsController.removeLlmProvider(settingsController.selectedLlmProviderId)
                                }
                                Item { Layout.fillWidth: true }
                                AppButton {
                                    text: qsTr("保存")
                                    enabled: llmBaseUrl.text.trim().length > 0 && llmModel.text.trim().length > 0
                                    onClicked: settingsController.saveLlmProvider(
                                        settingsController.selectedLlmProviderId,
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
                            enabled: settingsController.selectedLlmProviderId.length > 0
                            onClicked: settingsController.setActiveLlmProvider(settingsController.selectedLlmProviderId)
                        }
                        AppButton {
                            Layout.fillWidth: true
                            text: qsTr("测试连接")
                            enabled: settingsController.selectedLlmProviderId.length > 0
                            onClicked: settingsController.testLlmProvider(settingsController.selectedLlmProviderId)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("默认翻译语言"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: translationTargetLanguage
                            Layout.fillWidth: true
                            textRole: "label"; valueRole: "value"
                            model: subtitleController.translationLanguageOptions
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("默认处理模式"); color: Theme.textMuted; Layout.preferredWidth: 150 }
                        AppComboBox {
                            id: translationMode
                            Layout.fillWidth: true
                            textRole: "label"; valueRole: "value"
                            model: subtitleController.translationModeOptions
                            onActivated: root.scheduleSettingsSave()
                        }
                    }
                    Text { Layout.fillWidth: true; text: qsTr("翻译和高光分析使用当前启用的提供商。术语库在翻译页面维护。"); color: Theme.textMuted; wrapMode: Text.WordWrap }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "settingsAutoSaveNotice"
                Layout.fillWidth: true
                text: qsTr("更改会自动保存")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppButton {
                objectName: "settingsCloseButton"
                text: qsTr("关闭")
                enabled: downloadDirectory.text.length > 0
                onClicked: root.close()
            }
        }
    }
}
