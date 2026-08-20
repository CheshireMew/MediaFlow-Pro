import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root

    property bool canEdit: false
    property string itemKind: ""
    property string itemId: ""
    property string itemName: ""
    readonly property bool modalOpen: nameDialog.opened

    function open(kind, nextItemId, nextItemName) {
        itemKind = String(kind)
        itemId = String(nextItemId)
        itemName = String(nextItemName)
        contextMenu.popup()
    }

    function removeItem() {
        if (itemKind === "marker")
            mediaflow.timelineStructureController.removeTimelineMarker(itemId)
        else if (itemKind === "range")
            mediaflow.timelineStructureController.removeTimelineRange(itemId)
    }

    AppMenu {
        id: contextMenu
        objectName: "timelineItemContextMenu"
        AppMenuItem {
            objectName: "renameTimelineItemMenuItem"
            text: root.itemKind === "marker"
                ? qsTr("重命名标记…") : qsTr("重命名选区…")
            enabled: root.canEdit && root.itemId.length > 0
            onTriggered: nameDialog.request(root.itemKind, root.itemId, root.itemName)
        }
        AppMenuSeparator {}
        AppMenuItem {
            objectName: "removeTimelineItemMenuItem"
            text: root.itemKind === "marker"
                ? qsTr("删除标记（可撤销）") : qsTr("删除选区（可撤销）")
            enabled: root.canEdit && root.itemId.length > 0
            onTriggered: root.removeItem()
        }
    }

    AppDialog {
        id: nameDialog
        objectName: "timelineItemNameDialog"
        property string targetKind: ""
        property string targetId: ""
        function request(kind, nextItemId, currentName) {
            targetKind = String(kind)
            targetId = String(nextItemId)
            title = targetKind === "marker" ? qsTr("重命名标记") : qsTr("重命名选区")
            nameField.text = String(currentName)
            open()
            Qt.callLater(function() {
                nameField.forceActiveFocus()
                nameField.selectAll()
            })
        }
        anchors.centerIn: parent
        width: Math.min(420, root.width - 32)
        modal: true
        closePolicy: Popup.CloseOnEscape
        standardButtons: Dialog.NoButton
        contentItem: ColumnLayout {
            width: nameDialog.availableWidth
            spacing: 12
            AppTextField {
                id: nameField
                objectName: "timelineItemNameField"
                Layout.fillWidth: true
                placeholderText: qsTr("输入一个容易识别的名称")
                onAccepted: saveButton.clicked()
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("取消")
                    onClicked: nameDialog.close()
                }
                AppButton {
                    id: saveButton
                    primary: true
                    text: qsTr("保存")
                    enabled: nameField.text.trim().length > 0
                    onClicked: {
                        if (nameDialog.targetKind === "marker")
                            mediaflow.timelineStructureController.renameTimelineMarker(
                                nameDialog.targetId, nameField.text)
                        else
                            mediaflow.timelineStructureController.renameTimelineRange(
                                nameDialog.targetId, nameField.text)
                        nameDialog.close()
                    }
                }
            }
        }
    }
}
