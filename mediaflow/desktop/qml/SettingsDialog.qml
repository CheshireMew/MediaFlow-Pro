import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "settingsDialog"
    modal: true
    title: qsTr("设置")
    closePolicy: Popup.NoAutoClose
    anchors.centerIn: parent
    width: Math.min(780, parent ? parent.width - 48 : 780)
    height: Math.min(820, parent ? parent.height - 48 : 820)
    property var llmProviderPresets: mediaflow.settingsController.llmProviderPresets
    property var settingsDraft: mediaflow.settingsController.settingsDraft
    property bool syncingFromController: false
    readonly property bool explicitDraftDirty: aiPage.providerDirty
        || mediaPage.managedCookieDirty

    function requestClose() {
        settingsDraft.flush()
        if (explicitDraftDirty) {
            discardDraftDialog.request(
                "close",
                qsTr("放弃未保存的内容？"),
                qsTr("LLM 提供商或 Cookie 表单中仍有未保存内容。关闭后这些内容会丢失。"),
                qsTr("放弃并关闭"))
            return
        }
        close()
    }
    function indexOfValue(model, value) {
        for (var i = 0; i < model.length; ++i) {
            if (model[i].value === value)
                return i
        }
        return 0
    }
    function runtimeComponent(componentId) {
        const status = mediaflow.settingsController.runtimeToolStatus || {}
        const components = status.components || {}
        return components[componentId] || {}
    }
    function syncFromController() {
        syncForm(settingsDraft.data)
        loadLlmProvider()
    }

    function syncForm(data) {
        generalPage.sync(data)
        mediaPage.sync(data)
        aiPage.sync(data)
    }

    function syncDraftFromController() {
        if (!visible)
            return
        syncingFromController = true
        syncFromController()
        Qt.callLater(function() {
            root.syncingFromController = false
        })
    }

    function loadLlmProvider() {
        aiPage.loadLlmProvider()
    }

    function updateDraft(field, value) {
        if (!syncingFromController && visible)
            settingsDraft.update(field, value)
    }

    onOpened: {
        syncingFromController = true
        settingsDraft.begin()
        syncFromController()
        Qt.callLater(function() { root.syncingFromController = false })
    }
    onClosed: settingsDraft.finish()

    Shortcut {
        sequence: "Escape"
        enabled: root.opened && !discardDraftDialog.opened
            && !generalPage.modalOpen && !aiPage.modalOpen && !mediaPage.modalOpen
        onActivated: root.requestClose()
    }

    AppConfirmationDialog {
        id: discardDraftDialog
        objectName: "discardSettingsDraftDialog"
        onConfirmed: function (actionId) {
            if (actionId !== "close")
                return
            aiPage.discardProviderChanges()
            mediaPage.discardManagedCookieDraft()
            root.close()
        }
    }

    Connections {
        target: mediaflow.settingsController
        function onSelectionChanged() { root.loadLlmProvider() }
    }
    Connections {
        target: root.settingsDraft
        function onChanged() { root.syncDraftFromController() }
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

            SettingsGeneralPage {
                id: generalPage
                settingsDialog: root
            }
            SettingsMediaPage {
                id: mediaPage
                settingsDialog: root
            }
            SettingsAiPage {
                id: aiPage
                settingsDialog: root
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "settingsAutoSaveNotice"
                Layout.fillWidth: true
                text: mediaflow.settingsController.languageRestartRequired
                    ? qsTr("常规设置已保存；界面语言将在重启后生效")
                    : root.explicitDraftDirty
                    ? qsTr("提供商或 Cookie 有未保存内容")
                    : root.settingsDraft.dirty
                    ? qsTr("正在保存更改…")
                    : qsTr("常规设置自动保存；提供商和 Cookie 请点击各自的保存按钮")
                color: root.explicitDraftDirty || mediaflow.settingsController.languageRestartRequired
                    ? Theme.warning : Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppButton {
                objectName: "settingsCloseButton"
                text: qsTr("关闭")
                enabled: mediaPage.valid
                onClicked: root.requestClose()
            }
        }
    }
}
