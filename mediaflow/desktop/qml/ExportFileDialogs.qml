import QtQuick
import QtQuick.Dialogs

Item {
    id: root
    visible: false
    property var format: ({})
    property var options: ({})
    property bool actionsEnabled: true

    function openSequenceDialog() {
        sequenceDialog.open();
    }

    function openFcpxmlDialog() {
        fcpxmlDialog.open();
    }

    FileDialog {
        id: sequenceDialog
        title: qsTr("导出序列")
        fileMode: FileDialog.SaveFile
        defaultSuffix: root.format.suffix || ""
        nameFilters: root.format.filter ? [root.format.filter] : []
        onAccepted: {
            if (root.actionsEnabled) {
                exportController.exportSequenceWithOptions(
                    String(root.format.value || ""),
                    selectedFile.toString(),
                    root.options);
            }
        }
    }

    FileDialog {
        id: fcpxmlDialog
        title: qsTr("导出 FCPXML")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "fcpxml"
        nameFilters: [qsTr("Final Cut Pro XML (*.fcpxml)")]
        onAccepted: {
            if (root.actionsEnabled)
                exportController.exportFcpxml(selectedFile.toString());
        }
    }
}
