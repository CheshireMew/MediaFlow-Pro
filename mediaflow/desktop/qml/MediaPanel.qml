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
    readonly property string viewMode: String(
        settingsController.settingsData.assetViewMode || "list")
    readonly property var contextAssetData: mediaController.selectedAssetId === contextAssetId
        ? mediaController.selectedAssetData : ({})
    readonly property int filteredAssetCount: assetViewLoader.item
        ? assetViewLoader.item.count : 0
    readonly property bool canEdit:
        workspaceController.actionCapabilities.canEdit
    readonly property bool modalOpen: replaceDialog.opened

    function openImportDialog() {
        if (workspaceController.actionCapabilities.canImport)
            importDialog.open();
    }

    function addAssetAtPlayhead(assetId) {
        if (!root.canEdit)
            return;
        timelineController.dropAssets(
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
        if (!mediaController.isAssetSelected(assetId))
            mediaController.selectAsset(assetId);
        assetContextMenu.popup();
    }

    function refreshTask() {
        taskData = taskController.latestMediaTask(mediaController.selectedAssetId);
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
        settingsController.setAssetViewMode(nextMode);
    }

    Component.onCompleted: refreshTask()

    Connections {
        target: taskController
        function onTasksChanged() { root.refreshTask(); }
    }
    Connections {
        target: mediaController
        function onSelectionChanged() { root.refreshTask(); }
        function onProjectStateChanged() { root.refreshTask(); }
    }

    AppMenu {
        id: assetContextMenu
        objectName: "mediaAssetContextMenu"
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
            onTriggered: mediaController.openAssetFolder(root.contextAssetId)
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
            model: mediaController.filteredAssetsModel
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
            }
        }
    }

    Component {
        id: assetGridComponent
        GridView {
            objectName: "mediaAssetGridView"
            clip: true
            model: mediaController.filteredAssetsModel
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
            currentFolder: workspaceController.defaultImportDirectoryUrl
            nameFilters: [qsTr("素材文件 (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.flac *.png *.jpg *.jpeg *.srt *.vtt *.ass *.ssa editable-media.json)"), qsTr("所有文件 (*)")]
            onAccepted: if (workspaceController.actionCapabilities.canImport)
                mediaController.importFiles(selectedFiles)
        }
        FolderDialog {
            id: batchRelinkDialog
            title: qsTr("选择离线素材所在目录")
            onAccepted: if (root.canEdit)
                mediaController.relinkOfflineMedia(selectedFolder.toString())
        }
        FileDialog {
            id: selectedRelinkDialog
            title: qsTr("重新定位离线素材")
            fileMode: FileDialog.OpenFile
            onAccepted: if (root.canEdit)
                mediaController.relinkMedia(root.relinkAssetId, selectedFile.toString())
        }
        AppDialog {
            id: replaceDialog
            anchors.centerIn: parent
            implicitWidth: 400
            width: 400
            modal: true
            title: qsTr("替换为不同内容？")
            standardButtons: Dialog.Yes | Dialog.No
            onAccepted: mediaController.resolveRelinkReplacement(true)
            onRejected: mediaController.resolveRelinkReplacement(false)
            contentItem: Text {
                width: 360
                text: qsTr("所选文件的内容指纹与原素材不同：\n%1\n\n确认后会更新关联，并重新生成预览缓存和音频波形。").arg(workspaceController.pendingRelinkPath)
                color: Theme.text
                wrapMode: Text.WordWrap
            }
        }
        Connections {
            target: workspaceController
            function onRelinkConfirmationChanged() {
                if (workspaceController.relinkConfirmationPending)
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
                onTextChanged: mediaController.setAssetSearchText(text)
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
            enabled: workspaceController.actionCapabilities.canImport
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
                placeholderText: qsTr("粘贴视频或播放列表链接")
                text: String(
                    settingsController.settingsData.lastDownloadUrl
                    || "")
                enabled:
                    workspaceController.actionCapabilities.canStartTasks
                onAccepted: {
                    const value = text.trim();
                    if (value.length > 0
                            && !taskController.downloadAnalysisBusy)
                        taskController.analyzeDownloadUrl(value);
                }
            }
            AppButton {
                objectName: "workspaceAnalyzeDownloadButton"
                text: taskController.downloadAnalysisBusy
                    ? qsTr("分析中…") : qsTr("下载")
                enabled:
                    workspaceController.actionCapabilities.canStartTasks
                    && workspaceDownloadUrl.text.trim().length > 0
                    && !taskController.downloadAnalysisBusy
                onClicked: taskController.analyzeDownloadUrl(
                    workspaceDownloadUrl.text.trim())
            }
        }

        AppButton {
            visible: workspaceController.offlineAssetCount > 0
            enabled: root.canEdit
            Layout.fillWidth: true
            text: qsTr("批量重新定位 (%1)").arg(workspaceController.offlineAssetCount)
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

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Loader {
                id: assetViewLoader
                anchors.fill: parent
                sourceComponent: root.viewMode === "list"
                    ? assetListComponent : assetGridComponent
            }

            EmptyState {
                anchors.fill: parent
                visible: root.filteredAssetCount === 0
                iconName: "add"
                title: search.text.length === 0
                    ? qsTr("导入第一个素材") : qsTr("没有匹配的素材")
                description: search.text.length === 0
                    ? qsTr("支持视频、音频、图片、字幕和网页素材。下载的视频也会自动出现在这里。")
                    : qsTr("换个关键词，或清空搜索框查看全部素材。")
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
        enabled: workspaceController.actionCapabilities.canImport
        onDropped: function (drop) {
            if (!drop.hasUrls)
                return;
            mediaController.importFiles(drop.urls);
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
