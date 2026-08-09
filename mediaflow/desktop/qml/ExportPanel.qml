import QtQuick
import QtQuick.Controls
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
    readonly property bool canStartTasks: Boolean(workspaceController.actionCapabilities.canStartTasks)
    readonly property bool canExportSequence: root.canStartTasks
        && exportController.canExportSequence
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

    ExportFileDialogs {
        id: exportFileDialogs
        format: root.selectedFormat()
        options: exportSettings.exportOptions()
        actionsEnabled: root.canExportSequence
    }

    ExportSequenceSummary {
        Layout.fillWidth: true
    }
    ExportTargetBar {
        id: exportTarget
        Layout.fillWidth: true
        formats: root.formats
        taskActive: root.taskActive
        actionsEnabled: root.canExportSequence
        defaultDirectory: exportController.defaultExportDirectory
        onExportRequested: {
            if (root.canExportSequence)
                exportController.exportSequenceToDefaultLocation(
                    root.selectedFormat().value,
                    root.selectedFormat().suffix,
                    exportSettings.exportOptions());
        }
        onSaveAsRequested: {
            if (root.canExportSequence)
                exportFileDialogs.openSequenceDialog();
        }
    }
    AppButton {
        objectName: "exportFcpxmlButton"
        Layout.fillWidth: true
        text: qsTr("导出 FCPXML（Final Cut Pro / DaVinci Resolve）")
        enabled: root.canExportSequence
        onClicked: exportFileDialogs.openFcpxmlDialog()
    }
    AppButton {
        objectName: "copyExportCliRequestButton"
        Layout.fillWidth: true
        text: qsTr("复制当前导出为 CLI 请求")
        enabled: root.canExportSequence
        onClicked: automationController.copyCurrentExportRequest(
            root.selectedFormat().value,
            root.selectedFormat().suffix,
            exportSettings.exportOptions())
    }
    AppScrollView {
        id: settingsScroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        contentWidth: availableWidth
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
