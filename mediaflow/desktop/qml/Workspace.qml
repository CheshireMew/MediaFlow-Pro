import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"
Rectangle {
    id: root
    objectName: "workspace"
    color: Theme.window
    property string activeMode: workspaceController.workspaceModes.length > 0
        ? String(workspaceController.workspaceModes[0].key) : ""
    property var exportPreviewOptions: ({})
    readonly property int workspaceNavigationHeight: 68
    readonly property int workspaceGutter: 10
    readonly property Item focusedItem: root.Window.window
        ? root.Window.window.activeFocusItem : null
    readonly property bool textInputActive: focusedItem instanceof TextInput
        || focusedItem instanceof TextEdit
    readonly property bool modalOpen: settingsDialog.opened
        || profileDialog.opened
        || sequenceProfileDialog.opened
        || Boolean(mediaPanelLoader.item && mediaPanelLoader.item.modalOpen)
        || Boolean(taskCenterPanelLoader.item
            && taskCenterPanelLoader.item.modalOpen)
        || Boolean(root.Window.window
            && (root.Window.window.downloadPlanVisible
                || root.Window.window.projectVersionsVisible))
    readonly property bool shortcutsEnabled: !root.textInputActive
        && !Boolean(webEditorLoader.item
            && webEditorLoader.item.webInputActive)
        && !root.modalOpen
    readonly property bool canEdit:
        workspaceController.actionCapabilities.canEdit
    property real toolPanelWidth: Math.max(
        root.width >= 1600 ? 520 : 420,
        Math.min(
            root.width >= 1600 ? 680 : 460,
            settingsController.settingsData.leftPanelWidth || 520))
    property real inspectorPanelWidth: root.width >= 1600 ? 400 : 330
    property real timelinePanelHeight: Math.max(210, settingsController.settingsData.timelineHeight || 330)
    function panelIndexForMode(modeKey) {
        const modes = workspaceController.workspaceModes;
        let panelObjectName = "";
        for (let modeIndex = 0; modeIndex < modes.length; ++modeIndex) {
            if (String(modes[modeIndex].key) === String(modeKey)) {
                panelObjectName = String(modes[modeIndex].panelObjectName);
                break;
            }
        }
        if (!panelObjectName)
            return 0;
        const panels = toolStack.children;
        for (let panelIndex = 0; panelIndex < panels.length; ++panelIndex) {
            if (String(panels[panelIndex].panelObjectName) === panelObjectName)
                return panelIndex;
        }
        return 0;
    }
    function toggleFullscreen() {
        previewViewport.toggleFullscreen();
    }
    function playPreview() {
        previewViewport.playPreviewFrom(timeline.visiblePlayheadFrame);
    }

    function playReversePreview() {
        previewViewport.playReversePreviewFrom(timeline.visiblePlayheadFrame);
    }

    function stopPreview() {
        previewViewport.stopPreview();
    }

    function beginPreviewScrub() {
        previewViewport.beginScrub();
    }

    function endPreviewScrub() {
        previewViewport.endScrub();
    }

    function resetPreviewViewport() {
        previewViewport.resetViewport();
    }

    function persistPanelLayout() {
        settingsController.savePanelLayout(Math.round(toolPanelWidth), Math.round(timelinePanelHeight));
    }

    function openMediaImportDialog() {
        root.activeMode = "media";
        Qt.callLater(function () {
            if (mediaPanelLoader.item)
                mediaPanelLoader.item.openImportDialog();
        });
    }

    function openExportPanel() {
        root.activeMode = "export";
    }

    function activeSequenceName() {
        for (let index = 0;
                index < workspaceController.sequencesModel.rowCount();
                ++index) {
            const sequence = workspaceController.sequencesModel.get(index);
            if (String(sequence.sequenceId)
                    === String(workspaceController.activeSequenceId))
                return String(sequence.name);
        }
        return qsTr("时间线");
    }

    Connections {
        target: workspaceController
        function onPreviewRangeRequested(startFrame, endFrame) {
            previewViewport.playRequestedRange(startFrame, endFrame);
        }
    }

    Connections {
        target: taskController
        function onTaskCenterRequested() {
            root.activeMode = "tasks";
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: root.workspaceGutter
            Layout.rightMargin: root.workspaceGutter
            Layout.topMargin: root.workspaceGutter
            Layout.bottomMargin: root.workspaceGutter
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    id: toolPanelContainer
                    objectName: "toolPanelContainer"
                    Layout.preferredWidth: root.toolPanelWidth
                    Layout.minimumWidth: 420
                    Layout.fillHeight: true
                    color: Theme.surface
                    radius: Theme.radius
                    border.width: 1
                    border.color: Theme.borderSubtle
                    clip: true

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 0

                        WorkspaceNavigation {
                            Layout.fillWidth: true
                            Layout.preferredHeight: root.workspaceNavigationHeight
                            activeMode: root.activeMode
                            onModeRequested: function (mode) {
                                root.activeMode = mode;
                            }
                            onSettingsRequested: settingsDialog.open()
                        }

                        StackLayout {
                            id: toolStack
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.leftMargin: 14
                            Layout.rightMargin: 14
                            Layout.topMargin: 12
                            Layout.bottomMargin: 12
                            currentIndex: root.panelIndexForMode(root.activeMode)

                            Loader {
                                id: mediaPanelLoader
                                property string panelObjectName: "mediaPanel"
                                active: root.activeMode === "media"
                                    || status === Loader.Ready
                                sourceComponent: MediaPanel {
                                    dragPreview: mediaDragPreview
                                    playheadFrame: timeline.visiblePlayheadFrame
                                    pixelsPerFrame: timeline.pixelsPerFrame
                                    snapEnabled: timeline.snapEnabled
                                }
                            }
                            Loader {
                                property string panelObjectName: "transcriptWorkspace"
                                active: root.activeMode === "transcript"
                                    || status === Loader.Ready
                                sourceComponent: TranscriptWorkspace {
                                    playheadFrame: previewViewport.position
                                    playbackActive: previewViewport.playing
                                    onImportRequested: root.openMediaImportDialog()
                                    onSeekRequested: function (frame) {
                                        previewViewport.seek(frame);
                                    }
                                }
                            }
                            Loader {
                                property string panelObjectName: "highlightPanel"
                                active: root.activeMode === "highlight"
                                    || status === Loader.Ready
                                sourceComponent: HighlightPanel {
                                    playheadFrame: previewViewport.position
                                }
                            }
                            Loader {
                                property string panelObjectName: "audioScroll"
                                active: root.activeMode === "audio"
                                    || status === Loader.Ready
                                sourceComponent: AudioPanel {}
                            }
                            Loader {
                                property string panelObjectName: "exportPanel"
                                active: root.activeMode === "export"
                                    || status === Loader.Ready
                                sourceComponent: ExportPanel {
                                    onPreviewConfigurationChanged: function (options) {
                                        root.exportPreviewOptions = options;
                                    }
                                }
                            }
                            Loader {
                                id: taskCenterPanelLoader
                                property string panelObjectName: "taskCenterPanel"
                                active: root.activeMode === "tasks"
                                    || status === Loader.Ready
                                sourceComponent: TaskCenterPanel {}
                            }
                        }
                    }
                }

                Rectangle {
                    id: leftResizeHandle
                    Layout.preferredWidth: root.workspaceGutter
                    Layout.fillHeight: true
                    color: Theme.window
                    property real startWidth: 0

                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: leftDrag.active ? 2 : 1
                        color: leftDrag.active ? Theme.accent
                            : leftResizeHover.hovered ? Theme.borderStrong
                            : Theme.transparent
                    }

                    HoverHandler { id: leftResizeHover }

                    DragHandler {
                        id: leftDrag
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        onActiveChanged: {
                            if (active)
                                leftResizeHandle.startWidth = root.toolPanelWidth;
                            else
                                root.persistPanelLayout();
                        }
                        onTranslationChanged: root.toolPanelWidth = Math.max(
                            root.width >= 1600 ? 460 : 420,
                            Math.min(root.width >= 1600 ? 680 : 460,
                                leftResizeHandle.startWidth + translation.x))
                    }
                }

                Rectangle {
                    id: previewPanel
                    objectName: "previewPanel"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: 360
                    color: Theme.surface
                    radius: Theme.radius
                    border.width: 1
                    border.color: Theme.borderSubtle
                    clip: true

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 0

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 50
                            color: Theme.surface

                            Text {
                                anchors.left: parent.left
                                anchors.leftMargin: 18
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("播放器") + " · " + root.activeSequenceName()
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeTitleSmall
                                font.weight: Font.Medium
                            }

                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                height: 1
                                color: Theme.divider
                            }
                        }

                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: 260

                            PreviewViewport {
                                id: previewViewport
                                anchors.fill: parent
                                visible: !(webController.isWebClip
                                    && webController.editMode)
                                source: workspaceController.previewGraphPath
                                runtimeRoot: workspaceController.mltRuntimeRoot
                                hdrEnabled: workspaceController.colorMode
                                    === "hdr10_bt2020_pq"
                                profileWidth: workspaceController.profileWidth
                                profileHeight: workspaceController.profileHeight
                                exportPreviewActive: root.activeMode === "export"
                                exportPreviewOptions: root.exportPreviewOptions
                                subtitleText: root.activeMode === "export"
                                    ? subtitleController.subtitleTextForTrackAtFrame(
                                        String(root.exportPreviewOptions
                                            .burnSubtitleTrackId || ""),
                                        position)
                                    : subtitleController.subtitleTextAtFrame(position)
                                watermarkSource: root.activeMode === "export"
                                    && root.exportPreviewOptions.watermark
                                    && root.exportPreviewOptions.watermark.enabled
                                    ? mediaController.assetUrl(String(
                                        root.exportPreviewOptions.watermark
                                            .asset_id || ""))
                                    : ""
                                onDroppedFramesReported: function (count) {
                                    workspaceController
                                        .reportPreviewDroppedFrames(count);
                                }
                                onHdrActiveReported: function (active) {
                                    workspaceController
                                        .reportHdrPreviewActive(active);
                                }
                            }

                            Loader {
                                id: webEditorLoader
                                objectName: "webEditorLoader"
                                anchors.fill: parent
                                visible: webController.isWebClip
                                    && webController.editMode
                                active: visible || status === Loader.Ready
                                sourceComponent: WebEditorCanvas {
                                    playheadFrame: previewViewport.position
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: root.workspaceGutter
                    Layout.fillHeight: true
                    color: Theme.window
                }

                InspectorPanel {
                    objectName: "inspectorPanel"
                    Layout.preferredWidth: root.inspectorPanelWidth
                    Layout.minimumWidth: 330
                    Layout.fillHeight: true
                    playheadFrame: previewViewport.position
                    onEditProfileRequested: sequenceProfileDialog.open()
                    onSeekRequested: function(frame) {
                        previewViewport.seek(frame);
                    }
                }
            }

                Rectangle {
                    id: timelineResizeHandle
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.workspaceGutter
                    color: Theme.window
                    property real startHeight: 0

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        height: timelineDrag.active ? 2 : 1
                        color: timelineDrag.active ? Theme.accent
                            : timelineResizeHover.hovered ? Theme.borderStrong
                            : Theme.transparent
                    }

                    HoverHandler { id: timelineResizeHover }

                    DragHandler {
                        id: timelineDrag
                        target: null
                        xAxis.enabled: false
                        yAxis.enabled: true
                        onActiveChanged: {
                            if (active)
                                timelineResizeHandle.startHeight = root.timelinePanelHeight;
                            else
                                root.persistPanelLayout();
                        }
                        onTranslationChanged: root.timelinePanelHeight = Math.max(
                            210, Math.min(640,
                                timelineResizeHandle.startHeight - translation.y))
                    }
                }

            TimelineView {
                id: timeline
                objectName: "timelinePanel"
                Layout.fillWidth: true
                Layout.preferredHeight: root.timelinePanelHeight
                Layout.minimumHeight: 210
                playheadFrame: previewViewport.position
                shortcutsEnabled: root.shortcutsEnabled
                onPlayheadScrubbingChanged: {
                    if (timeline.playheadScrubbing)
                        root.beginPreviewScrub();
                    else
                        root.endPreviewScrub();
                }
                onSeekRequested: function (frame) {
                    previewViewport.seek(frame);
                }
                onEditProfileRequested: sequenceProfileDialog.open()
            }
        }
    }

    WorkspaceStatusOverlays {
        anchors.fill: parent
        toolPanelWidth: toolPanelContainer.width
        previewPanelWidth: previewPanel.width
        gutter: root.workspaceGutter
        z: 300
        onOpenSettingsRequested: settingsDialog.open()
        onOpenExportRequested: root.activeMode = "export"
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
        onAccepted: workspaceController.resolveProfileAdoption(true)
        onRejected: workspaceController.resolveProfileAdoption(false)
        contentItem: Text {
            width: 430
            color: Theme.text
            wrapMode: Text.WordWrap
            text: qsTr("主时间线中已经有图片或音频编辑。这个视频建议使用 %1。采用后会按实际时长重新换算现有编辑；选择“否”则保持当前项目配置。").arg(workspaceController.pendingProfileLabel)
        }
    }

    SequenceProfileDialog {
        id: sequenceProfileDialog
    }

    Connections {
        target: workspaceController
        function onProfileConfirmationChanged() {
            if (workspaceController.profileConfirmationPending)
                profileDialog.open();
            else
                profileDialog.close();
        }
    }

    WorkspaceShortcuts {
        host: root
        preview: previewViewport
        timelineView: timeline
    }
}
