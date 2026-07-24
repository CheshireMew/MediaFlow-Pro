import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Dialog {
    id: root
    objectName: "mobileImportDialog"
    anchors.centerIn: parent
    width: 430
    modal: true
    title: qsTr("手机扫码导入")
    standardButtons: Dialog.Close
    onClosed: mediaController.stopMobileImport()
    contentItem: ColumnLayout {
        spacing: 10
        Image {
            objectName: "mobileImportQrImage"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 230
            Layout.preferredHeight: 230
            source: mediaController.mobileImportQrUrl
            fillMode: Image.PreserveAspectFit
            cache: false
        }
        Text {
            Layout.fillWidth: true
            text: qsTr("手机和电脑连接同一局域网后扫码，选择视频、录音或图片即可发送。")
            color: Theme.text
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
        }
        TextEdit {
            objectName: "mobileImportUrlText"
            Layout.fillWidth: true
            text: mediaController.mobileImportUrl
            color: Theme.accent
            readOnly: true
            selectByMouse: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WrapAnywhere
        }
        Text {
            Layout.fillWidth: true
            text: qsTr("本次已接收 %1 个文件").arg(mediaController.mobileImportReceivedCount)
            color: Theme.textMuted
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
