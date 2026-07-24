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
    property var previewOptions: ({})
    property var taskData: ({})
    readonly property bool taskActive: taskData.status === "pending"
        || taskData.status === "running" || taskData.status === "paused"
    signal previewConfigurationChanged(var options)
    spacing: 10

    function selectedFormat() {
        return exportTarget.selectedFormat
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
            exportTarget.currentIndex = index
            break
        }
        Qt.callLater(function() { exportSettings.restore(value) })
    }

    function refreshTask() {
        taskData = taskController.latestTask(
            "export", workspaceController.activeSequenceId);
    }

    Component.onCompleted: {
        Qt.callLater(root.restorePreset);
        refreshTask();
    }

    Connections {
        target: exportController
        function onProjectStateChanged() { Qt.callLater(root.restorePreset) }
    }
    Connections {
        target: taskController
        function onTasksChanged() { root.refreshTask(); }
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
    FileDialog {
        id: fcpxmlDialog
        title: qsTr("导出 FCPXML")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "fcpxml"
        nameFilters: [qsTr("Final Cut Pro XML (*.fcpxml)")]
        onAccepted: exportController.exportFcpxml(selectedFile.toString())
    }

    ExportSequenceSummary {
        Layout.fillWidth: true
    }
    ExportTargetBar {
        id: exportTarget
        Layout.fillWidth: true
        formats: root.formats
        taskActive: root.taskActive
        defaultDirectory: exportController.defaultExportDirectory
        onExportRequested: exportController.exportSequenceToDefaultLocation(
            root.selectedFormat().value,
            root.selectedFormat().suffix,
            exportSettings.exportOptions())
        onSaveAsRequested: saveDialog.open()
    }
    AppButton {
        objectName: "exportFcpxmlButton"
        Layout.fillWidth: true
        text: qsTr("导出 FCPXML（Final Cut Pro / DaVinci Resolve）")
        onClicked: fcpxmlDialog.open()
    }
    ScrollView {
        id: settingsScroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        ColumnLayout {
            width: settingsScroll.availableWidth
            spacing: 10
            ContextTaskCard {
                objectName: "exportTaskPanel"
                Layout.fillWidth: true
                taskData: root.taskData
                fallbackTitle: qsTr("导出任务")
            }
            ExportHistoryPanel { }
            ExportSettings {
                id: exportSettings
                Layout.fillWidth: true
                format: root.selectedFormat()
                onOptionsChanged: function (options) {
                    root.previewOptions = options;
                    root.previewConfigurationChanged(options);
                }
            }
        }
    }
}
