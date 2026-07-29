import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    objectName: "sequenceActions"

    readonly property string mainSequencePrefix: qsTr("主")
    readonly property string shortSequencePrefix: qsTr("短")
    property bool actionsEnabled: true
    signal createShortRequested
    signal editProfileRequested

    implicitWidth: sequenceMenuButton.implicitWidth
    implicitHeight: sequenceMenuButton.implicitHeight

    function sequenceDisplayName(kind, name) {
        if (kind === "main" && name === "主序列")
            return qsTr("主序列");
        if (kind === "short" && name.indexOf("短视频") === 0)
            return qsTr("短视频") + name.slice(3);
        return (kind === "short" ? root.shortSequencePrefix : root.mainSequencePrefix) + " · " + name;
    }

    function activeSequenceName() {
        for (let index = 0; index < workspaceController.sequencesModel.rowCount(); index++) {
            const sequence = workspaceController.sequencesModel.get(index);
            if (sequence.sequenceId === workspaceController.activeSequenceId)
                return root.sequenceDisplayName(sequence.kind, sequence.name);
        }
        return qsTr("序列");
    }

    AppMenuButton {
        id: sequenceMenuButton
        objectName: "sequenceMenuButton"
        anchors.fill: parent
        text: qsTr("序列")
        quiet: true
        onClicked: sequenceMenu.open()
        ToolTip.visible: hovered
        ToolTip.text: qsTr("切换序列或管理当前序列")
    }

    AppPopover {
        id: sequenceMenu
        objectName: "sequenceMenuPopup"
        x: 0
        y: root.height + 4
        width: 248
        padding: 8
        modal: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        contentItem: ColumnLayout {
            spacing: 6
            Text {
                Layout.fillWidth: true
                Layout.leftMargin: 8
                Layout.rightMargin: 8
                text: qsTr("当前：%1").arg(root.activeSequenceName())
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                elide: Text.ElideRight
            }
            Repeater {
                model: workspaceController.sequencesModel
                AppButton {
                    required property string sequenceId
                    required property string name
                    required property string kind
                    Layout.fillWidth: true
                    checkable: true
                    checked: workspaceController.activeSequenceId === sequenceId
                    text: root.sequenceDisplayName(kind, name)
                    onClicked: {
                        workspaceController.selectSequence(sequenceId);
                        sequenceMenu.close();
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Theme.border
            }
            AppButton {
                objectName: "createShortSequenceButton"
                Layout.fillWidth: true
                text: qsTr("新建短视频序列")
                enabled: root.actionsEnabled
                onClicked: {
                    root.createShortRequested();
                    sequenceMenu.close();
                }
            }
            AppButton {
                objectName: "archiveActiveSequenceButton"
                Layout.fillWidth: true
                visible: workspaceController.canArchiveActiveSequence
                danger: true
                text: qsTr("移除当前短视频")
                onClicked: {
                    workspaceController.archiveActiveSequence();
                    sequenceMenu.close();
                }
            }
            AppButton {
                objectName: "editSequenceProfileButton"
                Layout.fillWidth: true
                text: workspaceController.profileConfirmed ? qsTr("序列设置 · %1 · %2").arg(workspaceController.profileLabel).arg(workspaceController.colorMode === "hdr10_bt2020_pq" ? "HDR10" : "SDR") : qsTr("序列设置")
                enabled: root.actionsEnabled
                onClicked: {
                    root.editProfileRequested();
                    sequenceMenu.close();
                }
            }
        }
    }
}
