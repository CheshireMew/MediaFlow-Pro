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
    closePolicy: Popup.CloseOnEscape
    anchors.centerIn: parent
    width: Math.min(780, parent ? parent.width - 48 : 780)
    height: Math.min(820, parent ? parent.height - 48 : 820)
    property var llmProviderPresets: mediaflow.settingsController.llmProviderPresets
    property var settingsDraft: mediaflow.settingsController.settingsDraft
    property bool syncingFromController: false

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
                text: qsTr("更改会自动保存")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppButton {
                objectName: "settingsCloseButton"
                text: qsTr("关闭")
                enabled: mediaPage.valid
                onClicked: root.close()
            }
        }
    }
}
