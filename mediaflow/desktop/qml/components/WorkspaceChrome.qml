import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: root

    required property Item host
    required property Item preview
    required property Item timelineView
    property real toolPanelWidth: 0
    property real previewPanelWidth: 0
    property real gutter: 0
    readonly property alias dragPreview: mediaDragPreview
    readonly property bool modalOpen: settingsDialog.opened
        || profileDialog.opened
        || sequenceProfileDialog.opened

    signal openExportRequested()

    function openSettings() {
        settingsDialog.open();
    }

    function openSequenceProfile() {
        sequenceProfileDialog.open();
    }

    WorkspaceStatusOverlays {
        anchors.fill: parent
        toolPanelWidth: root.toolPanelWidth
        previewPanelWidth: root.previewPanelWidth
        gutter: root.gutter
        z: 300
        onOpenSettingsRequested: root.openSettings()
        onOpenExportRequested: root.openExportRequested()
    }

    Rectangle {
        id: mediaDragPreview
        objectName: "mediaDragPreview"
        property bool dragActive: false
        property var draggedAssetIds: []
        property string assetName: ""
        width: Math.min(320, root.toolPanelWidth - 28)
        height: 64
        radius: Theme.radiusSmall
        color: Theme.accentSoft
        border.width: 2
        border.color: Theme.accent
        opacity: Drag.active ? 0.92 : 0
        visible: Drag.active
        z: 500
        Drag.active: dragActive
        Drag.source: mediaDragPreview
        Drag.keys: ["mediaflowAsset"]
        Drag.hotSpot.x: width / 2
        Drag.hotSpot.y: height / 2

        Text {
            anchors.fill: parent
            anchors.margins: 10
            text: mediaDragPreview.draggedAssetIds.length > 1
                ? qsTr("%1 个素材").arg(mediaDragPreview.draggedAssetIds.length)
                : mediaDragPreview.assetName
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.Medium
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }
    }

    SettingsDialog {
        id: settingsDialog
        anchors.centerIn: parent
    }

    AppDialog {
        id: profileDialog
        anchors.centerIn: parent
        implicitWidth: 460
        width: 460
        modal: true
        title: qsTr("采用视频项目配置？")
        standardButtons: Dialog.Yes | Dialog.No
        closePolicy: Popup.NoAutoClose
        onAccepted: mediaflow.workspaceSequenceController.resolveProfileAdoption(true)
        onRejected: mediaflow.workspaceSequenceController.resolveProfileAdoption(false)
        contentItem: Text {
            width: 430
            color: Theme.text
            wrapMode: Text.WordWrap
            text: qsTr("主时间线中已经有图片或音频编辑。这个视频建议使用 %1。采用后会按实际时长重新换算现有编辑；选择“否”则保持当前项目配置。").arg(mediaflow.workspaceViewController.pendingProfileLabel)
        }
    }

    SequenceProfileDialog {
        id: sequenceProfileDialog
    }

    Connections {
        target: mediaflow.workspaceViewController
        function onProfileConfirmationChanged() {
            if (mediaflow.workspaceViewController.profileConfirmationPending)
                profileDialog.open();
            else
                profileDialog.close();
        }
    }

    WorkspaceShortcuts {
        host: root.host
        preview: root.preview
        timelineView: root.timelineView
    }
}
