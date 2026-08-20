import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"
AppScrollView {
    id: page
    property var settingsDialog

    function sync(data) {
        language.currentIndex = settingsDialog.indexOfValue(language.model, data.language)
        theme.currentIndex = settingsDialog.indexOfValue(theme.model, data.theme)
        autoContinue.checked = data.autoContinue
        defaultProjectDirectory.text = data.defaultProjectDirectory
        defaultImportDirectory.text = data.defaultImportDirectory
        automaticProxy.checked = data.automaticProxy
        previewQuality.currentIndex = settingsDialog.indexOfValue(previewQuality.model, data.previewQuality)
        hdrPreview.checked = data.hdrPreview
        loudnessTarget.value = Math.round(data.loudnessTarget * 10)
        truePeak.value = Math.round(data.truePeak * 10)
        audioLayout.currentIndex = settingsDialog.indexOfValue(audioLayout.model, data.audioLayout)
        diarizationBackend.currentIndex = settingsDialog.indexOfValue(diarizationBackend.model, data.diarizationBackend)
        diarizationPython.text = data.diarizationPython
        diarizationModel.text = data.diarizationModel
        diarizationHfToken.text = data.diarizationHfToken
        diarizationDevice.currentIndex = settingsDialog.indexOfValue(diarizationDevice.model, data.diarizationDevice)
    }
    clip: true
    ColumnLayout {
        width: page.availableWidth
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
                onActivated: page.settingsDialog.updateDraft("language", language.currentValue)
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
                onActivated: page.settingsDialog.updateDraft("theme", theme.currentValue)
            }
        }
        AppCheckBox {
            id: autoContinue
            objectName: "autoContinueSetting"
            text: qsTr("工作流自动继续（遇到缺少 API、语言不明确或离线素材时仍会停止）")
            onToggled: page.settingsDialog.updateDraft("autoContinue", autoContinue.checked)
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
                onEditingFinished: page.settingsDialog.updateDraft("defaultImportDirectory", defaultImportDirectory.text)
            }
        }
        Text { text: qsTr("预览性能"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
        AppCheckBox {
            id: automaticProxy
            text: qsTr("需要时自动创建轻量预览文件（不会修改原视频）")
            onToggled: page.settingsDialog.updateDraft("automaticProxy", automaticProxy.checked)
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("预览质量"); color: Theme.textMuted; Layout.preferredWidth: 150 }
            AppComboBox {
                id: previewQuality
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: [{text: qsTr("自动"), value: "auto"}, {text: qsTr("原始素材"), value: "source"}, {text: qsTr("轻量预览"), value: "proxy"}]
                onActivated: page.settingsDialog.updateDraft("previewQuality", previewQuality.currentValue)
            }
        }
        AppCheckBox {
            id: hdrPreview
            text: qsTr("在设备支持时启用 HDR 预览")
            onToggled: page.settingsDialog.updateDraft("hdrPreview", hdrPreview.checked)
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
                onValueChanged: page.settingsDialog.updateDraft("loudnessTarget", loudnessTarget.value / 10.0)
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
                onValueChanged: page.settingsDialog.updateDraft("truePeak", truePeak.value / 10.0)
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
                onActivated: page.settingsDialog.updateDraft("audioLayout", audioLayout.currentValue)
            }
        }
        Text { text: qsTr("多人音色识别"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: diarizationBackend.currentValue === "transcript_clustering"
                ? qsTr("普通模式直接按英文转写片段提取 3D-Speaker 音色并聚类，适合轮流说话的音频，不需要 Hugging Face 账号或令牌。")
                : qsTr("Community-1 用于多人同时说话等复杂录音，需要先在 Hugging Face 接受模型条款并配置令牌。")
            color: Theme.textMuted
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("识别方式"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppComboBox {
                id: diarizationBackend
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: [
                    {text: qsTr("转写片段音色聚类（推荐）"), value: "transcript_clustering"},
                    {text: qsTr("重叠语音 Community-1"), value: "community_1"}
                ]
                onActivated: page.settingsDialog.updateDraft("diarizationBackend", diarizationBackend.currentValue)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: diarizationBackend.currentValue === "transcript_clustering"
            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: {
                    const status = mediaflow.settingsController.runtimeToolStatus.speakerClustering || {}
                    return status.ready
                        ? qsTr("本地音色模型已就绪：%1").arg(status.version || "sherpa-onnx")
                        : (status.reason || qsTr("尚未安装本地音色模型"))
                }
                color: (mediaflow.settingsController.runtimeToolStatus.speakerClustering || {}).ready
                    ? Theme.success : Theme.textMuted
            }
            AppButton {
                objectName: "installSpeakerClusteringButton"
                enabled: !mediaflow.settingsController.runtimeToolStatus.busy
                text: (mediaflow.settingsController.runtimeToolStatus.speakerClustering || {}).ready
                    ? qsTr("重新安装") : qsTr("安装本地模型")
                onClicked: mediaflow.settingsController.installSpeakerClustering()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: diarizationBackend.currentValue === "community_1"
            Text { text: qsTr("pyannote Python"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: diarizationPython
                objectName: "diarizationPythonField"
                Layout.fillWidth: true
                placeholderText: qsTr("选择独立 pyannote 环境中的 python.exe")
                color: Theme.text
                onEditingFinished: page.settingsDialog.updateDraft("diarizationPython", diarizationPython.text)
            }
            AppButton { text: qsTr("选择"); onClicked: diarizationPythonDialog.open() }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: diarizationBackend.currentValue === "community_1"
            Text { text: qsTr("Community-1 模型"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: diarizationModel
                Layout.fillWidth: true
                placeholderText: "pyannote/speaker-diarization-community-1"
                color: Theme.text
                onEditingFinished: page.settingsDialog.updateDraft("diarizationModel", diarizationModel.text)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: diarizationBackend.currentValue === "community_1"
            Text { text: qsTr("Hugging Face 令牌"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: diarizationHfToken
                Layout.fillWidth: true
                echoMode: TextInput.Password
                placeholderText: qsTr("本地模型已缓存时可以留空")
                color: Theme.text
                onEditingFinished: page.settingsDialog.updateDraft("diarizationHfToken", diarizationHfToken.text)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: diarizationBackend.currentValue === "community_1"
            Text { text: qsTr("说话人识别设备"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppComboBox {
                id: diarizationDevice
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: [{text: qsTr("自动"), value: "auto"}, {text: "CUDA", value: "cuda"}, {text: "CPU", value: "cpu"}]
                onActivated: page.settingsDialog.updateDraft("diarizationDevice", diarizationDevice.currentValue)
            }
        }
    }

    FolderDialog {
        id: projectDirectoryDialog
        title: qsTr("选择默认项目保存目录")
        currentFolder: mediaflow.workspaceViewController.defaultProjectDirectoryUrl
        onAccepted: {
            defaultProjectDirectory.text = selectedFolder.toLocalFile()
            page.settingsDialog.updateDraft("defaultProjectDirectory", defaultProjectDirectory.text)
        }
    }
    FileDialog {
        id: diarizationPythonDialog
        title: qsTr("选择 pyannote.audio Python")
        fileMode: FileDialog.OpenFile
        nameFilters: Qt.platform.os === "windows"
            ? [qsTr("Python (python.exe)"), qsTr("可执行文件 (*.exe)")]
            : [qsTr("可执行文件 (*)")]
        onAccepted: {
            diarizationPython.text = selectedFile.toLocalFile()
            page.settingsDialog.updateDraft("diarizationPython", diarizationPython.text)
        }
    }
}
