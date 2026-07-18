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
    readonly property int workspaceNavigationHeight: 50
    readonly property int workspaceBannerHeight: (taskController.downloadProgressVisible ? 64 : 0)
        + (workflowBanner.visible ? 58 : 0)
    property real toolPanelWidth: Math.max(220, settingsController.settingsData.leftPanelWidth || 286)
    property real inspectorPanelWidth: Math.max(250, settingsController.settingsData.inspectorWidth || 310)
    property bool inspectorDrawerOpen: false
    property real timelinePanelHeight: Math.max(210, settingsController.settingsData.timelineHeight || 330)
    readonly property bool taskFocusedMode: activeMode === "transcript" || activeMode === "translate" || activeMode === "highlight" || activeMode === "audio" || activeMode === "export"
    readonly property bool timelineVisible: activeMode !== "translate" && activeMode !== "export"
    readonly property bool dockedInspectorVisible: !taskFocusedMode && width >= 1320
    readonly property bool compactInspectorAvailable: taskFocusedMode || width < 1320
    readonly property real focusedTaskPanelWidth: Math.max(540, Math.min(1040, Math.round((width - 96) * 0.52)))
    readonly property int sequencePreviewIn: workspaceController.hasSequenceInOut ? workspaceController.sequenceInFrame : 0
    readonly property int sequencePreviewOut: workspaceController.hasSequenceInOut ? workspaceController.sequenceOutFrame : Math.max(1, previewViewport.duration)

    onWidthChanged: {
        if (!compactInspectorAvailable)
            inspectorDrawerOpen = false;
    }

    function toggleFullscreen() {
        previewViewport.toggleFullscreen();
    }

    function playPreview() {
        previewViewport.playPreview();
    }

    function playReversePreview() {
        previewViewport.playReversePreview();
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

    Component {
        id: inspectorPanelComponent
        InspectorPanel {}
    }

    function resetPreviewViewport() {
        previewViewport.resetViewport();
    }

    function persistPanelLayout() {
        settingsController.savePanelLayout(Math.round(toolPanelWidth), Math.round(inspectorPanelWidth), Math.round(timelinePanelHeight));
    }

    Connections {
        target: workspaceController
        function onPreviewRangeRequested(startFrame, endFrame) {
            previewViewport.playRequestedRange(startFrame, endFrame);
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        WorkspaceNavigation {
            Layout.fillWidth: true
            Layout.preferredHeight: root.workspaceNavigationHeight
            activeMode: root.activeMode
            compactInspectorAvailable: root.compactInspectorAvailable
            inspectorDrawerOpen: root.inspectorDrawerOpen
            onModeRequested: function (mode) {
                root.activeMode = mode;
            }
            onSettingsRequested: settingsDialog.open()
            onInspectorDrawerToggled: root.inspectorDrawerOpen = !root.inspectorDrawerOpen
        }

        DownloadProgressBanner {
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 64 : 0
        }

        WorkflowBanner {
            id: workflowBanner
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 58 : 0
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
                    Layout.preferredWidth: root.taskFocusedMode ? root.focusedTaskPanelWidth : root.toolPanelWidth
                    Layout.fillHeight: true
                    color: Theme.surface
                    border.color: Theme.border
                    StackLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        currentIndex: root.activeMode === "media" ? 0 : root.activeMode === "transcript" ? 1 : root.activeMode === "translate" ? 2 : root.activeMode === "highlight" ? 3 : root.activeMode === "audio" ? 4 : root.activeMode === "export" ? 5 : 6

                        MediaPanel {}
                        TranscriptPanel {}
                        TranslationPanel {}
                        HighlightPanel {}
                        AudioPanel {}
                        ExportPanel {}
                        EditPanel {}
                    }
                }

                Rectangle {
                    id: leftResizeHandle
                    Layout.preferredWidth: root.taskFocusedMode ? 0 : 6
                    Layout.fillHeight: true
                    visible: !root.taskFocusedMode
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
                        onTranslationChanged: root.toolPanelWidth = Math.max(220, Math.min(520, leftResizeHandle.startWidth + translation.x))
                    }
                }

                PreviewViewport {
                    id: previewViewport
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 260
                    sequenceIn: root.sequencePreviewIn
                    sequenceOut: root.sequencePreviewOut
                    source: workspaceController.previewGraphPath
                    runtimeRoot: workspaceController.mltRuntimeRoot
                    reloadToken: workspaceController.previewGraphRevision
                    hdrEnabled: workspaceController.colorMode === "hdr10_bt2020_pq"
                    profileWidth: workspaceController.profileWidth
                    profileHeight: workspaceController.profileHeight
                    subtitleText: subtitleController.subtitleTextAtFrame(position)
                    onDroppedFramesReported: function (count) { workspaceController.reportPreviewDroppedFrames(count) }
                    onHdrActiveReported: function (active) { workspaceController.reportHdrPreviewActive(active) }
                }

                Rectangle {
                    id: inspectorResizeHandle
                    Layout.preferredWidth: root.dockedInspectorVisible ? 6 : 0
                    Layout.fillHeight: true
                    visible: root.dockedInspectorVisible
                    color: inspectorDrag.active ? Theme.accent : Theme.border
                    property real startWidth: 0
                    DragHandler {
                        id: inspectorDrag
                        target: null
                        xAxis.enabled: true
                        yAxis.enabled: false
                        onActiveChanged: {
                            if (active)
                                inspectorResizeHandle.startWidth = root.inspectorPanelWidth;
                            else
                                root.persistPanelLayout();
                        }
                        onTranslationChanged: root.inspectorPanelWidth = Math.max(250, Math.min(520, inspectorResizeHandle.startWidth - translation.x))
                    }
                }

                Rectangle {
                    id: inspectorContainer
                    objectName: "inspectorContainer"
                    Layout.preferredWidth: root.dockedInspectorVisible ? root.inspectorPanelWidth : 0
                    Layout.fillHeight: true
                    visible: root.dockedInspectorVisible
                    color: Theme.surface
                    border.color: Theme.border
                    Loader {
                        anchors.fill: parent
                        anchors.margins: 14
                        active: root.dockedInspectorVisible
                        sourceComponent: inspectorPanelComponent
                    }
                }
            }

            Rectangle {
                id: timelineResizeHandle
                Layout.fillWidth: true
                Layout.preferredHeight: root.timelineVisible ? 6 : 0
                visible: root.timelineVisible
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
                Layout.preferredHeight: root.timelineVisible ? root.timelinePanelHeight : 0
                Layout.minimumHeight: root.timelineVisible ? 210 : 0
                visible: root.timelineVisible
                playheadFrame: previewViewport.position
                onPlayheadScrubbingChanged: {
                    if (playheadScrubbing)
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
        id: compactInspectorDrawer
        objectName: "compactInspectorDrawer"
        width: Math.min(360, Math.max(280, root.width - 80))
        y: root.workspaceNavigationHeight + root.workspaceBannerHeight
        height: Math.max(0, root.height - y)
        x: root.inspectorDrawerOpen && root.compactInspectorAvailable ? root.width - width : root.width + 8
        visible: root.compactInspectorAvailable
        enabled: visible && root.inspectorDrawerOpen
        z: 40
        color: Theme.surfaceFloating
        border.color: Theme.borderStrong
        border.width: 1
        radius: Theme.radiusLarge

        Behavior on x {
            NumberAnimation {
                duration: 220
                easing.type: Easing.OutCubic
            }
        }

        Loader {
            anchors.fill: parent
            anchors.margins: 14
            active: root.compactInspectorAvailable
            sourceComponent: inspectorPanelComponent
        }

        AppButton {
            anchors.top: parent.top
            anchors.right: parent.right
            anchors.topMargin: 9
            anchors.rightMargin: 10
            z: 1
            text: "×"
            Accessible.name: qsTr("关闭检查器")
            onClicked: root.inspectorDrawerOpen = false
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
                text: qsTr("修改帧率会按实际时长重新换算片段、转场和字幕；主序列的代理会自动失效并按需重建。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
        }
    }

    TaskDrawer {
        anchors.top: parent.top
        anchors.topMargin: root.workspaceNavigationHeight + root.workspaceBannerHeight
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        visible: taskController.taskDrawerOpen
        z: 50
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
        sequence: "Space"
        onActivated: previewViewport.playing ? previewViewport.pause() : root.playPreview()
    }
    Shortcut {
        sequence: "F11"
        onActivated: root.toggleFullscreen()
    }
    Shortcut {
        sequence: "J"
        onActivated: {
            previewViewport.playbackRate = -1.0;
            root.playReversePreview();
        }
    }
    Shortcut {
        sequence: "K"
        onActivated: previewViewport.pause()
    }
    Shortcut {
        sequence: "L"
        onActivated: {
            previewViewport.playbackRate = previewViewport.playbackRate > 0 ? Math.min(4, previewViewport.playbackRate * 2) : 1.0;
            root.playPreview();
        }
    }
    Shortcut {
        sequence: "S"
        enabled: timelineController.selectedClipId.length > 0
        onActivated: timelineController.splitClip(timelineController.selectedClipId, previewViewport.position)
    }
    Shortcut {
        sequence: "Delete"
        enabled: timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(false)
    }
    Shortcut {
        sequence: "Shift+Delete"
        enabled: timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(true)
    }
    Shortcut {
        sequence: "Ctrl+Z"
        enabled: timelineController.canUndo
        onActivated: timelineController.undo()
    }
    Shortcut {
        sequence: "Ctrl+Y"
        enabled: timelineController.canRedo
        onActivated: timelineController.redo()
    }
    Shortcut {
        sequence: "Ctrl+D"
        enabled: timelineController.selectedClipId.length > 0
        onActivated: timelineController.copyClip(timelineController.selectedClipId, timeline.pixelsPerFrame, previewViewport.position)
    }
    Shortcut {
        sequence: "Ctrl+S"
        onActivated: workspaceController.saveProject()
    }
    Shortcut {
        sequence: "M"
        onActivated: timelineController.addTimelineMarker(previewViewport.position)
    }
}
