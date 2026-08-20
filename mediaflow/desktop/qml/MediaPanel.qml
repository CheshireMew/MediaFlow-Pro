import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

Item {
    id: root
    objectName: "mediaPanel"
    required property Item dragPreview
    property int playheadFrame: 0
    property real pixelsPerFrame: 3.0
    property bool snapEnabled: true
    property string relinkAssetId: ""
    property string contextAssetId: ""
    property var taskData: ({})
    signal sourceRequested(string assetId, int frame)
    property string searchResultMode: "files"
    readonly property string viewMode: String(
        mediaflow.settingsController.settingsData.assetViewMode || "list")
    readonly property var contextAssetData: mediaflow.mediaController.selectedAssetId === contextAssetId
        ? mediaflow.mediaController.selectedAssetData : ({})
    readonly property int filteredAssetCount: assetViewLoader.item
        ? assetViewLoader.item.count : 0
    readonly property bool canEdit:
        mediaflow.workspaceViewController.actionCapabilities.canEdit
    readonly property bool modalOpen: replaceDialog.opened
        || newBinDialog.opened
    property string selectedBinId: ""
    readonly property var binOptions: {
        const options = [
            {label: qsTr("全部素材"), value: ""},
            {label: qsTr("未归档"), value: "__unfiled__"}
        ];
        for (let index = 0; index < mediaflow.mediaController.assetBinsModel.rowCount(); ++index) {
            const item = mediaflow.mediaController.assetBinsModel.get(index);
            options.push({
                label: String(item.displayName) + "  (" + item.assetCount + ")",
                value: String(item.binId)
            });
        }
        return options;
    }

    function openImportDialog() {
        if (mediaflow.workspaceViewController.actionCapabilities.canImport)
            importDialog.open();
    }

    function addAssetAtPlayhead(assetId) {
        if (!root.canEdit)
            return;
        mediaflow.timelineClipController.dropAssets(
            [assetId],
            "",
            -1,
            playheadFrame,
            pixelsPerFrame,
            playheadFrame,
            snapEnabled,
            false);
    }

    function openAssetContextMenu(assetId) {
        contextAssetId = assetId;
        if (!mediaflow.mediaController.isAssetSelected(assetId))
            mediaflow.mediaController.selectAsset(assetId);
        assetContextMenu.popup();
    }

    function refreshTask() {
        taskData = mediaflow.taskController.latestMediaTask(mediaflow.mediaController.selectedAssetId);
    }

    function viewModeLabel(mode) {
        if (mode === "thumbnails")
            return qsTr("缩略图");
        if (mode === "large_thumbnails")
            return qsTr("大缩略图");
        return qsTr("列表");
    }

    function cycleViewMode() {
        const nextMode = viewMode === "list" ? "thumbnails"
            : viewMode === "thumbnails" ? "large_thumbnails" : "list";
        mediaflow.settingsController.setAssetViewMode(nextMode);
    }

    Component.onCompleted: refreshTask()

    Connections {
        target: mediaflow.taskController
        function onTasksChanged() { root.refreshTask(); }
    }
    Connections {
        target: mediaflow.mediaController
        function onSelectionChanged() { root.refreshTask(); }
        function onProjectStateChanged() { root.refreshTask(); }
    }

    AppMenu {
        id: assetContextMenu
        objectName: "mediaAssetContextMenu"
        AppMenuItem {
            objectName: "assetOpenSourceMenuItem"
            text: qsTr("在源监视器中打开")
            enabled: root.contextAssetData.status === "online"
                && ["video", "audio", "image"].indexOf(
                    String(root.contextAssetData.kind)) >= 0
            onTriggered: root.sourceRequested(root.contextAssetId, 0)
        }
        AppMenuItem {
            objectName: "assetAddAtPlayheadMenuItem"
            text: qsTr("添加到播放头")
            enabled: root.canEdit
                && root.contextAssetData.status === "online"
            onTriggered: root.addAssetAtPlayhead(root.contextAssetId)
        }
        AppMenuItem {
            objectName: "assetOpenFolderMenuItem"
            text: qsTr("打开素材所在文件夹")
            onTriggered: mediaflow.mediaController.openAssetFolder(root.contextAssetId)
        }
        AppMenuItem {
            text: qsTr("重新定位")
            visible: root.contextAssetData.status === "offline"
            enabled: root.canEdit
            onTriggered: {
                root.relinkAssetId = root.contextAssetId;
                selectedRelinkDialog.open();
            }
        }
    }

    Component {
        id: assetListComponent
        ListView {
            objectName: "mediaAssetListView"
            clip: true
            spacing: 2
            model: mediaflow.mediaController.filteredAssetsModel
            ScrollBar.vertical: AppScrollBar {}

            delegate: MediaAssetDelegate {
                width: ListView.view ? ListView.view.width : 0
                height: 30
                viewMode: "list"
                dragPreview: root.dragPreview
                onContextRequested: function (assetId) {
                    root.openAssetContextMenu(assetId);
                }
                onAddRequested: function (assetId) {
                    root.addAssetAtPlayhead(assetId);
                }
                onOpenRequested: function (assetId) {
                    root.sourceRequested(assetId, 0);
                }
            }
        }
    }

    Component {
        id: assetGridComponent
        GridView {
            objectName: "mediaAssetGridView"
            clip: true
            model: mediaflow.mediaController.filteredAssetsModel
            cellWidth: root.viewMode === "large_thumbnails" ? 172 : 112
            cellHeight: root.viewMode === "large_thumbnails" ? 140 : 92
            ScrollBar.vertical: AppScrollBar {}

            delegate: MediaAssetDelegate {
                width: (GridView.view ? GridView.view.cellWidth : 112) - 8
                height: root.viewMode === "large_thumbnails" ? 132 : 84
                viewMode: root.viewMode
                dragPreview: root.dragPreview
                onContextRequested: function (assetId) {
                    root.openAssetContextMenu(assetId);
                }
                onAddRequested: function (assetId) {
                    root.addAssetAtPlayhead(assetId);
                }
                onOpenRequested: function (assetId) {
                    root.sourceRequested(assetId, 0);
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        FileDialog {
            id: importDialog
            objectName: "mediaImportDialog"
            title: qsTr("导入素材")
            fileMode: FileDialog.OpenFiles
            currentFolder: mediaflow.workspaceViewController.defaultImportDirectoryUrl
            nameFilters: [qsTr("素材文件 (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.flac *.png *.jpg *.jpeg *.srt *.vtt *.ass *.ssa editable-media.json)"), qsTr("所有文件 (*)")]
            onAccepted: if (mediaflow.workspaceViewController.actionCapabilities.canImport)
                mediaflow.mediaController.importFiles(selectedFiles)
        }
        FolderDialog {
            id: batchRelinkDialog
            title: qsTr("选择离线素材所在目录")
            onAccepted: if (root.canEdit)
                mediaflow.mediaController.relinkOfflineMedia(selectedFolder.toString())
        }
        FileDialog {
            id: selectedRelinkDialog
            title: qsTr("重新定位离线素材")
            fileMode: FileDialog.OpenFile
            onAccepted: if (root.canEdit)
                mediaflow.mediaController.relinkMedia(root.relinkAssetId, selectedFile.toString())
        }
        AppDialog {
            id: replaceDialog
            anchors.centerIn: parent
            implicitWidth: 400
            width: 400
            modal: true
            title: qsTr("替换为不同内容？")
            standardButtons: Dialog.Yes | Dialog.No
            onAccepted: mediaflow.mediaController.resolveRelinkReplacement(true)
            onRejected: mediaflow.mediaController.resolveRelinkReplacement(false)
            contentItem: Text {
                width: 360
                text: qsTr("所选文件的内容指纹与原素材不同：\n%1\n\n确认后会更新关联，并重新生成预览缓存和音频波形。").arg(mediaflow.workspaceViewController.pendingRelinkPath)
                color: Theme.text
                wrapMode: Text.WordWrap
            }
        }
        AppDialog {
            id: newBinDialog
            objectName: "newAssetBinDialog"
            anchors.centerIn: parent
            width: 380
            modal: true
            title: qsTr("新建素材文件夹")
            standardButtons: Dialog.Ok | Dialog.Cancel
            onOpened: {
                newBinName.clear();
                newBinName.forceActiveFocus();
            }
            onAccepted: mediaflow.mediaController.createAssetBin(
                newBinName.text,
                root.selectedBinId === "__unfiled__" ? "" : root.selectedBinId)
            contentItem: AppTextField {
                id: newBinName
                objectName: "newAssetBinName"
                placeholderText: qsTr("文件夹名称")
            }
        }
        Connections {
            target: mediaflow.workspaceViewController
            function onRelinkConfirmationChanged() {
                if (mediaflow.workspaceViewController.relinkConfirmationPending)
                    replaceDialog.open();
                else
                    replaceDialog.close();
            }
        }

        RowLayout {
            objectName: "mediaToolbar"
            Layout.fillWidth: true
            spacing: 6
            AppTextField {
                id: search
                objectName: "mediaSearchField"
                Layout.fillWidth: true
                implicitHeight: 36
                placeholderText: qsTr("搜索素材、转写内容或概念")
                color: Theme.text
                placeholderTextColor: Theme.textMuted
                leftPadding: 12
                onTextChanged: mediaflow.mediaController.setAssetSearchText(text)
                background: Rectangle {
                    radius: Theme.radiusSmall
                    color: Theme.field
                    border.color: search.activeFocus
                        ? Theme.accent
                        : search.hovered ? Theme.borderStrong : Theme.borderSubtle
                    border.width: search.activeFocus ? 2 : 1
                }
            }
            MediaViewModeButton {
                objectName: "mediaViewModeButton"
                iconKind: root.viewMode
                toolTipText: root.viewModeLabel(root.viewMode)
                    + " · " + qsTr("点击切换视图")
                onClicked: root.cycleViewMode()
            }
            AppButton {
            objectName: "openMediaImportButton"
            text: qsTr("导入")
            primary: true
            enabled: mediaflow.workspaceViewController.actionCapabilities.canImport
            onClicked: importDialog.open()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            AppTextField {
                id: workspaceDownloadUrl
                objectName: "workspaceDownloadUrlField"
                Layout.fillWidth: true
                placeholderText: qsTr("粘贴媒体或播放列表链接")
                text: String(
                    mediaflow.settingsController.settingsData.lastDownloadUrl
                    || "")
                enabled:
                    mediaflow.workspaceViewController.actionCapabilities.canStartTasks
                onAccepted: {
                    const value = text.trim();
                    if (value.length > 0
                            && !mediaflow.taskController.downloadAnalysisBusy)
                        mediaflow.taskController.analyzeDownloadUrl(value);
                }
            }
            AppButton {
                objectName: "workspaceAnalyzeDownloadButton"
                text: mediaflow.taskController.downloadAnalysisBusy
                    ? qsTr("分析中…") : qsTr("下载")
                enabled:
                    mediaflow.workspaceViewController.actionCapabilities.canStartTasks
                    && workspaceDownloadUrl.text.trim().length > 0
                    && !mediaflow.taskController.downloadAnalysisBusy
                onClicked: mediaflow.taskController.analyzeDownloadUrl(
                    workspaceDownloadUrl.text.trim())
            }
        }

        RowLayout {
            objectName: "assetBinToolbar"
            Layout.fillWidth: true
            spacing: 6
            AppComboBox {
                id: assetBinFilter
                objectName: "assetBinFilter"
                Layout.fillWidth: true
                model: root.binOptions
                textRole: "label"
                valueRole: "value"
                onActivated: {
                    root.selectedBinId = String(currentValue);
                    mediaflow.mediaController.setAssetBinFilter(root.selectedBinId);
                }
            }
            AppIconButton {
                objectName: "createAssetBinButton"
                iconName: "add"
                flat: true
                enabled: root.canEdit
                Accessible.name: qsTr("新建素材文件夹")
                toolTipText: Accessible.name
                onClicked: newBinDialog.open()
            }
            AppMenuButton {
                id: moveToBinButton
                objectName: "moveAssetsToBinButton"
                text: qsTr("移动到")
                compact: true
                quiet: true
                enabled: root.canEdit
                    && mediaflow.mediaController.selectedAssetIds.length > 0
                onClicked: moveToBinMenu.open()
                AppMenu {
                    id: moveToBinMenu
                    y: moveToBinButton.height + 4
                    AppMenuItem {
                        text: qsTr("未归档")
                        onTriggered: mediaflow.mediaController.moveSelectedAssetsToBin("")
                    }
                    Repeater {
                        model: mediaflow.mediaController.assetBinsModel
                        AppMenuItem {
                            required property string binId
                            required property string displayName
                            text: displayName
                            onTriggered: mediaflow.mediaController.moveSelectedAssetsToBin(binId)
                        }
                    }
                }
            }
        }

        AppButton {
            visible: mediaflow.workspaceViewController.offlineAssetCount > 0
            enabled: root.canEdit
            Layout.fillWidth: true
            text: qsTr("批量重新定位 (%1)").arg(mediaflow.workspaceViewController.offlineAssetCount)
            onClicked: batchRelinkDialog.open()
        }

        Text {
            objectName: "mediaDragHint"
            Layout.fillWidth: true
            text: qsTr("将素材拖到下方时间轴；同一素材可以重复拖入")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }

        RowLayout {
            objectName: "assetSearchResultTabs"
            Layout.fillWidth: true
            visible: mediaflow.mediaController.assetSearchText.length > 0
            spacing: 4
            AppButton {
                objectName: "assetSearchFilesTab"
                text: qsTr("文件 %1").arg(root.filteredAssetCount)
                compact: true
                checkable: true
                checked: root.searchResultMode === "files"
                quiet: !checked
                onClicked: root.searchResultMode = "files"
            }
            AppButton {
                objectName: "assetSearchMomentsTab"
                text: qsTr("内容时刻 %1").arg(
                    mediaflow.mediaController.filteredAssetMomentsModel.rowCount())
                compact: true
                checkable: true
                checked: root.searchResultMode === "moments"
                quiet: !checked
                onClicked: root.searchResultMode = "moments"
            }
            Item { Layout.fillWidth: true }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Loader {
                id: assetViewLoader
                anchors.fill: parent
                visible: mediaflow.mediaController.assetSearchText.length === 0
                    || root.searchResultMode === "files"
                sourceComponent: root.viewMode === "list"
                    ? assetListComponent : assetGridComponent
            }

            ListView {
                id: assetMomentList
                objectName: "assetMomentList"
                anchors.fill: parent
                visible: mediaflow.mediaController.assetSearchText.length > 0
                    && root.searchResultMode === "moments"
                clip: true
                spacing: 5
                model: mediaflow.mediaController.filteredAssetMomentsModel
                ScrollBar.vertical: AppScrollBar {}
                delegate: Rectangle {
                    required property string assetId
                    required property string assetName
                    required property string momentType
                    required property string label
                    required property string detail
                    required property int startFrame
                    required property int endFrame
                    required property string previewUrl
                    width: assetMomentList.width
                    height: 64
                    radius: Theme.radiusSmall
                    color: momentMouse.containsMouse
                        ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: momentMouse.containsMouse
                        ? Theme.accent : Theme.borderSubtle
                    Image {
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        anchors.margins: 5
                        width: 92
                        source: previewUrl
                        fillMode: Image.PreserveAspectCrop
                        visible: previewUrl.length > 0
                    }
                    Column {
                        anchors.left: parent.left
                        anchors.leftMargin: previewUrl.length > 0 ? 104 : 10
                        anchors.right: parent.right
                        anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3
                        Text {
                            width: parent.width
                            text: (momentType === "spoken" ? qsTr("口述") : qsTr("画面"))
                                + " · " + label
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodySmall
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            width: parent.width
                            text: assetName + " · " + startFrame + "–" + endFrame
                                + (detail.length > 0 ? " · " + detail : "")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                            elide: Text.ElideRight
                        }
                    }
                    MouseArea {
                        id: momentMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onDoubleClicked: root.sourceRequested(assetId, startFrame)
                    }
                }
            }

            EmptyState {
                anchors.fill: parent
                visible: (mediaflow.mediaController.assetSearchText.length === 0
                        || root.searchResultMode === "files")
                    && root.filteredAssetCount === 0
                iconName: "add"
                iconVisible: height >= 150
                title: search.text.length === 0
                    ? qsTr("导入第一个素材") : qsTr("没有匹配的素材")
                description: search.text.length === 0
                    ? qsTr("支持视频、音频、图片、字幕和网页素材。下载的视频也会自动出现在这里。")
                    : qsTr("换个关键词，或清空搜索框查看全部素材。")
            }
            EmptyState {
                anchors.fill: parent
                visible: mediaflow.mediaController.assetSearchText.length > 0
                    && root.searchResultMode === "moments"
                    && mediaflow.mediaController.filteredAssetMomentsModel.rowCount() === 0
                iconName: "search"
                iconVisible: height >= 150
                title: qsTr("没有匹配的内容时刻")
                description: qsTr("内容时刻来自真实转写片段和画面高光分析。")
            }
        }

        ContextTaskCard {
            objectName: "mediaTaskPanel"
            Layout.fillWidth: true
            taskData: root.taskData
            fallbackTitle: qsTr("媒体处理任务")
            showArtifact: false
        }

    }

    DropArea {
        id: fileDropArea
        objectName: "mediaFileDropArea"
        anchors.fill: parent
        z: 200
        enabled: mediaflow.workspaceViewController.actionCapabilities.canImport
        onDropped: function (drop) {
            if (!drop.hasUrls)
                return;
            mediaflow.mediaController.importFiles(drop.urls);
            drop.acceptProposedAction();
        }
    }

    Rectangle {
        anchors.fill: parent
        visible: fileDropArea.containsDrag
        z: 201
        color: Theme.dragOverlay
        border.width: 2
        border.color: Theme.accent
        radius: Theme.radiusSmall
        Text {
            anchors.centerIn: parent
            text: qsTr("释放以导入素材")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodyLarge
            font.weight: Font.DemiBold
        }
    }
}
