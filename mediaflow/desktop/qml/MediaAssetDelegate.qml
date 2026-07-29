import QtQuick
import QtQuick.Controls
import "."
import "components"

Rectangle {
    id: root
    objectName: "mediaAssetDelegate"

    required property string assetId
    required property string name
    required property string kind
    required property string status
    required property string previewUrl
    required property var model
    required property string viewMode
    required property Item dragPreview

    signal contextRequested(string assetId)
    signal addRequested(string assetId)

    readonly property bool listMode: viewMode === "list"
    readonly property bool largeThumbnailMode: viewMode === "large_thumbnails"
    readonly property bool hasVisualPreview: status === "online"
        && (kind === "video" || kind === "image") && previewUrl.length > 0
    readonly property bool canEdit:
        workspaceController.actionCapabilities.canEdit
    property var draggedAssetIds: mediaController.isAssetSelected(assetId)
        ? mediaController.selectedAssetIds : [assetId]

    radius: listMode ? Theme.radiusSmall : Theme.radius
    color: mediaController.isAssetSelected(assetId)
        ? Theme.accentSoft : pointer.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
    border.color: mediaController.isAssetSelected(assetId)
        ? Theme.accent
        : pointer.containsMouse ? Theme.borderStrong : Theme.borderSubtle
    border.width: activeFocus ? 2 : 1
    activeFocusOnTab: true
    Accessible.name: name
    Accessible.description: detailLabel()
    Accessible.role: Accessible.ListItem
    Accessible.selected: mediaController.isAssetSelected(assetId)

    Keys.onSpacePressed: function (event) {
        mediaController.selectAsset(
            root.assetId, (event.modifiers & Qt.ControlModifier) !== 0);
        event.accepted = true;
    }
    Keys.onReturnPressed: function (event) {
        mediaController.selectAsset(root.assetId, false);
        if (root.canEdit && root.status === "online")
            root.addRequested(root.assetId);
        event.accepted = true;
    }
    Keys.onPressed: function (event) {
        if (event.key === Qt.Key_Menu) {
            root.contextRequested(root.assetId);
            event.accepted = true;
        }
    }

    function kindLabel() {
        if (kind === "video")
            return qsTr("视频");
        if (kind === "audio")
            return qsTr("音频");
        if (kind === "image")
            return qsTr("图片");
        if (kind === "web")
            return qsTr("网页");
        if (kind === "subtitle")
            return qsTr("字幕");
        return qsTr("素材");
    }

    function detailLabel() {
        if (status !== "online")
            return qsTr("素材离线");
        return kindLabel() + (model.width > 0 && model.height > 0
            ? " · " + model.width + "×" + model.height : "");
    }

    Rectangle {
        id: previewFrame
        x: root.listMode ? 4 : 6
        y: root.listMode ? Math.round((root.height - height) / 2) : 6
        width: root.listMode ? 36 : root.width - 12
        height: root.listMode ? 22 : root.largeThumbnailMode ? 86 : 52
        radius: root.listMode ? 4 : Theme.radiusSmall
        clip: true
        color: root.kind === "audio" ? Theme.audioSoft
            : root.kind === "image" ? Theme.imageSoft
            : root.kind === "subtitle" ? Theme.subtitleSoft
            : root.kind === "web" ? Theme.webSoft : Theme.videoSoft

        Image {
            id: previewImage
            objectName: "assetPreviewImage"
            anchors.fill: parent
            source: root.previewUrl
            sourceSize.width: 160
            sourceSize.height: 90
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            mipmap: true
            visible: root.hasVisualPreview
        }
        AppIcon {
            objectName: "assetKindIcon"
            anchors.centerIn: parent
            width: root.listMode ? 15 : root.largeThumbnailMode ? 30 : 23
            height: width
            iconName: root.kind === "audio" ? "audio"
                : root.kind === "image" ? "image"
                : root.kind === "subtitle" ? "subtitle"
                : root.kind === "web" ? "web" : "media"
            iconColor: Theme.text
            visible: !previewImage.visible || previewImage.status === Image.Error
        }
    }

    Text {
        id: nameText
        x: root.listMode ? 46 : 6
        y: root.listMode ? 0 : previewFrame.y + previewFrame.height + 3
        width: root.listMode
            ? Math.max(36, detailText.x - x - 8)
            : root.width - 12
        height: root.listMode ? root.height : 18
        text: root.name
        color: Theme.text
        font.pixelSize: Theme.fontSizeBodySmall
        font.weight: Font.Medium
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: root.listMode ? Text.AlignLeft : Text.AlignHCenter
    }

    Text {
        id: detailText
        x: root.listMode ? root.width - width - 6 : 6
        y: root.listMode ? 0 : nameText.y + nameText.height
        width: root.listMode ? Math.min(implicitWidth, Math.max(72, root.width * 0.36))
            : root.width - 12
        height: root.listMode ? root.height : 15
        visible: root.listMode || root.largeThumbnailMode
        text: root.detailLabel()
        color: root.status === "online" ? Theme.textMuted : Theme.danger
        font.pixelSize: Theme.fontSizeCaption
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: root.listMode ? Text.AlignRight : Text.AlignHCenter
    }

    MouseArea {
        id: pointer
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        hoverEnabled: true
        cursorShape: drag.active ? Qt.ClosedHandCursor
            : root.canEdit && root.status === "online"
            ? Qt.OpenHandCursor : Qt.ArrowCursor
        drag.target: root.canEdit && (pressedButtons & Qt.LeftButton)
            && root.status === "online"
            ? root.dragPreview : null
        drag.axis: Drag.XAndYAxis

        onPressed: function (mouse) {
            if (mouse.button !== Qt.LeftButton)
                return;
            const point = root.mapToItem(root.dragPreview.parent, mouse.x, mouse.y);
            root.dragPreview.x = point.x - root.dragPreview.width / 2;
            root.dragPreview.y = point.y - root.dragPreview.height / 2;
            root.dragPreview.assetName = root.name;
            root.dragPreview.draggedAssetIds = root.draggedAssetIds;
            root.dragPreview.dragActive = false;
        }
        onPositionChanged: function (mouse) {
            if (drag.active && root.status === "online") {
                const point = root.mapToItem(root.dragPreview.parent, mouse.x, mouse.y);
                root.dragPreview.x = point.x - root.dragPreview.width / 2;
                root.dragPreview.y = point.y - root.dragPreview.height / 2;
                root.dragPreview.dragActive = true;
            }
        }
        onClicked: function (mouse) {
            if (mouse.button === Qt.RightButton) {
                root.contextRequested(root.assetId);
                return;
            }
            mediaController.selectAsset(
                root.assetId, (mouse.modifiers & Qt.ControlModifier) !== 0);
        }
        onReleased: {
            if (root.dragPreview.dragActive)
                root.dragPreview.Drag.drop();
            root.dragPreview.dragActive = false;
        }
        onCanceled: root.dragPreview.dragActive = false
        onDoubleClicked: function (mouse) {
            if (root.canEdit && mouse.button === Qt.LeftButton
                    && root.status === "online")
                root.addRequested(root.assetId);
        }

        ToolTip.visible: containsMouse && !drag.active && root.status === "online"
        ToolTip.text: root.canEdit
            ? qsTr("拖到时间轴；双击则添加到播放头")
            : qsTr("只读项目中可查看素材，但不能加入时间线")
    }
}
