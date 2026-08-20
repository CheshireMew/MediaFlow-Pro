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

    implicitWidth: Math.min(520, Math.max(150, sequenceTabs.contentWidth))
        + createShortButton.implicitWidth + sequenceMenuButton.implicitWidth + 8
    implicitHeight: Theme.controlHeightCompact

    function sequenceDisplayName(kind, name, displayName) {
        if (displayName !== name)
            return displayName;
        return (kind === "short" ? root.shortSequencePrefix : root.mainSequencePrefix)
            + " · " + displayName;
    }

    function activeSequenceName() {
        for (let index = 0; index < mediaflow.workspaceViewController.sequencesModel.rowCount(); index++) {
            const sequence = mediaflow.workspaceViewController.sequencesModel.get(index);
            if (sequence.sequenceId === mediaflow.workspaceViewController.activeSequenceId)
                return root.sequenceDisplayName(
                    sequence.kind, sequence.name, sequence.displayName);
        }
        return qsTr("序列");
    }

    RowLayout {
        anchors.fill: parent
        spacing: 4

        ListView {
            id: sequenceTabs
            objectName: "sequenceTabs"
            Layout.preferredWidth: Math.min(520, Math.max(150, contentWidth))
            Layout.fillHeight: true
            orientation: ListView.Horizontal
            spacing: 3
            clip: true
            model: mediaflow.workspaceViewController.sequencesModel
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.horizontal: AppScrollBar {
                policy: sequenceTabs.contentWidth > sequenceTabs.width
                    ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
            }

            delegate: AppButton {
                required property string sequenceId
                required property string name
                required property string displayName
                required property string kind
                objectName: "sequenceTab_" + sequenceId
                height: sequenceTabs.height
                width: Math.min(180, Math.max(86, implicitWidth))
                text: root.sequenceDisplayName(kind, name, displayName)
                checkable: true
                checked: mediaflow.workspaceViewController.activeSequenceId === sequenceId
                quiet: !checked
                onClicked: mediaflow.workspaceSequenceController.selectSequence(sequenceId)
                ToolTip.visible: hovered && implicitWidth > width
                ToolTip.text: text
            }
        }

        AppIconButton {
            id: createShortButton
            objectName: "createShortSequenceButton"
            iconName: "add"
            compact: true
            flat: true
            enabled: root.actionsEnabled
            Accessible.name: qsTr("新建短视频序列")
            toolTipText: Accessible.name
            onClicked: root.createShortRequested()
        }

        AppIconButton {
            id: sequenceMenuButton
            objectName: "sequenceMenuButton"
            iconName: "more"
            compact: true
            flat: true
            Accessible.name: qsTr("当前序列设置")
            toolTipText: Accessible.name
            onClicked: sequenceMenu.open()
        }
    }

    AppPopover {
        id: sequenceMenu
        objectName: "sequenceMenuPopup"
        x: Math.max(0, root.width - width)
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
            AppButton {
                objectName: "archiveActiveSequenceButton"
                Layout.fillWidth: true
                visible: mediaflow.workspaceViewController.canArchiveActiveSequence
                danger: true
                text: qsTr("移除当前短视频")
                onClicked: {
                    mediaflow.workspaceSequenceController.archiveActiveSequence();
                    sequenceMenu.close();
                }
            }
            AppButton {
                objectName: "editSequenceProfileButton"
                Layout.fillWidth: true
                text: mediaflow.workspaceViewController.profileConfirmed ? qsTr("序列设置 · %1 · %2").arg(mediaflow.workspaceViewController.profileLabel).arg(mediaflow.workspaceViewController.colorMode === "hdr10_bt2020_pq" ? "HDR10" : "SDR") : qsTr("序列设置")
                enabled: root.actionsEnabled
                onClicked: {
                    root.editProfileRequested();
                    sequenceMenu.close();
                }
            }
        }
    }
}
