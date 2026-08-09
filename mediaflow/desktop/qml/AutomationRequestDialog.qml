import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "automationRequestDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(760, parent ? parent.width - 48 : 760)
    height: Math.min(720, parent ? parent.height - 48 : 720)
    modal: true
    title: automationController.requestTitle || qsTr("CLI 请求")
    standardButtons: Dialog.Close

    Connections {
        target: automationController
        function onRequestPrepared() { root.open() }
    }

    contentItem: ColumnLayout {
        spacing: 9
        Text {
            Layout.fillWidth: true
            text: qsTr("请求已复制到剪贴板。保存为 request.json 后可直接执行：")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        AppTextField {
            Layout.fillWidth: true
            readOnly: true
            text: automationController.executionCommand
            selectByMouse: true
        }
        TextArea {
            id: requestPreview
            objectName: "automationRequestPreview"
            Layout.fillWidth: true
            Layout.fillHeight: true
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.NoWrap
            text: automationController.requestPreviewJson
            color: Theme.text
            selectionColor: Theme.accent
            selectedTextColor: Theme.onAccent
            font.family: "Consolas"
            font.pixelSize: 12
            background: Rectangle {
                color: Theme.field
                border.color: Theme.border
                radius: Theme.radiusSmall
            }
        }
    }
}
