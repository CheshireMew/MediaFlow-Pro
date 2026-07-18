import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    objectName: "exportPanel"

    property var formats: exportController.exportFormatOptions

    spacing: 10

    function selectedFormat() {
        return root.formats[Math.max(0, format.currentIndex)]
    }

    function restorePreset() {
        const value = exportController.exportPresetData
        if (!value || !value.format)
            return
        const advanced = value.advanced || {}
        for (var index = 0; index < root.formats.length; ++index) {
            const candidate = root.formats[index]
            if (candidate.value !== value.format)
                continue
            if (candidate.value === "audio" && candidate.container !== value.container)
                continue
            if (candidate.value === "prores"
                    && Number(candidate.profile) !== Number(advanced.profile ?? 3))
                continue
            format.currentIndex = index
            break
        }
        Qt.callLater(function() { exportSettings.restore(value) })
    }

    Component.onCompleted: Qt.callLater(root.restorePreset)

    Connections {
        target: exportController
        function onProjectStateChanged() { Qt.callLater(root.restorePreset) }
    }

    FileDialog {
        id: saveDialog
        title: qsTr("导出序列")
        fileMode: FileDialog.SaveFile
        defaultSuffix: root.selectedFormat().suffix
        nameFilters: [root.selectedFormat().filter]
        onAccepted: exportController.exportSequenceWithOptions(
            root.selectedFormat().value,
            selectedFile.toString(),
            exportSettings.exportOptions())
    }

    Text {
        text: qsTr("导出")
        color: Theme.text
        font.pixelSize: Theme.fontSizeSection
        font.weight: Font.DemiBold
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 94
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 11
            spacing: 5
            Text { text: qsTr("当前序列"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
            Text {
                Layout.fillWidth: true
                text: workspaceController.profileLabel
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.DemiBold
            }
            Text {
                visible: workspaceController.profileConfirmed
                text: workspaceController.colorMode === "hdr10_bt2020_pq"
                    ? qsTr("HDR10 · BT.2020 · PQ") : qsTr("SDR · BT.709")
                color: workspaceController.colorMode === "hdr10_bt2020_pq"
                    ? Theme.warning : Theme.accentHover
                font.pixelSize: Theme.fontSizeCaption
            }
        }
    }
    Text { text: qsTr("格式"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
    AppComboBox {
        id: format
        Layout.fillWidth: true
        model: root.formats
        textRole: "label"
        valueRole: "value"
    }
    ScrollView {
        id: settingsScroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        ExportSettings {
            id: exportSettings
            width: settingsScroll.availableWidth
            format: root.selectedFormat()
        }
    }
    RowLayout {
        Layout.fillWidth: true
        AppButton {
            objectName: "exportToProjectButton"
            Layout.fillWidth: true
            primary: true
            text: qsTr("导出到项目")
            onClicked: exportController.exportSequenceToDefaultLocation(
                root.selectedFormat().value,
                root.selectedFormat().suffix,
                exportSettings.exportOptions())
        }
        AppButton {
            objectName: "exportAsButton"
            text: qsTr("另存为…")
            onClicked: saveDialog.open()
        }
    }
    Text {
        Layout.fillWidth: true
        text: qsTr("默认保存到 %1，文件名会自动避开已有结果。也可以使用“另存为”指定其他位置。").arg(
            exportController.defaultExportDirectory)
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }
}
