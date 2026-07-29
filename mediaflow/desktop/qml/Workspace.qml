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
    readonly property int workspaceNavigationHeight: 54
    readonly property int workspaceBannerHeight:
        taskController.downloadProgressVisible || workflowBanner.visible ? 64 : 0
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
    property real toolPanelWidth: Math.max(340, settingsController.settingsData.leftPanelWidth || 360)
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

        WorkspaceNavigation {
            Layout.fillWidth: true
            Layout.preferredHeight: root.workspaceNavigationHeight
            activeMode: root.activeMode
            onModeRequested: function (mode) {
                root.activeMode = mode;
            }
            onSettingsRequested: settingsDialog.open()
        }

        DownloadProgressBanner {
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 64 : 0
        }

        WorkflowBanner {
            id: workflowBanner
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 64 : 0
            onOpenSettingsRequested: settingsDialog.open()
            onOpenExportRequested: root.activeMode = "export"
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    id: toolPanelContainer
                    objectName: "toolPanelContainer"
                    Layout.preferredWidth: root.toolPanelWidth
                    Layout.fillHeight: true
                    color: Theme.surface
                    border.width: 0

                    StackLayout {
                        id: toolStack
                        anchors.fill: parent
                        anchors.leftMargin: 16
                        anchors.rightMargin: 16
                        anchors.topMargin: 14
                        anchors.bottomMargin: 14
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
                            property string panelObjectName: "editPanel"
                            active: root.activeMode === "edit"
                                || status === Loader.Ready
                            sourceComponent: EditPanel {
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

                Rectangle {
                    id: leftResizeHandle
                    Layout.preferredWidth: 8
                    Layout.fillHeight: true
                    color: Theme.window
                    property real startWidth: 0

                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: leftDrag.active ? 2 : 1
                        color: leftDrag.active ? Theme.accent : Theme.divider
                    }

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
                        onTranslationChanged: root.toolPanelWidth = Math.max(340, Math.min(640, leftResizeHandle.startWidth + translation.x))
                    }
                }

                PreviewViewport {
                    id: previewViewport
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 260
                    visible: !(root.activeMode === "edit" && webController.isWebClip && webController.editMode)
                    source: workspaceController.previewGraphPath
                    runtimeRoot: workspaceController.mltRuntimeRoot
                    hdrEnabled: workspaceController.colorMode === "hdr10_bt2020_pq"
                    profileWidth: workspaceController.profileWidth
                    profileHeight: workspaceController.profileHeight
                    exportPreviewActive: root.activeMode === "export"
                    exportPreviewOptions: root.exportPreviewOptions
                    subtitleText: root.activeMode === "export"
                        ? subtitleController.subtitleTextForTrackAtFrame(
                            String(root.exportPreviewOptions.burnSubtitleTrackId || ""),
                            position)
                        : subtitleController.subtitleTextAtFrame(position)
                    watermarkSource: root.activeMode === "export"
                        && root.exportPreviewOptions.watermark
                        && root.exportPreviewOptions.watermark.enabled
                        ? mediaController.assetUrl(
                            String(root.exportPreviewOptions.watermark.asset_id || ""))
                        : ""
                    onDroppedFramesReported: function (count) {
                        workspaceController.reportPreviewDroppedFrames(count);
                    }
                    onHdrActiveReported: function (active) {
                        workspaceController.reportHdrPreviewActive(active);
                    }
                }

                Loader {
                    id: webEditorLoader
                    objectName: "webEditorLoader"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 260
                    visible: root.activeMode === "edit" && webController.isWebClip && webController.editMode
                    active: visible || status === Loader.Ready
                    sourceComponent: WebEditorCanvas {
                        playheadFrame: previewViewport.position
                    }
                }
            }

                Rectangle {
                    id: timelineResizeHandle
                    Layout.fillWidth: true
                    Layout.preferredHeight: 8
                    color: Theme.window
                property real startHeight: 0

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    height: timelineDrag.active ? 2 : 1
                    color: timelineDrag.active ? Theme.accent : Theme.divider
                }

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
                    onTranslationChanged: root.timelinePanelHeight = Math.max(210, Math.min(640, timelineResizeHandle.startHeight - translation.y))
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

    Shortcut {
        sequence: "Ctrl+I"
        enabled: root.shortcutsEnabled && root.canEdit
        onActivated: root.openMediaImportDialog()
    }
    Shortcut {
        sequence: "Ctrl+M"
        enabled: root.shortcutsEnabled
        onActivated: root.activeMode = "export"
    }
    Shortcut {
        sequence: "Space"
        enabled: root.shortcutsEnabled
        autoRepeat: false
        onActivated: previewViewport.playing || previewViewport.playbackRequested
            ? previewViewport.pause() : root.playPreview()
    }
    Shortcut {
        sequence: "F11"
        enabled: root.shortcutsEnabled
        onActivated: root.toggleFullscreen()
    }
    Shortcut {
        sequence: "J"
        enabled: root.shortcutsEnabled
        onActivated: {
            previewViewport.playbackRate = previewViewport.playing && previewViewport.playbackRate < 0
                ? Math.max(-4, previewViewport.playbackRate * 2) : -1.0;
            root.playReversePreview();
        }
    }
    Shortcut {
        sequence: "K"
        enabled: root.shortcutsEnabled
        onActivated: previewViewport.pause()
    }
    Shortcut {
        sequence: "L"
        enabled: root.shortcutsEnabled
        onActivated: {
            previewViewport.playbackRate = previewViewport.playing && previewViewport.playbackRate > 0
                ? Math.min(4, previewViewport.playbackRate * 2) : 1.0;
            root.playPreview();
        }
    }
    Shortcut {
        sequence: "S"
        enabled: root.shortcutsEnabled
        onActivated: timeline.snapEnabled = !timeline.snapEnabled
    }
    Shortcut {
        sequence: "Ctrl+K"
        enabled: root.shortcutsEnabled && root.canEdit
            && timelineController.selectedClipId.length > 0
        onActivated: timelineController.splitClip(
            timelineController.selectedClipId, previewViewport.position)
    }
    Shortcut {
        sequence: "Ctrl+B"
        enabled: root.shortcutsEnabled && root.canEdit
            && timelineController.selectedClipId.length > 0
        onActivated: timelineController.splitClip(
            timelineController.selectedClipId, previewViewport.position)
    }
    Shortcut {
        sequence: "Delete"
        enabled: root.shortcutsEnabled && root.canEdit
            && timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(false)
    }
    Shortcut {
        sequence: "Shift+Delete"
        enabled: root.shortcutsEnabled && root.canEdit
            && timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(true)
    }
    Shortcut {
        sequence: "Ctrl+Z"
        enabled: root.shortcutsEnabled && root.canEdit
            && timelineController.canUndo
        onActivated: timelineController.undo()
    }
    Shortcut {
        sequences: ["Ctrl+Y", "Ctrl+Shift+Z"]
        enabled: root.shortcutsEnabled && root.canEdit
            && timelineController.canRedo
        onActivated: timelineController.redo()
    }
    Shortcut {
        sequence: "Ctrl+D"
        enabled: root.shortcutsEnabled && root.canEdit
            && timelineController.selectedClipId.length > 0
        onActivated: timelineController.duplicateClip(
            timelineController.selectedClipId,
            timeline.pixelsPerFrame,
            previewViewport.position)
    }
    Shortcut {
        sequence: "Ctrl+A"
        enabled: root.shortcutsEnabled
            && timelineController.clipsModel.rowCount() > 0
        onActivated: timelineController.selectAllClips()
    }
    Shortcut {
        sequence: "Ctrl+Shift+A"
        enabled: root.shortcutsEnabled
        onActivated: timeline.clearTimelineSelection()
    }
    Shortcut {
        sequence: "Escape"
        enabled: root.shortcutsEnabled
            && timelineController.selectedClipIds.length > 0
        onActivated: timeline.clearTimelineSelection()
    }
    Shortcut {
        sequence: "I"
        enabled: root.shortcutsEnabled && root.canEdit
            && workspaceController.timelineDurationFrames > 0
        onActivated: timelineController.setSequenceInPoint(previewViewport.position)
    }
    Shortcut {
        sequence: "O"
        enabled: root.shortcutsEnabled && root.canEdit
            && workspaceController.timelineDurationFrames > 0
        onActivated: timelineController.setSequenceOutPoint(previewViewport.position)
    }
    Shortcut {
        sequence: "Ctrl+Shift+X"
        enabled: root.shortcutsEnabled && root.canEdit
            && workspaceController.hasSequenceInOut
        onActivated: timelineController.clearSequenceInOut()
    }
    Shortcut {
        sequence: "Left"
        enabled: root.shortcutsEnabled
        onActivated: previewViewport.seek(previewViewport.position - 1)
    }
    Shortcut {
        sequence: "Right"
        enabled: root.shortcutsEnabled
        onActivated: previewViewport.seek(previewViewport.position + 1)
    }
    Shortcut {
        sequence: "Home"
        enabled: root.shortcutsEnabled
        onActivated: previewViewport.seek(0)
    }
    Shortcut {
        sequence: "End"
        enabled: root.shortcutsEnabled
            && workspaceController.timelineDurationFrames > 0
        onActivated: previewViewport.seek(workspaceController.timelineDurationFrames - 1)
    }
    Shortcut {
        sequence: "\\"
        enabled: root.shortcutsEnabled
        onActivated: timeline.fitTimeline()
    }
    Shortcut {
        sequence: "Ctrl+S"
        enabled: root.shortcutsEnabled && root.canEdit
        onActivated: workspaceController.saveProject()
    }
    Shortcut {
        sequence: "M"
        enabled: root.shortcutsEnabled && root.canEdit
        onActivated: timelineController.addTimelineMarker(previewViewport.position)
    }
}
