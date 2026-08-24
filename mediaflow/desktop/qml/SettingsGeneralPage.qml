import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"
AppScrollView {
    id: page
    property var settingsDialog
    property string selectedRuntimeDirectory: ""
    readonly property bool modalOpen: runtimeChangeDialog.opened

    function sync(data) {
        language.currentIndex = settingsDialog.indexOfValue(language.model, data.language)
        theme.currentIndex = settingsDialog.indexOfValue(theme.model, data.theme)
        autoContinue.checked = data.autoContinue
        defaultProjectDirectory.text = data.defaultProjectDirectory
        defaultImportDirectory.text = data.defaultImportDirectory
        automaticProxy.checked = data.automaticProxy
        previewQuality.currentIndex = settingsDialog.indexOfValue(previewQuality.model, data.previewQuality)
        hdrPreview.checked = data.hdrPreview
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
        Text {
            Layout.fillWidth: true
            visible: mediaflow.settingsController.languageRestartRequired
            text: qsTr("界面语言将在重新启动 MediaFlow Pro 后生效。")
            color: Theme.warning
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
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
        Text {
            text: qsTr("运行环境")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodyLarge
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: qsTr("媒体组件、浏览器、模型、代理文件和缓存保存在这里。迁移会在下次启动、运行时尚未加载前执行，并保留旧目录。")
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("当前位置")
                color: Theme.textMuted
                Layout.preferredWidth: 150
            }
            AppTextField {
                objectName: "runtimeDirectoryField"
                Layout.fillWidth: true
                readOnly: true
                text: String(mediaflow.runtimeSettingsController.runtimeDirectoryInfo.currentPath || "")
                ToolTip.visible: hovered
                ToolTip.text: text
            }
            AppButton {
                objectName: "selectRuntimeDirectoryButton"
                text: qsTr("迁移…")
                enabled: !mediaflow.runtimeSettingsController.runtimeDirectoryInfo.managedExternally
                onClicked: runtimeDirectoryDialog.open()
            }
        }
        Text {
            Layout.fillWidth: true
            text: mediaflow.runtimeSettingsController.runtimeDirectoryInfo.managedExternally
                ? qsTr("当前目录由 MEDIAFLOW_RUNTIME_DIR 或开发环境配置管理，请修改对应配置。")
                : qsTr("当前磁盘可用空间：%1").arg(
                    mediaflow.runtimeSettingsController.runtimeDirectoryInfo.freeLabel || qsTr("未知"))
            color: mediaflow.runtimeSettingsController.runtimeDirectoryInfo.managedExternally
                ? Theme.warning : Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        Panel {
            Layout.fillWidth: true
            visible: String(mediaflow.runtimeSettingsController.runtimeDirectoryInfo.pendingPath || "").length > 0
            implicitHeight: pendingRuntimeContent.implicitHeight + 18
            ColumnLayout {
                id: pendingRuntimeContent
                anchors.fill: parent
                anchors.margins: 9
                spacing: 6
                Text {
                    Layout.fillWidth: true
                    text: mediaflow.runtimeSettingsController.runtimeDirectoryInfo.pendingMigration
                        ? qsTr("下次启动将迁移到：%1").arg(
                            mediaflow.runtimeSettingsController.runtimeDirectoryInfo.pendingPath)
                        : qsTr("下次启动将切换到：%1").arg(
                            mediaflow.runtimeSettingsController.runtimeDirectoryInfo.pendingPath)
                    color: Theme.text
                    wrapMode: Text.WrapAnywhere
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("切换完成前会继续使用当前目录。")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppButton {
                        text: qsTr("取消变更")
                        onClicked: mediaflow.runtimeSettingsController.cancelRuntimeDirectoryChange()
                    }
                }
            }
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
    FolderDialog {
        id: runtimeDirectoryDialog
        title: qsTr("选择新的运行环境目录")
        onAccepted: {
            page.selectedRuntimeDirectory = selectedFolder.toLocalFile()
            runtimeChangeDialog.open()
        }
    }
    AppDialog {
        id: runtimeChangeDialog
        parent: Overlay.overlay
        anchors.centerIn: Overlay.overlay
        width: Math.min(560, Overlay.overlay ? Overlay.overlay.width - 48 : 560)
        title: qsTr("如何使用新的运行环境目录？")
        modal: true
        closePolicy: Popup.CloseOnEscape
        standardButtons: Dialog.NoButton
        contentItem: ColumnLayout {
            width: runtimeChangeDialog.availableWidth
            spacing: 12
            Text {
                Layout.fillWidth: true
                text: page.selectedRuntimeDirectory
                color: Theme.text
                font.family: Theme.monoFontFamily
                wrapMode: Text.WrapAnywhere
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("推荐迁移现有数据。复制和校验会在下次启动时进行，旧目录不会删除。只有目标目录已经包含完整运行环境时，才应选择“仅切换”。")
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("取消")
                    onClicked: runtimeChangeDialog.close()
                }
                AppButton {
                    text: qsTr("仅切换")
                    onClicked: {
                        if (mediaflow.runtimeSettingsController.scheduleRuntimeDirectoryChange(
                                page.selectedRuntimeDirectory, false))
                            runtimeChangeDialog.close()
                    }
                }
                AppButton {
                    primary: true
                    text: qsTr("迁移现有数据")
                    onClicked: {
                        if (mediaflow.runtimeSettingsController.scheduleRuntimeDirectoryChange(
                                page.selectedRuntimeDirectory, true))
                            runtimeChangeDialog.close()
                    }
                }
            }
        }
    }
}
