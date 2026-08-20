import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"
AppScrollView {
    id: page
    objectName: "downloadSettingsScroll"
    property var settingsDialog
    readonly property bool valid: downloadDirectory.text.length > 0
    readonly property bool managedCookieDirty: cookieJsonText.text.trim().length > 0
    readonly property bool modalOpen: clearCookieDialog.opened

    function discardManagedCookieDraft() {
        cookieDomain.clear()
        cookieJsonText.clear()
    }

    AppConfirmationDialog {
        id: clearCookieDialog
        onConfirmed: function (domain) {
            if (domain.length > 0)
                mediaflow.settingsController.clearManagedCookies(domain)
        }
    }

    function sync(data) {
        resolution.text = data.downloadResolution
        downloadDirectory.text = data.downloadDirectory
        downloadProxy.text = data.downloadProxy
        cookieFile.text = data.cookieFile
        browserCookies.currentIndex = settingsDialog.indexOfValue(browserCookies.model, data.browserCookies)
        downloadSubtitles.checked = data.downloadSubtitles
        subtitleLanguages.text = data.subtitleLanguages
        downloadCodec.currentIndex = settingsDialog.indexOfValue(downloadCodec.model, data.downloadCodec)
        asrEngine.currentIndex = settingsDialog.indexOfValue(asrEngine.model, data.asrEngine)
        asrCliPath.text = data.asrCliPath
        asrModelDirectory.text = data.asrModelDirectory
        asrModel.text = data.asrModel
        asrDevice.currentIndex = settingsDialog.indexOfValue(asrDevice.model, data.asrDevice)
        computeType.text = data.asrComputeType
        asrLanguage.text = data.asrLanguage
        asrSmartSplitLimit.value = Number(data.asrSmartSplitLimit)
        asrParallelChunks.currentIndex = settingsDialog.indexOfValue(asrParallelChunks.model, Number(data.asrParallelChunks))
        gptSoVitsRoot.text = data.gptSoVitsRoot
        gptSoVitsDevice.currentIndex = settingsDialog.indexOfValue(gptSoVitsDevice.model, data.gptSoVitsDevice)
    }
    clip: true
    ColumnLayout {
        width: page.availableWidth
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
                visible: downloadDirectory.text !== mediaflow.settingsController.builtInMediaDirectory
                onClicked: {
                    downloadDirectory.text = mediaflow.settingsController.builtInMediaDirectory
                    page.settingsDialog.updateDraft("downloadDirectory", downloadDirectory.text)
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
                onEditingFinished: page.settingsDialog.updateDraft("downloadResolution", resolution.text)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("代理服务器（可选）"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: downloadProxy
                Layout.fillWidth: true
                placeholderText: "http://127.0.0.1:7890"
                onEditingFinished: page.settingsDialog.updateDraft("downloadProxy", downloadProxy.text)
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
                onEditingFinished: page.settingsDialog.updateDraft("cookieFile", cookieFile.text)
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
                onActivated: page.settingsDialog.updateDraft("browserCookies", browserCookies.currentValue)
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
                        objectName: "cookieDomainField"
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
                    objectName: "cookieJsonField"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    placeholderText: qsTr("粘贴浏览器导出的 Cookie JSON 数组")
                    wrapMode: TextEdit.WrapAnywhere
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: mediaflow.settingsController.managedCookieStatus.exists
                              ? (mediaflow.settingsController.managedCookieStatus.valid
                                 ? qsTr("有效 · %1 小时").arg(mediaflow.settingsController.managedCookieStatus.ageHours)
                                 : qsTr("已过期 · %1 小时").arg(mediaflow.settingsController.managedCookieStatus.ageHours))
                              : qsTr("未保存")
                        color: mediaflow.settingsController.managedCookieStatus.valid ? Theme.success : Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppButton { text: qsTr("检查"); onClicked: mediaflow.settingsController.inspectManagedCookies(cookieDomain.text) }
                    AppButton {
                        text: qsTr("清除")
                        enabled: cookieDomain.text.trim().length > 0
                        onClicked: clearCookieDialog.request(
                            cookieDomain.text.trim(),
                            qsTr("清除这个网站的 Cookie？"),
                            qsTr("保存的 Cookie 会永久移除，之后下载可能需要重新登录并导出 Cookie。"),
                            qsTr("永久清除"))
                    }
                    AppButton {
                        primary: true
                        text: qsTr("保存")
                        enabled: cookieDomain.text.trim().length > 0 && cookieJsonText.text.trim().length > 0
                        onClicked: {
                            if (mediaflow.settingsController.saveManagedCookies(
                                    cookieDomain.text, cookieJsonText.text))
                                page.discardManagedCookieDraft()
                        }
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
                onActivated: page.settingsDialog.updateDraft("downloadCodec", downloadCodec.currentValue)
            }
        }
        AppCheckBox {
            id: downloadSubtitles
            text: qsTr("默认同时下载字幕并转换为 SRT")
            onToggled: page.settingsDialog.updateDraft("downloadSubtitles", downloadSubtitles.checked)
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("字幕语言"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: subtitleLanguages
                Layout.fillWidth: true
                placeholderText: qsTr("逗号分隔，例如 en,zh")
                onEditingFinished: page.settingsDialog.updateDraft("subtitleLanguages", subtitleLanguages.text)
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
                Text { text: qsTr("可选运行组件"); color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("yt-dlp：%1。语音组件按需下载，不进入 MediaFlow Pro 安装包。")
                        .arg(mediaflow.settingsController.runtimeToolStatus.ytDlpVersion || qsTr("未检测"))
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                AppCheckBox {
                    id: selectXxlDownload
                    objectName: "selectFasterWhisperDownload"
                    Layout.fillWidth: true
                    enabled: page.settingsDialog.runtimeComponent("faster-whisper-xxl").supported !== false
                    text: qsTr("Faster-Whisper XXL · %1 GiB · %2")
                        .arg(page.settingsDialog.runtimeComponent("faster-whisper-xxl").downloadGiB || "1.33")
                        .arg(page.settingsDialog.runtimeComponent("faster-whisper-xxl").ready ? qsTr("可用") : qsTr("未就绪"))
                }
                Text {
                    Layout.fillWidth: true
                    text: page.settingsDialog.runtimeComponent("faster-whisper-xxl").path
                        || page.settingsDialog.runtimeComponent("faster-whisper-xxl").reason
                        || qsTr("尚未安装或选择本地程序")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    elide: Text.ElideMiddle
                }
                AppCheckBox {
                    id: selectGptSoVitsDownload
                    objectName: "selectGptSoVitsDownload"
                    Layout.fillWidth: true
                    enabled: page.settingsDialog.runtimeComponent("gpt-sovits-v2pro").supported !== false
                    text: qsTr("GPT-SoVITS v2Pro · %1 GiB · %2")
                        .arg(page.settingsDialog.runtimeComponent("gpt-sovits-v2pro").downloadGiB || "7.59")
                        .arg(page.settingsDialog.runtimeComponent("gpt-sovits-v2pro").ready ? qsTr("可用") : qsTr("未就绪"))
                }
                Text {
                    Layout.fillWidth: true
                    text: page.settingsDialog.runtimeComponent("gpt-sovits-v2pro").path
                        || page.settingsDialog.runtimeComponent("gpt-sovits-v2pro").reason
                        || qsTr("尚未安装或选择本地目录")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    elide: Text.ElideMiddle
                }
                Text {
                    Layout.fillWidth: true
                    text: mediaflow.settingsController.runtimeToolStatus.cudaSummary || qsTr("尚未检测 CUDA")
                    color: mediaflow.settingsController.runtimeToolStatus.cudaStatus === "ready" ? Theme.success : Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                AppProgressBar {
                    Layout.fillWidth: true
                    visible: mediaflow.settingsController.runtimeToolStatus.busy
                    from: 0; to: 100
                    indeterminate: mediaflow.settingsController.runtimeToolStatus.progressMode !== "determinate"
                    value: Number(mediaflow.settingsController.runtimeToolStatus.progressValue || 0)
                }
                Text {
                    visible: mediaflow.settingsController.runtimeToolStatus.busy
                    text: mediaflow.settingsController.runtimeToolStatus.message || qsTr("正在处理")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton { Layout.fillWidth: true; enabled: !mediaflow.settingsController.runtimeToolStatus.busy; text: qsTr("检测 CUDA"); onClicked: mediaflow.settingsController.inspectRuntimeTools() }
                    AppButton { Layout.fillWidth: true; enabled: !mediaflow.settingsController.runtimeToolStatus.busy; text: qsTr("更新 yt-dlp"); onClicked: mediaflow.settingsController.updateYtDlp() }
                    AppButton {
                        objectName: "downloadSelectedRuntimeComponents"
                        Layout.fillWidth: true
                        enabled: !mediaflow.settingsController.runtimeToolStatus.busy
                            && (selectXxlDownload.checked || selectGptSoVitsDownload.checked)
                        text: qsTr("下载所选组件")
                        onClicked: {
                            const selected = []
                            if (selectXxlDownload.checked)
                                selected.push("faster-whisper-xxl")
                            if (selectGptSoVitsDownload.checked)
                                selected.push("gpt-sovits-v2pro")
                            mediaflow.settingsController.installRuntimeComponents(selected)
                        }
                    }
                    AppButton { Layout.fillWidth: true; enabled: !mediaflow.settingsController.runtimeToolStatus.busy && page.settingsDialog.runtimeComponent("faster-whisper-xxl").ready; text: qsTr("预热 XXL"); onClicked: mediaflow.settingsController.prewarmAsrCli() }
                    AppButton { visible: mediaflow.settingsController.runtimeToolStatus.busy; danger: true; text: qsTr("取消"); onClicked: mediaflow.settingsController.cancelRuntimeToolOperation() }
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
                onActivated: page.settingsDialog.updateDraft("asrEngine", asrEngine.currentValue)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: asrEngine.currentValue === "faster_whisper_cli"
            Text { text: qsTr("转录程序路径"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: asrCliPath
                Layout.fillWidth: true
                placeholderText: qsTr("留空使用环境文件中的运行时安装目录")
                color: Theme.text
                onEditingFinished: page.settingsDialog.updateDraft("asrCliPath", asrCliPath.text)
            }
            AppButton { text: qsTr("选择"); onClicked: xxlExecutableDialog.open() }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("模型目录"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: asrModelDirectory
                objectName: "asrModelDirectoryField"
                Layout.fillWidth: true
                placeholderText: qsTr("留空使用环境文件中的运行时模型目录")
                color: Theme.text
                onEditingFinished: page.settingsDialog.updateDraft("asrModelDirectory", asrModelDirectory.text)
            }
            AppButton { text: qsTr("选择"); onClicked: asrModelDirectoryDialog.open() }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("模型"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: asrModel
                Layout.fillWidth: true
                placeholderText: qsTr("例如 large-v3-turbo")
                color: Theme.text
                onEditingFinished: page.settingsDialog.updateDraft("asrModel", asrModel.text)
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
                onActivated: page.settingsDialog.updateDraft("asrDevice", asrDevice.currentValue)
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
                onEditingFinished: page.settingsDialog.updateDraft("asrComputeType", computeType.text)
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
                onEditingFinished: page.settingsDialog.updateDraft("asrLanguage", asrLanguage.text)
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
                onValueChanged: page.settingsDialog.updateDraft("asrSmartSplitLimit", asrSmartSplitLimit.value)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("长音频并行分块"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppComboBox {
                id: asrParallelChunks
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: mediaflow.settingsController.asrParallelOptions
                onActivated: page.settingsDialog.updateDraft("asrParallelChunks", asrParallelChunks.currentValue)
            }
        }
        Text { text: qsTr("声音克隆"); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.weight: Font.DemiBold }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("GPT-SoVITS 根目录"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppTextField {
                id: gptSoVitsRoot
                objectName: "gptSoVitsRootField"
                Layout.fillWidth: true
                placeholderText: qsTr("留空使用环境文件中的运行时安装目录")
                color: Theme.text
                onEditingFinished: page.settingsDialog.updateDraft("gptSoVitsRoot", gptSoVitsRoot.text)
            }
            AppButton { text: qsTr("选择"); onClicked: gptSoVitsRootDialog.open() }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("GPT-SoVITS 设备"); color: Theme.textMuted; Layout.preferredWidth: 180 }
            AppComboBox {
                id: gptSoVitsDevice
                Layout.fillWidth: true
                textRole: "text"; valueRole: "value"
                model: [{text: qsTr("自动"), value: "auto"}, {text: "CUDA", value: "cuda"}, {text: "CPU", value: "cpu"}]
                onActivated: page.settingsDialog.updateDraft("gptSoVitsDevice", gptSoVitsDevice.currentValue)
            }
        }
    }

    FolderDialog {
        id: downloadDirectoryDialog
        title: qsTr("选择媒体默认保存位置")
        onAccepted: {
            downloadDirectory.text = selectedFolder.toLocalFile()
            page.settingsDialog.updateDraft("downloadDirectory", downloadDirectory.text)
        }
    }
    FileDialog {
        id: xxlExecutableDialog
        title: qsTr("选择 Faster-Whisper XXL 可执行文件")
        fileMode: FileDialog.OpenFile
        nameFilters: Qt.platform.os === "windows"
            ? [qsTr("Faster-Whisper XXL (faster-whisper-xxl.exe)"), qsTr("可执行文件 (*.exe)")]
            : [qsTr("可执行文件 (*)")]
        onAccepted: {
            asrCliPath.text = selectedFile.toLocalFile()
            page.settingsDialog.updateDraft("asrCliPath", asrCliPath.text)
        }
    }
    FolderDialog {
        id: asrModelDirectoryDialog
        title: qsTr("选择 Faster-Whisper 模型目录")
        onAccepted: {
            asrModelDirectory.text = selectedFolder.toLocalFile()
            page.settingsDialog.updateDraft("asrModelDirectory", asrModelDirectory.text)
        }
    }
    FolderDialog {
        id: gptSoVitsRootDialog
        title: qsTr("选择 GPT-SoVITS v2Pro 根目录")
        onAccepted: {
            gptSoVitsRoot.text = selectedFolder.toLocalFile()
            page.settingsDialog.updateDraft("gptSoVitsRoot", gptSoVitsRoot.text)
        }
    }
}
