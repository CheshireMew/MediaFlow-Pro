import QtQuick
import QtQuick.Layouts
import ".."

ColumnLayout {
    id: root
    property var formats: []
    property alias currentIndex: format.currentIndex
    property bool taskActive: false
    property string defaultDirectory: ""
    readonly property var selectedFormat: formats[Math.max(0, currentIndex)]
    signal exportRequested
    signal saveAsRequested
    spacing: 10

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text {
            text: qsTr("格式")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            Layout.preferredWidth: 58
        }
        AppComboBox {
            id: format
            Layout.fillWidth: true
            model: root.formats
            textRole: "label"
            valueRole: "value"
        }
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text {
            text: qsTr("保存位置")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            Layout.preferredWidth: 58
        }
        PathDisplay {
            objectName: "exportDefaultPath"
            Layout.fillWidth: true
            text: root.defaultDirectory
        }
    }
    RowLayout {
        Layout.fillWidth: true
        AppButton {
            objectName: "exportToProjectButton"
            Layout.fillWidth: true
            primary: true
            text: root.selectedFormat && root.selectedFormat.value === "audio"
                ? qsTr("开始导出音频") : qsTr("开始导出视频")
            enabled: !root.taskActive
            onClicked: root.exportRequested()
        }
        AppButton {
            objectName: "exportAsButton"
            text: qsTr("另存为…")
            enabled: !root.taskActive
            onClicked: root.saveAsRequested()
        }
    }
}
