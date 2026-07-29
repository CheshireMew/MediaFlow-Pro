import QtQuick
import QtQuick.Controls
import ".."

Dialog {
    id: control
    padding: Theme.dialogPadding
    dim: modal

    Overlay.modal: Rectangle {
        color: Theme.overlay
    }

    background: Rectangle {
        color: Theme.dialog
        radius: Theme.radiusLarge
        border.color: Theme.borderStrong
        border.width: 1
    }

    header: Rectangle {
        visible: control.title.length > 0
        implicitHeight: visible ? 52 : 0
        color: Theme.dialog
        radius: Theme.radiusLarge

        Text {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Theme.dialogPadding
            anchors.rightMargin: Theme.dialogPadding
            text: control.title
            color: Theme.text
            font.pixelSize: Theme.fontSizeTitleSmall
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Theme.divider
        }
    }

    footer: DialogButtonBox {
        id: buttonBox
        visible: control.standardButtons !== Dialog.NoButton
        implicitHeight: visible ? 58 : 0
        standardButtons: control.standardButtons
        alignment: Qt.AlignRight
        spacing: 8
        leftPadding: Theme.dialogPadding
        rightPadding: Theme.dialogPadding
        topPadding: 10
        bottomPadding: 10

        delegate: AppButton {
            primary: DialogButtonBox.buttonRole === DialogButtonBox.AcceptRole
                || DialogButtonBox.buttonRole === DialogButtonBox.YesRole
                || DialogButtonBox.buttonRole === DialogButtonBox.ApplyRole
            danger: DialogButtonBox.buttonRole === DialogButtonBox.DestructiveRole
        }

        background: Rectangle {
            color: Theme.dialog
            radius: Theme.radiusLarge
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: 1
                color: Theme.divider
            }
        }

        onAccepted: control.accept()
        onRejected: control.reject()
    }
}
