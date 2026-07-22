import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    objectName: "workspace"
    color: Theme.window
    property string activeMode: "media"
    property var exportPreviewOptions: ({})
    readonly property int workspaceNavigationHeight: 50
    readonly property int workspaceBannerHeight: taskController.downloadProgressVisible ? 64 : 0
    readonly property Item focusedItem: root.Window.window
        ? root.Window.window.activeFocusItem : null
    readonly property bool textInputActive: focusedItem instanceof TextInput
        || focusedItem instanceof TextEdit
    property real toolPanelWidth: Math.max(340, settingsController.settingsData.leftPanelWidth || 360)
    property real timelinePanelHeight: Math.max(210, settingsController.settingsData.timelineHeight || 330)
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
                    border.color: Theme.border
                    StackLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        currentIndex: root.activeMode === "media" ? 0
                            : root.activeMode === "transcript" ? 1
                            : root.activeMode === "subtitle" ? 2
                            : root.activeMode === "translate" ? 3
                            : root.activeMode === "highlight" ? 4
                            : root.activeMode === "edit" ? 5
                            : root.activeMode === "audio" ? 6
                            : root.activeMode === "export" ? 7 : 8

                        MediaPanel {
                            id: mediaPanel
                            dragPreview: mediaDragPreview
                            playheadFrame: timeline.visiblePlayheadFrame
                            pixelsPerFrame: timeline.pixelsPerFrame
                            snapEnabled: timeline.snapEnabled
                        }
                        TranscriptPanel {
                            onModeRequested: function (mode) {
                                root.activeMode = mode;
                            }
                        }
                        SubtitlePanel {
                            playheadFrame: previewViewport.position
                            playbackActive: previewViewport.playing
                            onModeRequested: function (mode) {
                                root.activeMode = mode;
                            }
                            onImportRequested: {
                                root.activeMode = "media";
                                mediaPanel.openImportDialog();
                            }
                            onSeekRequested: function (frame) {
                                previewViewport.seek(frame);
                            }
                        }
                        TranslationPanel {
                            onModeRequested: function (mode) {
                                root.activeMode = mode;
                            }
                            onImportRequested: {
                                root.activeMode = "media";
                                mediaPanel.openImportDialog();
                            }
                        }
                        HighlightPanel {
                            playheadFrame: previewViewport.position
                        }
                        EditPanel { playheadFrame: previewViewport.position }
                        AudioPanel {}
                        ExportPanel {
                            id: exportPanel
                            onPreviewConfigurationChanged: function (options) {
                                root.exportPreviewOptions = options;
                            }
                        }
                        TaskCenterPanel {}
                    }
                }

                Rectangle {
                    id: leftResizeHandle
                    Layout.preferredWidth: 6
                    Layout.fillHeight: true
                    color: leftDrag.active ? Theme.accent : Theme.border
                    property real startWidth: 0
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

                WebEditorCanvas {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 260
                    visible: root.activeMode === "edit" && webController.isWebClip && webController.editMode
                    playheadFrame: previewViewport.position
                }
            }

            Rectangle {
                id: timelineResizeHandle
                Layout.fillWidth: true
                Layout.preferredHeight: 6
                color: timelineDrag.active ? Theme.accent : Theme.border
                property real startHeight: 0
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

    Dialog {
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

    Dialog {
        id: sequenceProfileDialog
        anchors.centerIn: parent
        implicitWidth: 440
        width: 440
        modal: true
        title: qsTr("序列配置")
        standardButtons: Dialog.Save | Dialog.Cancel
        onOpened: {
            profileWidth.text = String(workspaceController.profileWidth);
            profileHeight.text = String(workspaceController.profileHeight);
            for (var index = 0; index < frameRate.model.length; ++index) {
                var item = frameRate.model[index];
                if (item.n === workspaceController.profileFpsNumerator && item.d === workspaceController.profileFpsDenominator) {
                    frameRate.currentIndex = index;
                    break;
                }
            }
            colorProfile.currentIndex = workspaceController.colorMode === "hdr10_bt2020_pq" ? 1 : 0;
            for (var channelIndex = 0; channelIndex < audioChannels.model.length; ++channelIndex) {
                if (audioChannels.model[channelIndex].value === workspaceController.profileAudioChannels) {
                    audioChannels.currentIndex = channelIndex;
                    break;
                }
            }
        }
        onAccepted: {
            var fps = frameRate.model[frameRate.currentIndex];
            var color = colorProfile.model[colorProfile.currentIndex];
            var channels = audioChannels.model[audioChannels.currentIndex];
            workspaceController.updateSequenceProfile(Number(profileWidth.text), Number(profileHeight.text), fps.n, fps.d, color.value, channels.value);
        }
        contentItem: ColumnLayout {
            spacing: 10
            Text {
                text: qsTr("画布比例")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppComboBox {
                Layout.fillWidth: true
                textRole: "label"
                model: [
                    {
                        label: "16:9 · 1920×1080",
                        width: 1920,
                        height: 1080
                    },
                    {
                        label: "9:16 · 1080×1920",
                        width: 1080,
                        height: 1920
                    },
                    {
                        label: "1:1 · 1080×1080",
                        width: 1080,
                        height: 1080
                    },
                    {
                        label: "4:5 · 1080×1350",
                        width: 1080,
                        height: 1350
                    }
                ]
                onActivated: function (index) {
                    profileWidth.text = String(model[index].width);
                    profileHeight.text = String(model[index].height);
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AppTextField {
                    id: profileWidth
                    Layout.fillWidth: true
                    validator: IntValidator {
                        bottom: 16
                        top: 16384
                    }
                    placeholderText: qsTr("宽度")
                }
                Text {
                    text: "×"
                    color: Theme.textMuted
                }
                AppTextField {
                    id: profileHeight
                    Layout.fillWidth: true
                    validator: IntValidator {
                        bottom: 16
                        top: 16384
                    }
                    placeholderText: qsTr("高度")
                }
            }
            Text {
                text: qsTr("帧率")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppComboBox {
                id: frameRate
                Layout.fillWidth: true
                textRole: "label"
                model: [
                    {
                        label: "23.976 fps",
                        n: 24000,
                        d: 1001
                    },
                    {
                        label: "24 fps",
                        n: 24,
                        d: 1
                    },
                    {
                        label: "25 fps",
                        n: 25,
                        d: 1
                    },
                    {
                        label: "29.97 fps",
                        n: 30000,
                        d: 1001
                    },
                    {
                        label: "30 fps",
                        n: 30,
                        d: 1
                    },
                    {
                        label: "50 fps",
                        n: 50,
                        d: 1
                    },
                    {
                        label: "59.94 fps",
                        n: 60000,
                        d: 1001
                    },
                    {
                        label: "60 fps",
                        n: 60,
                        d: 1
                    }
                ]
                currentIndex: 4
            }
            Text {
                text: qsTr("色彩与输出声道")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            RowLayout {
                Layout.fillWidth: true
                AppComboBox {
                    id: colorProfile
                    Layout.fillWidth: true
                    textRole: "label"
                    model: [
                        {
                            label: "SDR · BT.709",
                            value: "sdr_bt709"
                        },
                        {
                            label: "HDR10 · BT.2020 · PQ",
                            value: "hdr10_bt2020_pq"
                        }
                    ]
                }
                AppComboBox {
                    id: audioChannels
                    Layout.fillWidth: true
                    textRole: "label"
                    model: [
                        {
                            label: qsTr("单声道"),
                            value: 1
                        },
                        {
                            label: qsTr("立体声"),
                            value: 2
                        },
                        {
                            label: "5.1",
                            value: 6
                        }
                    ]
                    currentIndex: 1
                }
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("修改帧率会按实际时长重新换算片段、转场和字幕；预览缓存会按需重建。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
        }
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
        enabled: !root.textInputActive && !workspaceController.readOnly
        onActivated: {
            root.activeMode = "media";
            mediaPanel.openImportDialog();
        }
    }
    Shortcut {
        sequence: "Ctrl+M"
        enabled: !root.textInputActive
        onActivated: root.activeMode = "export"
    }
    Shortcut {
        sequence: "Space"
        enabled: !root.textInputActive
        autoRepeat: false
        onActivated: previewViewport.playing || previewViewport.playbackRequested
            ? previewViewport.pause() : root.playPreview()
    }
    Shortcut {
        sequence: "F11"
        onActivated: root.toggleFullscreen()
    }
    Shortcut {
        sequence: "J"
        enabled: !root.textInputActive
        onActivated: {
            previewViewport.playbackRate = previewViewport.playing && previewViewport.playbackRate < 0
                ? Math.max(-4, previewViewport.playbackRate * 2) : -1.0;
            root.playReversePreview();
        }
    }
    Shortcut {
        sequence: "K"
        enabled: !root.textInputActive
        onActivated: previewViewport.pause()
    }
    Shortcut {
        sequence: "L"
        enabled: !root.textInputActive
        onActivated: {
            previewViewport.playbackRate = previewViewport.playing && previewViewport.playbackRate > 0
                ? Math.min(4, previewViewport.playbackRate * 2) : 1.0;
            root.playPreview();
        }
    }
    Shortcut {
        sequence: "S"
        enabled: !root.textInputActive
        onActivated: timeline.snapEnabled = !timeline.snapEnabled
    }
    Shortcut {
        sequence: "Ctrl+K"
        enabled: !root.textInputActive && timelineController.selectedClipId.length > 0
        onActivated: timelineController.splitClip(
            timelineController.selectedClipId, previewViewport.position)
    }
    Shortcut {
        sequence: "Ctrl+B"
        enabled: !root.textInputActive && timelineController.selectedClipId.length > 0
        onActivated: timelineController.splitClip(
            timelineController.selectedClipId, previewViewport.position)
    }
    Shortcut {
        sequence: "Delete"
        enabled: !root.textInputActive && timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(false)
    }
    Shortcut {
        sequence: "Shift+Delete"
        enabled: !root.textInputActive && timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(true)
    }
    Shortcut {
        sequence: "Ctrl+Z"
        enabled: !root.textInputActive && timelineController.canUndo
        onActivated: timelineController.undo()
    }
    Shortcut {
        sequences: ["Ctrl+Y", "Ctrl+Shift+Z"]
        enabled: !root.textInputActive && timelineController.canRedo
        onActivated: timelineController.redo()
    }
    Shortcut {
        sequence: "Ctrl+D"
        enabled: !root.textInputActive && timelineController.selectedClipId.length > 0
        onActivated: timelineController.duplicateClip(
            timelineController.selectedClipId,
            timeline.pixelsPerFrame,
            previewViewport.position)
    }
    Shortcut {
        sequence: "Ctrl+A"
        enabled: !root.textInputActive && timelineController.clipsModel.rowCount() > 0
        onActivated: timelineController.selectAllClips()
    }
    Shortcut {
        sequence: "Ctrl+Shift+A"
        enabled: !root.textInputActive
        onActivated: timelineController.clearSelection()
    }
    Shortcut {
        sequence: "I"
        enabled: !root.textInputActive && workspaceController.timelineDurationFrames > 0
        onActivated: timelineController.setSequenceInPoint(previewViewport.position)
    }
    Shortcut {
        sequence: "O"
        enabled: !root.textInputActive && workspaceController.timelineDurationFrames > 0
        onActivated: timelineController.setSequenceOutPoint(previewViewport.position)
    }
    Shortcut {
        sequence: "Ctrl+Shift+X"
        enabled: !root.textInputActive && workspaceController.hasSequenceInOut
        onActivated: timelineController.clearSequenceInOut()
    }
    Shortcut {
        sequence: "Left"
        enabled: !root.textInputActive
        onActivated: previewViewport.seek(previewViewport.position - 1)
    }
    Shortcut {
        sequence: "Right"
        enabled: !root.textInputActive
        onActivated: previewViewport.seek(previewViewport.position + 1)
    }
    Shortcut {
        sequence: "Home"
        enabled: !root.textInputActive
        onActivated: previewViewport.seek(0)
    }
    Shortcut {
        sequence: "End"
        enabled: !root.textInputActive && workspaceController.timelineDurationFrames > 0
        onActivated: previewViewport.seek(workspaceController.timelineDurationFrames - 1)
    }
    Shortcut {
        sequence: "\\"
        enabled: !root.textInputActive
        onActivated: timeline.fitTimeline()
    }
    Shortcut {
        sequence: "Ctrl+S"
        onActivated: workspaceController.saveProject()
    }
    Shortcut {
        sequence: "M"
        enabled: !root.textInputActive
        onActivated: timelineController.addTimelineMarker(previewViewport.position)
    }
}
