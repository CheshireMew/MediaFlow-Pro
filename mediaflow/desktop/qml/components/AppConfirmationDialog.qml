import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

AppDialog {
    id: root

    property string actionId: ""
    property string message: ""
    property string confirmText: qsTr("确认")
    property bool destructive: true
    signal confirmed(string actionId)

    function request(nextActionId, nextTitle, nextMessage, nextConfirmText) {
        actionId = String(nextActionId || "")
        title = String(nextTitle || "")
        message = String(nextMessage || "")
        confirmText = String(nextConfirmText || qsTr("确认"))
        open()
    }

    anchors.centerIn: parent
    width: Math.min(460, parent ? parent.width - 32 : 460)
    modal: true
    closePolicy: Popup.CloseOnEscape
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        width: root.availableWidth
        spacing: 16

        Text {
            Layout.fillWidth: true
            text: root.message
            color: Theme.text
            font.pixelSize: Theme.fontSizeBody
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton {
                text: qsTr("取消")
                onClicked: root.close()
            }
            AppButton {
                objectName: "confirmationActionButton"
                text: root.confirmText
                danger: root.destructive
                primary: !root.destructive
                onClicked: {
                    const acceptedAction = root.actionId
                    root.close()
                    root.confirmed(acceptedAction)
                }
            }
        }
    }
}
