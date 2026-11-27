import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"
Rectangle {
    id: root
    objectName: "workspace"
    color: Theme.window
    property string activeMode: mediaflow.workspaceViewController.workspaceModes.length > 0
        ? String(mediaflow.workspaceViewController.workspaceModes[0].key) : ""
    property var exportPreviewOptions: ({})
    property alias previewMode: previewPanel.previewMode
    readonly property var previewViewport: previewPanel.viewport
    readonly property Item tourToolPanel: toolPanelContainer
    readonly property Item tourPreviewPanel: previewPanel
    readonly property Item tourInspectorPanel: inspectorPanel
    readonly property Item tourTimelinePanel: timeline
    readonly property int workspaceNavigationHeight: Theme.workspaceNavigationHeight
    readonly property int workspaceGutter: Theme.workspaceOuterGutter
    readonly property Item focusedItem: root.Window.window
        ? root.Window.window.activeFocusItem : null
    readonly property bool textInputActive: focusedItem instanceof TextInput
        || focusedItem instanceof TextEdit
    readonly property bool controlInputActive: {
        let item = focusedItem;
        while (item && item !== root) {
            if (item instanceof AbstractButton || item instanceof Slider
                    || item instanceof SpinBox || item instanceof ComboBox || item instanceof Dial)
                return true;
            item = item.parent;
        }
        return false;
    }
    readonly property bool modalOpen: workspaceChrome.modalOpen
        || Boolean(mediaPanelLoader.item && mediaPanelLoader.item.modalOpen)
        || Boolean(highlightPanelLoader.item && highlightPanelLoader.item.modalOpen)
        || Boolean(transcriptPanelLoader.item && transcriptPanelLoader.item.modalOpen)
        || Boolean(exportPanelLoader.item && exportPanelLoader.item.modalOpen)
        || inspectorPanel.modalOpen
        || timeline.modalOpen
        || Boolean(taskCenterPanelLoader.item
            && taskCenterPanelLoader.item.modalOpen)
        || Boolean(root.Window.window
            && (root.Window.window.downloadPlanVisible
                || root.Window.window.projectVersionsVisible
                || root.Window.window.shortcutReferenceVisible))
    readonly property bool shortcutsEnabled: !root.textInputActive
        && !previewPanel.webInputActive
        && !root.modalOpen
    readonly property bool navigationShortcutsEnabled: root.shortcutsEnabled && !root.controlInputActive
    readonly property bool canEdit:
        mediaflow.workspaceViewController.actionCapabilities.canEdit
    property string layoutPreset: String(
        mediaflow.settingsController.settingsData.workspaceLayoutPreset || "standard")
    property string maximizedPanel: ""
    property real toolPanelWidth: 520
    property real inspectorPanelWidth: 400
    property real timelinePanelHeight: 330
    property bool toolPanelVisible: true
    property bool inspectorPanelVisible: true
    property bool timelinePanelVisible: true
    property string compactPanel: ""
    readonly property bool compactWidth: width < Theme.workspaceToolMinimumWidth
        + Theme.workspacePreviewMinimumWidth + Theme.workspaceInspectorMinimumWidth + workspaceGutter * 4
    readonly property bool narrowWidth: width < Theme.workspaceToolMinimumWidth
        + Theme.workspacePreviewMinimumWidth + workspaceGutter * 3
    readonly property int previewMinimumHeight: 190
    readonly property int panelMinimumHeight: !root.mediaLayout
        && ((!narrowWidth && (toolPanelVisible || inspectorPanelVisible)) || compactPanel.length > 0)
        ? 320 : previewMinimumHeight
    readonly property bool compactHeight: height < panelMinimumHeight
        + Theme.workspaceTimelineMinimumHeight + workspaceGutter * 3
    readonly property real fittedTimelineHeight: Math.max(Theme.workspaceTimelineMinimumHeight,
        Math.min(timelinePanelHeight, height - workspaceGutter * 3 - panelMinimumHeight))
    readonly property bool mediaLayout: layoutPreset === "media"
    readonly property bool toolShown: maximizedPanel.length > 0
        ? maximizedPanel === "tool" : toolPanelVisible
            && (!compactWidth || compactPanel !== "inspector")
            && (!narrowWidth || compactPanel === "tool")
    readonly property bool previewShown: maximizedPanel.length > 0
        ? maximizedPanel === "preview" : !narrowWidth || compactPanel.length === 0
    readonly property bool inspectorShown: maximizedPanel.length > 0
        ? maximizedPanel === "inspector" : inspectorPanelVisible
            && (!compactWidth || compactPanel === "inspector" || !toolPanelVisible)
            && (!narrowWidth || compactPanel === "inspector")
    readonly property bool timelineShown: maximizedPanel.length > 0
        ? maximizedPanel === "timeline" : timelinePanelVisible && !compactHeight
    // Fit saved side-pane widths around the preview's minimum, without
    // overwriting the user's preferred widths when the window gets smaller.
    readonly property real sidePanelBudget: Math.max(0, width
        - workspaceGutter * (2 + (toolShown && previewShown ? 1 : 0)
            + (previewShown && inspectorShown ? 1 : 0))
        - (previewShown ? Theme.workspacePreviewMinimumWidth : 0))
    readonly property real fittedToolPanelWidth: Math.max(Theme.workspaceToolMinimumWidth,
        Math.min(toolPanelWidth, sidePanelBudget
            - (inspectorShown ? Math.max(Theme.workspaceInspectorMinimumWidth, inspectorPanelWidth) : 0)))
    readonly property real fittedInspectorPanelWidth: Math.max(Theme.workspaceInspectorMinimumWidth,
        Math.min(inspectorPanelWidth, sidePanelBudget - (toolShown ? fittedToolPanelWidth : 0)))

    Connections {
        target: mediaflow.workspacePlaybackController
        function onRemoteModeRequested(mode) {
            const requested = String(mode);
            const available = mediaflow.workspaceViewController.workspaceModes.some(
                function (item) { return String(item.key) === requested; });
            if (available)
                root.activeMode = requested;
        }
    }

    function layoutData(preset) {
        const layouts = mediaflow.settingsController.settingsData.workspaceLayouts || {};
        return layouts[String(preset)] || {};
    }

    function syncWorkspaceLayout() {
        const preset = String(
            mediaflow.settingsController.settingsData.workspaceLayoutPreset || "standard");
        const layout = root.layoutData(preset);
        root.layoutPreset = preset;
        root.toolPanelWidth = Number(layout.left_panel_width || 520);
        root.inspectorPanelWidth = Number(layout.inspector_panel_width || 400);
        root.timelinePanelHeight = Number(layout.timeline_height || 330);
        root.toolPanelVisible = layout.tool_panel_visible === undefined
            ? true : Boolean(layout.tool_panel_visible);
        root.inspectorPanelVisible = layout.inspector_panel_visible === undefined
            ? true : Boolean(layout.inspector_panel_visible);
        root.timelinePanelVisible = layout.timeline_visible === undefined
            ? true : Boolean(layout.timeline_visible);
    }

    function setWorkspaceLayoutPreset(preset) {
        root.maximizedPanel = "";
        root.compactPanel = "";
        mediaflow.workspaceSettingsController.setWorkspaceLayoutPreset(String(preset));
    }

    function toggleWorkspacePanel(panel) {
        if (root.compactHeight && panel === "timeline" && root.maximizedPanel === "timeline") {
            root.maximizedPanel = "";
            return;
        }
        if (root.maximizedPanel.length > 0)
            root.maximizedPanel = "";
        if (panel === "timeline" && root.compactHeight) {
            root.maximizedPanel = "timeline";
            return;
        }
        if (root.compactWidth && panel === "inspector" && !root.inspectorShown) {
            root.compactPanel = "inspector";
            root.inspectorPanelVisible = true;
            return;
        }
        if (root.compactWidth && panel === "tool" && !root.toolShown) {
            root.compactPanel = "tool";
            root.toolPanelVisible = true;
            return;
        }
        if (root.compactWidth && panel === root.compactPanel) {
            root.compactPanel = "";
            if (root.narrowWidth || panel === "inspector")
                return;
        }
        if (panel === "tool")
            root.toolPanelVisible = !root.toolPanelVisible;
        else if (panel === "inspector")
            root.inspectorPanelVisible = !root.inspectorPanelVisible;
        else if (panel === "timeline")
            root.timelinePanelVisible = !root.timelinePanelVisible;
        root.persistPanelLayout();
    }

    function togglePanelMaximized(panel) {
        root.maximizedPanel = root.maximizedPanel === panel ? "" : String(panel);
    }

    Component.onCompleted: root.syncWorkspaceLayout()

    Connections {
        target: mediaflow.workspaceSettingsController
        function onSettingsChanged() { root.syncWorkspaceLayout(); }
    }
    function panelIndexForMode(modeKey) {
        const modes = mediaflow.workspaceViewController.workspaceModes;
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
        previewViewport.playPreviewFrom(root.previewMode === "source"
            ? previewViewport.position : timeline.visiblePlayheadFrame);
    }

    function playReversePreview() {
        previewViewport.playReversePreviewFrom(root.previewMode === "source"
            ? previewViewport.position : timeline.visiblePlayheadFrame);
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
        mediaflow.workspaceSettingsController.saveWorkspaceLayout(
            root.layoutPreset,
            Math.round(toolPanelWidth),
            Math.round(inspectorPanelWidth),
            Math.round(timelinePanelHeight),
            root.toolPanelVisible,
            root.inspectorPanelVisible,
            root.timelinePanelVisible);
    }

    function openMediaImportDialog() {
        root.activeMode = "media";
        Qt.callLater(function () {
            if (mediaPanelLoader.item)
                mediaPanelLoader.item.openImportDialog();
        });
    }

    function openSourceMonitor(assetId, frame) {
        mediaflow.mediaController.openSourceMonitor(String(assetId));
        if (mediaflow.mediaController.sourceMonitorData.assetId) {
            previewPanel.openSource(Math.max(0, frame || 0));
        }
    }

    function openExportPanel() {
        root.activeMode = "export";
    }

    function activeSequenceName() {
        for (let index = 0;
                index < mediaflow.workspaceViewController.sequencesModel.rowCount();
                ++index) {
            const sequence = mediaflow.workspaceViewController.sequencesModel.get(index);
            if (String(sequence.sequenceId)
                    === String(mediaflow.workspaceViewController.activeSequenceId))
                return String(sequence.displayName);
        }
        return qsTr("时间线");
    }

    Connections {
        target: mediaflow.workspaceViewController
        function onPreviewRangeRequested(startFrame, endFrame) {
            previewPanel.playProgramRange(startFrame, endFrame);
        }
    }

    Connections {
        target: mediaflow.taskController
        function onTaskCenterRequested() {
            root.activeMode = "tasks";
        }
    }

    GridLayout {
        anchors.fill: parent
        anchors.margins: root.workspaceGutter
        columns: 5
        columnSpacing: 0
        rowSpacing: 0

                Rectangle {
                    id: toolPanelContainer
                    objectName: "toolPanelContainer"
                    visible: root.toolShown
                    Layout.row: root.maximizedPanel === "tool" ? 0 : 0
                    Layout.column: root.maximizedPanel === "tool" ? 0 : 0
                    Layout.rowSpan: root.maximizedPanel === "tool"
                        ? 3 : root.mediaLayout ? 3 : 1
                    Layout.columnSpan: root.maximizedPanel === "tool" ? 5 : 1
                    Layout.preferredWidth: root.fittedToolPanelWidth
                    Layout.minimumWidth: root.maximizedPanel === "tool"
                        ? 0 : Theme.workspaceToolMinimumWidth
                    Layout.fillWidth: root.maximizedPanel === "tool"
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
                            onSettingsRequested: workspaceChrome.openSettings()
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
                                    dragPreview: workspaceChrome.dragPreview
                                    playheadFrame: timeline.visiblePlayheadFrame
                                    pixelsPerFrame: timeline.pixelsPerFrame
                                    snapEnabled: timeline.snapEnabled
                                    onSourceRequested: function (assetId, frame) {
                                        root.openSourceMonitor(assetId, frame);
                                    }
                                }
                            }
                            Loader {
                                id: resourceLibraryPanelLoader
                                property string panelObjectName: "resourceLibraryPanel"
                                active: root.activeMode === "resources"
                                    || status === Loader.Ready
                                sourceComponent: ResourceLibraryPanel {
                                    playheadFrame: timeline.visiblePlayheadFrame
                                    pixelsPerFrame: timeline.pixelsPerFrame
                                    snapEnabled: timeline.snapEnabled
                                }
                            }
                            Loader {
                                id: transcriptPanelLoader
                                property string panelObjectName: "transcriptWorkspace"
                                active: root.activeMode === "transcript"
                                    || status === Loader.Ready
                                sourceComponent: TranscriptWorkspace {
                                    playheadFrame: previewPanel.programFrame
                                    playbackActive: root.previewMode === "program" && previewViewport.playing
                                    onImportRequested: root.openMediaImportDialog()
                                    onSeekRequested: function (frame) {
                                        previewPanel.seekProgram(frame);
                                    }
                                }
                            }
                            Loader {
                                id: highlightPanelLoader
                                property string panelObjectName: "highlightPanel"
                                active: root.activeMode === "highlight"
                                    || status === Loader.Ready
                                sourceComponent: HighlightPanel {
                                    playheadFrame: previewPanel.programFrame
                                }
                            }
                            Loader {
                                property string panelObjectName: "audioScroll"
                                active: root.activeMode === "audio"
                                    || status === Loader.Ready
                                sourceComponent: AudioPanel {}
                            }
                            Loader {
                                id: exportPanelLoader
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
                    objectName: "toolPanelResizeHandle"
                    visible: root.toolShown && root.previewShown
                        && root.maximizedPanel.length === 0
                    Layout.row: 0
                    Layout.column: 1
                    Layout.rowSpan: root.mediaLayout ? 3 : 1
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
                                leftResizeHandle.startWidth = toolPanelContainer.width;
                            else
                                root.persistPanelLayout();
                        }
                        onTranslationChanged: root.toolPanelWidth = Math.max(
                            Theme.workspaceToolMinimumWidth,
                            Math.min(680,
                                Math.max(Theme.workspaceToolMinimumWidth, root.sidePanelBudget
                                    - (root.inspectorShown ? inspectorPanel.width : 0)),
                                leftResizeHandle.startWidth + translation.x))
                    }
                }

                WorkspacePreviewPanel {
                    id: previewPanel
                    timelineView: timeline
                    sequenceName: root.activeSequenceName()
                    activeMode: root.activeMode
                    exportPreviewOptions: root.exportPreviewOptions
                    visible: root.previewShown
                    Layout.row: root.maximizedPanel === "preview" ? 0 : 0
                    Layout.column: root.maximizedPanel === "preview" ? 0 : 2
                    Layout.rowSpan: root.maximizedPanel === "preview" ? 3 : 1
                    Layout.columnSpan: root.maximizedPanel === "preview" ? 5 : 1
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: root.maximizedPanel === "preview"
                        ? 0 : Theme.workspacePreviewMinimumWidth
                    Layout.minimumHeight: root.maximizedPanel === "preview" ? 0 : root.previewMinimumHeight
                }

                Rectangle {
                    id: inspectorResizeHandle
                    visible: root.previewShown && root.inspectorShown
                        && root.maximizedPanel.length === 0
                    Layout.row: 0
                    Layout.column: 3
                    Layout.preferredWidth: root.workspaceGutter
                    Layout.fillHeight: true
                    color: Theme.window

                    property real startWidth: 0

                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: inspectorDrag.active ? 2 : 1
                        color: inspectorDrag.active ? Theme.accent
                            : inspectorResizeHover.hovered ? Theme.borderStrong
                            : Theme.transparent
                    }

                    HoverHandler { id: inspectorResizeHover }

                    DragHandler {
                        id: inspectorDrag
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        onActiveChanged: {
                            if (active)
                                inspectorResizeHandle.startWidth = inspectorPanel.width;
                            else
                                root.persistPanelLayout();
                        }
                        onTranslationChanged: root.inspectorPanelWidth = Math.max(
                            Theme.workspaceInspectorMinimumWidth,
                            Math.min(560,
                                Math.max(Theme.workspaceInspectorMinimumWidth, root.sidePanelBudget
                                    - (root.toolShown ? toolPanelContainer.width : 0)),
                                inspectorResizeHandle.startWidth - translation.x))
                    }
                }

                InspectorPanel {
                    id: inspectorPanel
                    objectName: "inspectorPanel"
                    visible: root.inspectorShown
                    Layout.row: root.maximizedPanel === "inspector" ? 0 : 0
                    Layout.column: root.maximizedPanel === "inspector" ? 0 : 4
                    Layout.rowSpan: root.maximizedPanel === "inspector" ? 3 : 1
                    Layout.columnSpan: root.maximizedPanel === "inspector" ? 5 : 1
                    Layout.preferredWidth: root.fittedInspectorPanelWidth
                    Layout.minimumWidth: root.maximizedPanel === "inspector"
                        ? 0 : Theme.workspaceInspectorMinimumWidth
                    Layout.fillWidth: root.maximizedPanel === "inspector"
                    Layout.fillHeight: true
                    playheadFrame: previewPanel.programFrame
                    onEditProfileRequested: workspaceChrome.openSequenceProfile()
                    onSeekRequested: function(frame) {
                        previewPanel.seekProgram(frame);
                    }
                }
                Rectangle {
                    id: timelineResizeHandle
                    visible: root.timelineShown && root.maximizedPanel.length === 0
                        && (root.previewShown || root.inspectorShown)
                    Layout.row: 1
                    Layout.column: root.mediaLayout ? 2 : 0
                    Layout.columnSpan: root.mediaLayout ? 3 : 5
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
                visible: root.timelineShown
                Layout.row: root.maximizedPanel === "timeline" ? 0 : 2
                Layout.column: root.maximizedPanel === "timeline"
                    ? 0 : root.mediaLayout ? 2 : 0
                Layout.rowSpan: root.maximizedPanel === "timeline" ? 3 : 1
                Layout.columnSpan: root.maximizedPanel === "timeline"
                    ? 5 : root.mediaLayout ? 3 : 5
                Layout.fillWidth: true
                Layout.fillHeight: root.maximizedPanel === "timeline"
                Layout.preferredHeight: root.fittedTimelineHeight
                Layout.minimumHeight: root.maximizedPanel === "timeline"
                    ? 0 : Theme.workspaceTimelineMinimumHeight
                playheadFrame: previewPanel.programFrame
                shortcutsEnabled: root.shortcutsEnabled
                onPlayheadScrubbingChanged: {
                    if (timeline.playheadScrubbing)
                        root.beginPreviewScrub();
                    else
                        root.endPreviewScrub();
                }
                onSeekRequested: function (frame) {
                    previewPanel.seekProgram(frame);
                }
                onEditProfileRequested: workspaceChrome.openSequenceProfile()
                onCreateVariantsRequested: workspaceChrome.openSequenceVariants()
                onCreateMulticamRequested: workspaceChrome.openMulticam()
                onOpenColorScopesRequested: workspaceChrome.openColorScopes()
                onOpenVoiceoverRequested: workspaceChrome.openVoiceover()
            }
    }

    WorkspaceChrome {
        id: workspaceChrome
        anchors.fill: parent
        host: root
        preview: previewViewport
        timelineView: timeline
        toolPanelWidth: toolPanelContainer.width
        previewPanelWidth: previewPanel.width
        gutter: root.workspaceGutter
        onOpenExportRequested: root.activeMode = "export"
    }
}
