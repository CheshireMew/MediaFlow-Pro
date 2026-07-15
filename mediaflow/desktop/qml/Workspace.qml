import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import MediaFlow.Native 1.0
import "."
import "components"

Rectangle {
    id: root
    objectName: "workspace"
    color: Theme.window
    property string activeMode: "media"
    readonly property string mainSequencePrefix: qsTr("主")
    readonly property string shortSequencePrefix: qsTr("短")
    property real toolPanelWidth: Math.max(220, projectController.settingsData.leftPanelWidth || 286)
    property real inspectorPanelWidth: Math.max(250, projectController.settingsData.inspectorWidth || 310)
    property real timelinePanelHeight: Math.max(210, projectController.settingsData.timelineHeight || 330)
    property int previewRangeEnd: -1

    function persistPanelLayout() {
        projectController.savePanelLayout(
            Math.round(toolPanelWidth),
            Math.round(inspectorPanelWidth),
            Math.round(timelinePanelHeight))
    }

    function sequenceDisplayName(kind, name) {
        if (kind === "main" && name === "主序列")
            return qsTr("主序列")
        if (kind === "short" && name.indexOf("短视频") === 0)
            return qsTr("短视频") + name.slice(3)
        return (kind === "short" ? root.shortSequencePrefix : root.mainSequencePrefix)
            + " · " + name
    }

    Connections {
        target: projectController
        function onPreviewRangeRequested(startFrame, endFrame) {
            root.previewRangeEnd = endFrame
            preview.seek(startFrame)
            preview.play()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 58
            color: "#0e1115"
            border.color: Theme.border
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 12
                spacing: 10
                Rectangle {
                    width: 34
                    height: 34
                    radius: 10
                    color: Theme.accent
                    Text { anchors.centerIn: parent; text: "M"; color: "white"; font.weight: Font.Bold }
                }
                ColumnLayout {
                    spacing: 1
                    Text { text: projectController.projectName; color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Text { text: projectController.projectPath; color: Theme.textMuted; font.pixelSize: 9; elide: Text.ElideMiddle; Layout.maximumWidth: 320 }
                }
                Text {
                    visible: projectController.readOnly
                    text: qsTr("只读")
                    color: Theme.warning
                    font.pixelSize: 11
                }
                Text {
                    visible: !projectController.readOnly
                    text: qsTr("已保存")
                    color: Theme.success
                    font.pixelSize: 10
                }
                Item { Layout.fillWidth: true }
                AppButton { text: "↶"; Accessible.name: qsTr("撤销"); enabled: projectController.canUndo; onClicked: projectController.undo() }
                AppButton { text: "↷"; Accessible.name: qsTr("重做"); enabled: projectController.canRedo; onClicked: projectController.redo() }
                AppButton { text: qsTr("任务"); onClicked: projectController.toggleTaskDrawer() }
                AppButton { primary: true; text: qsTr("导出"); onClicked: root.activeMode = "export" }
                AppButton { text: qsTr("关闭"); onClicked: projectController.closeProject() }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            color: Theme.surface
            border.color: Theme.border
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 66
                anchors.rightMargin: 14
                spacing: 8
                ListView {
                    id: sequenceList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    orientation: ListView.Horizontal
                    spacing: 6
                    model: projectController.sequencesModel
                    delegate: Rectangle {
                        required property string sequenceId
                        required property string name
                        required property string kind
                        width: Math.max(104, sequenceName.implicitWidth + 34)
                        height: 34
                        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                        radius: Theme.radiusSmall
                        color: projectController.activeSequenceId === sequenceId ? Theme.accentSoft : sequenceMouse.containsMouse ? Theme.surfaceHover : "transparent"
                        border.color: projectController.activeSequenceId === sequenceId ? Theme.accent : "transparent"
                        activeFocusOnTab: true
                        Accessible.name: root.sequenceDisplayName(kind, name)
                        Accessible.role: Accessible.Button
                        Keys.onReturnPressed: projectController.selectSequence(sequenceId)
                        Keys.onSpacePressed: projectController.selectSequence(sequenceId)
                        Text { id: sequenceName; anchors.centerIn: parent; text: root.sequenceDisplayName(kind, name); color: Theme.text; font.pixelSize: 12 }
                        MouseArea { id: sequenceMouse; anchors.fill: parent; hoverEnabled: true; onClicked: projectController.selectSequence(sequenceId) }
                    }
                }
                AppButton { text: "+ " + qsTr("短视频"); onClicked: shortDialog.open() }
                ComboBox {
                    id: workflowMode
                    objectName: "workflowMode"
                    Layout.preferredWidth: 224
                    textRole: "label"
                    valueRole: "value"
                    model: [
                        { label: qsTr("工作流：跟随全局"), value: "inherit" },
                        { label: qsTr("工作流：每步确认"), value: "confirm" },
                        { label: qsTr("工作流：自动继续"), value: "auto" }
                    ]
                    currentIndex: projectController.projectWorkflowMode === "auto" ? 2
                        : projectController.projectWorkflowMode === "confirm" ? 1 : 0
                    onActivated: function(index) {
                        projectController.setProjectWorkflowMode(model[index].value)
                    }
                }
                AppButton { text: projectController.profileLabel; onClicked: sequenceProfileDialog.open() }
                Rectangle {
                    width: 54
                    height: 24
                    radius: 12
                    color: projectController.colorMode === "hdr10_bt2020_pq" ? "#5e4617" : "#1a344f"
                    Text { anchors.centerIn: parent; text: projectController.colorMode === "hdr10_bt2020_pq" ? "HDR10" : "SDR"; color: Theme.text; font.pixelSize: 9; font.weight: Font.DemiBold }
                }
            }
        }

        Rectangle {
            id: workflowBanner
            objectName: "workflowBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 68 : 0
            visible: projectController.workflowPending
            color: projectController.workflowStatus === "blocked" ? "#352318" : Theme.accentSoft
            border.color: projectController.workflowStatus === "blocked" ? Theme.warning : Theme.accent

            function stageLabel(stage) {
                if (stage === "download") return qsTr("下载")
                if (stage === "prepare_media") return qsTr("媒体分析、代理与波形")
                if (stage === "transcribe") return qsTr("转录")
                if (stage === "translate") return qsTr("翻译")
                if (stage === "highlight") return qsTr("AI 高光分析")
                if (stage === "create_shorts") return qsTr("创建短视频草稿")
                if (stage === "export") return qsTr("导出")
                return qsTr("工作流")
            }

            function message(code) {
                if (code === "workflow_translation_language_required") return qsTr("请选择目标语言后继续。")
                if (code === "workflow_llm_provider_required") return qsTr("需要先配置并启用 LLM 提供商。")
                if (code === "workflow_export_settings_required") return qsTr("请在导出页选择格式和保存位置。")
                if (code === "workflow_offline_assets") return qsTr("工作流包含离线素材，请先重新关联。")
                if (code === "workflow_task_failed") return qsTr("阶段任务失败，可在任务中心查看原因后重试。")
                if (code === "workflow_task_cancelled") return qsTr("阶段任务已取消，可重新继续。")
                if (code.indexOf("_running") >= 0) return qsTr("正在执行，进度可在任务中心查看。")
                if (code.indexOf("_ready") >= 0) return qsTr("上一阶段已完成，确认后继续。")
                return qsTr("工作流已暂停，请处理当前阶段。")
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 66
                anchors.rightMargin: 14
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: workflowBanner.stageLabel(projectController.workflowStage)
                        color: Theme.text
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: workflowBanner.message(projectController.workflowMessageCode)
                        color: Theme.textMuted
                        font.pixelSize: 10
                        elide: Text.ElideRight
                    }
                }
                ComboBox {
                    id: workflowLanguage
                    objectName: "workflowLanguage"
                    visible: projectController.workflowStage === "translate"
                    Layout.preferredWidth: 150
                    textRole: "label"
                    valueRole: "value"
                    model: [
                        { label: qsTr("选择目标语言"), value: "" },
                        { label: qsTr("中文"), value: "zh_CN" },
                        { label: "English", value: "en" },
                        { label: qsTr("日本语"), value: "ja" }
                    ]
                    Component.onCompleted: {
                        const wanted = projectController.defaultTranslationLanguage
                        for (var index = 0; index < model.length; ++index) {
                            if (model[index].value === wanted) {
                                currentIndex = index
                                break
                            }
                        }
                    }
                }
                AppButton {
                    objectName: "workflowContinue"
                    primary: true
                    visible: projectController.workflowStatus !== "running"
                    enabled: projectController.workflowStage !== "translate"
                        || workflowLanguage.currentValue.length > 0
                    text: projectController.workflowMessageCode === "workflow_llm_provider_required"
                        ? qsTr("打开设置")
                        : projectController.workflowStage === "export" ? qsTr("前往导出") : qsTr("继续")
                    onClicked: {
                        if (projectController.workflowMessageCode === "workflow_llm_provider_required")
                            settingsDialog.open()
                        else if (projectController.workflowStage === "export")
                            root.activeMode = "export"
                        else
                            projectController.continueWorkflow(
                                projectController.workflowRunId,
                                workflowLanguage.currentValue || "")
                    }
                }
                AppButton {
                    text: qsTr("取消工作流")
                    onClicked: projectController.cancelWorkflow(projectController.workflowRunId)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Rectangle {
                Layout.preferredWidth: 58
                Layout.fillHeight: true
                color: "#0e1115"
                border.color: Theme.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.topMargin: 10
                    anchors.bottomMargin: 10
                    spacing: 6
                    Repeater {
                        model: [
                            {key: "media", label: qsTr("媒体")},
                            {key: "transcript", label: qsTr("转录")},
                            {key: "translate", label: qsTr("翻译")},
                            {key: "highlight", label: qsTr("高光")},
                            {key: "edit", label: qsTr("编辑")},
                            {key: "audio", label: qsTr("音频")},
                            {key: "export", label: qsTr("导出")}
                        ]
                        Rectangle {
                            required property var modelData
                            Layout.alignment: Qt.AlignHCenter
                            width: 44
                            height: 44
                            radius: 10
                            color: root.activeMode === modelData.key ? Theme.accentSoft : navMouse.containsMouse ? Theme.surfaceHover : "transparent"
                            NavIcon {
                                anchors.centerIn: parent
                                iconName: modelData.key
                                iconColor: root.activeMode === modelData.key ? Theme.accentHover : Theme.textMuted
                            }
                            Accessible.name: modelData.label
                            Accessible.role: Accessible.Button
                            activeFocusOnTab: true
                            Keys.onReturnPressed: root.activeMode = modelData.key
                            Keys.onSpacePressed: root.activeMode = modelData.key
                            ToolTip.visible: navMouse.containsMouse
                            ToolTip.text: modelData.label
                            MouseArea { id: navMouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.activeMode = modelData.key }
                        }
                    }
                    Item { Layout.fillHeight: true }
                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        width: 44; height: 44; radius: 10; color: settingsMouse.containsMouse ? Theme.surfaceHover : "transparent"
                        NavIcon { anchors.centerIn: parent; iconName: "settings"; iconColor: Theme.textMuted }
                        Accessible.name: qsTr("设置")
                        Accessible.role: Accessible.Button
                        activeFocusOnTab: true
                        Keys.onReturnPressed: settingsDialog.open()
                        Keys.onSpacePressed: settingsDialog.open()
                        ToolTip.visible: settingsMouse.containsMouse
                        ToolTip.text: qsTr("设置")
                        MouseArea { id: settingsMouse; anchors.fill: parent; hoverEnabled: true; onClicked: settingsDialog.open() }
                    }
                }
            }

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
                        : root.activeMode === "translate" ? 2
                        : root.activeMode === "highlight" ? 3
                        : root.activeMode === "audio" ? 4
                        : root.activeMode === "export" ? 5 : 6
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
                            leftResizeHandle.startWidth = root.toolPanelWidth
                        else
                            root.persistPanelLayout()
                    }
                    onTranslationChanged: root.toolPanelWidth = Math.max(
                        220, Math.min(520, leftResizeHandle.startWidth + translation.x))
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 260
                    color: "#08090b"
                    border.color: Theme.border

                    Rectangle {
                        id: previewSurface
                        anchors.centerIn: parent
                        width: Math.min(parent.width - 80, 820)
                        height: Math.min(parent.height - 92, width * 9 / 16)
                        color: "#020304"
                        border.color: Theme.borderStrong

                        MltPreviewItem {
                            id: preview
                            anchors.fill: parent
                            anchors.margins: 1
                            source: projectController.previewGraphPath
                            runtimeRoot: projectController.mltRuntimeRoot
                            hdrEnabled: projectController.colorMode === "hdr10_bt2020_pq"
                            onDroppedFramesChanged: projectController.reportPreviewDroppedFrames(droppedFrames)
                            onHdrActiveChanged: projectController.reportHdrPreviewActive(hdrActive)
                            onPositionChanged: if (root.previewRangeEnd >= 0
                                    && position >= root.previewRangeEnd) {
                                pause()
                                root.previewRangeEnd = -1
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: projectController.previewGraphPath.length === 0 || preview.errorString.length > 0
                            text: preview.errorString.length > 0
                                  ? qsTr("预览不可用：") + preview.errorString
                                  : qsTr("把素材添加到时间线开始创作")
                            color: Theme.textMuted
                            font.pixelSize: 14
                            wrapMode: Text.WordWrap
                            horizontalAlignment: Text.AlignHCenter
                            width: Math.min(parent.width - 40, 520)
                        }
                        Text {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.leftMargin: 28
                            anchors.rightMargin: 28
                            anchors.bottomMargin: 18
                            text: projectController.subtitleTextAtFrame(preview.position)
                            visible: text.length > 0
                            color: "white"
                            font.pixelSize: Math.max(18, previewSurface.height * 0.055)
                            font.weight: Font.DemiBold
                            style: Text.Outline
                            styleColor: "black"
                            wrapMode: Text.WordWrap
                            horizontalAlignment: Text.AlignHCenter
                        }
                        Rectangle {
                            anchors.top: parent.top
                            anchors.right: parent.right
                            anchors.margins: 8
                            width: hdrPreviewLabel.implicitWidth + 14
                            height: 24
                            radius: 12
                            color: "#7a4a18"
                            visible: projectController.colorMode === "hdr10_bt2020_pq"
                            Text { id: hdrPreviewLabel; anchors.centerIn: parent; text: preview.hdrActive ? qsTr("HDR 预览") : qsTr("HDR 项目 / SDR 预览"); color: "white"; font.pixelSize: 9 }
                        }
                    }
                    RowLayout {
                        anchors.bottom: parent.bottom
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottomMargin: 12
                        spacing: 10
                        AppButton { text: "◀"; Accessible.name: qsTr("上一帧"); enabled: preview.duration > 0; onClicked: preview.seek(Math.max(0, preview.position - 1)) }
                        AppButton { text: preview.playing ? "Ⅱ" : "▶"; Accessible.name: preview.playing ? qsTr("暂停") : qsTr("播放"); primary: true; enabled: preview.duration > 0; onClicked: preview.playing ? preview.pause() : preview.play() }
                        AppButton { text: "■"; Accessible.name: qsTr("停止并回到开头"); enabled: preview.duration > 0; onClicked: { preview.pause(); preview.seek(0) } }
                        Text { text: preview.position + " / " + preview.duration; color: Theme.textMuted; font.family: "Consolas"; font.pixelSize: 11 }
                        Text { visible: preview.droppedFrames > 0; text: qsTr("掉帧 ") + preview.droppedFrames; color: Theme.warning; font.pixelSize: 10 }
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
                                timelineResizeHandle.startHeight = root.timelinePanelHeight
                            else
                                root.persistPanelLayout()
                        }
                        onTranslationChanged: root.timelinePanelHeight = Math.max(
                            210, Math.min(640, timelineResizeHandle.startHeight - translation.y))
                    }
                }

                TimelineView {
                    id: timeline
                    objectName: "timelinePanel"
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.timelinePanelHeight
                    Layout.minimumHeight: 210
                    playheadFrame: preview.position
                    onSeekRequested: function(frame) { preview.seek(frame) }
                }
            }

            Rectangle {
                id: inspectorResizeHandle
                Layout.preferredWidth: root.width < 1320 ? 0 : 6
                Layout.fillHeight: true
                visible: root.width >= 1320
                color: inspectorDrag.active ? Theme.accent : Theme.border
                property real startWidth: 0
                DragHandler {
                    id: inspectorDrag
                    target: null
                    xAxis.enabled: true
                    yAxis.enabled: false
                    onActiveChanged: {
                        if (active)
                            inspectorResizeHandle.startWidth = root.inspectorPanelWidth
                        else
                            root.persistPanelLayout()
                    }
                    onTranslationChanged: root.inspectorPanelWidth = Math.max(
                        250, Math.min(520, inspectorResizeHandle.startWidth - translation.x))
                }
            }

            Rectangle {
                id: inspectorContainer
                objectName: "inspectorContainer"
                Layout.preferredWidth: root.width < 1320 ? 0 : root.inspectorPanelWidth
                Layout.fillHeight: true
                visible: root.width >= 1320
                color: Theme.surface
                border.color: Theme.border
                InspectorPanel { anchors.fill: parent; anchors.margins: 14 }
            }
        }
    }

    Dialog {
        id: shortDialog
        anchors.centerIn: parent
        modal: true
        title: qsTr("新建短视频序列")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: projectController.createShortSequence(shortName.text)
        contentItem: TextField {
            id: shortName
            text: qsTr("短视频")
            color: Theme.text
            selectByMouse: true
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
        onAccepted: projectController.resolveProfileAdoption(true)
        onRejected: projectController.resolveProfileAdoption(false)
        contentItem: Text {
            width: 430
            color: Theme.text
            wrapMode: Text.WordWrap
            text: qsTr("主时间线中已经有图片或音频编辑。这个视频建议使用 %1。采用后会按实际时长重新换算现有编辑；选择“否”则保持当前项目配置。").arg(projectController.pendingProfileLabel)
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
            profileWidth.text = String(projectController.profileWidth)
            profileHeight.text = String(projectController.profileHeight)
            for (var index = 0; index < frameRate.model.length; ++index) {
                var item = frameRate.model[index]
                if (item.n === projectController.profileFpsNumerator
                        && item.d === projectController.profileFpsDenominator) {
                    frameRate.currentIndex = index
                    break
                }
            }
            colorProfile.currentIndex = projectController.colorMode === "hdr10_bt2020_pq" ? 1 : 0
            for (var channelIndex = 0; channelIndex < audioChannels.model.length; ++channelIndex) {
                if (audioChannels.model[channelIndex].value === projectController.profileAudioChannels) {
                    audioChannels.currentIndex = channelIndex
                    break
                }
            }
        }
        onAccepted: {
            var fps = frameRate.model[frameRate.currentIndex]
            var color = colorProfile.model[colorProfile.currentIndex]
            var channels = audioChannels.model[audioChannels.currentIndex]
            projectController.updateSequenceProfile(
                Number(profileWidth.text), Number(profileHeight.text),
                fps.n, fps.d, color.value, channels.value)
        }
        contentItem: ColumnLayout {
            spacing: 10
            Text { text: qsTr("画布比例"); color: Theme.textMuted; font.pixelSize: 10 }
            ComboBox {
                Layout.fillWidth: true
                textRole: "label"
                model: [
                    { label: "16:9 · 1920×1080", width: 1920, height: 1080 },
                    { label: "9:16 · 1080×1920", width: 1080, height: 1920 },
                    { label: "1:1 · 1080×1080", width: 1080, height: 1080 },
                    { label: "4:5 · 1080×1350", width: 1080, height: 1350 }
                ]
                onActivated: function(index) {
                    profileWidth.text = String(model[index].width)
                    profileHeight.text = String(model[index].height)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: profileWidth
                    Layout.fillWidth: true
                    validator: IntValidator { bottom: 16; top: 16384 }
                    placeholderText: qsTr("宽度")
                }
                Text { text: "×"; color: Theme.textMuted }
                TextField {
                    id: profileHeight
                    Layout.fillWidth: true
                    validator: IntValidator { bottom: 16; top: 16384 }
                    placeholderText: qsTr("高度")
                }
            }
            Text { text: qsTr("帧率"); color: Theme.textMuted; font.pixelSize: 10 }
            ComboBox {
                id: frameRate
                Layout.fillWidth: true
                textRole: "label"
                model: [
                    { label: "23.976 fps", n: 24000, d: 1001 },
                    { label: "24 fps", n: 24, d: 1 },
                    { label: "25 fps", n: 25, d: 1 },
                    { label: "29.97 fps", n: 30000, d: 1001 },
                    { label: "30 fps", n: 30, d: 1 },
                    { label: "50 fps", n: 50, d: 1 },
                    { label: "59.94 fps", n: 60000, d: 1001 },
                    { label: "60 fps", n: 60, d: 1 }
                ]
                currentIndex: 4
            }
            Text { text: qsTr("色彩与输出声道"); color: Theme.textMuted; font.pixelSize: 10 }
            RowLayout {
                Layout.fillWidth: true
                ComboBox {
                    id: colorProfile
                    Layout.fillWidth: true
                    textRole: "label"
                    model: [
                        { label: "SDR · BT.709", value: "sdr_bt709" },
                        { label: "HDR10 · BT.2020 · PQ", value: "hdr10_bt2020_pq" }
                    ]
                }
                ComboBox {
                    id: audioChannels
                    Layout.fillWidth: true
                    textRole: "label"
                    model: [
                        { label: qsTr("单声道"), value: 1 },
                        { label: qsTr("立体声"), value: 2 },
                        { label: "5.1", value: 6 }
                    ]
                    currentIndex: 1
                }
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("修改帧率会按实际时长重新换算片段、转场和字幕；主序列的代理会自动失效并按需重建。")
                color: Theme.textMuted
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
        }
    }

    TaskDrawer {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        visible: projectController.taskDrawerOpen
        z: 50
    }

    Connections {
        target: projectController
        function onPreviewGraphChanged() {
            preview.reload()
        }
        function onProfileConfirmationChanged() {
            if (projectController.profileConfirmationPending)
                profileDialog.open()
            else
                profileDialog.close()
        }
    }

    Shortcut { sequence: "Space"; onActivated: preview.playing ? preview.pause() : preview.play() }
    Shortcut { sequence: "J"; onActivated: { preview.playbackRate = -1.0; preview.play() } }
    Shortcut { sequence: "K"; onActivated: preview.pause() }
    Shortcut { sequence: "L"; onActivated: { preview.playbackRate = preview.playbackRate > 0 ? Math.min(4, preview.playbackRate * 2) : 1.0; preview.play() } }
    Shortcut { sequence: "S"; enabled: projectController.selectedClipId.length > 0; onActivated: projectController.splitClip(projectController.selectedClipId, preview.position) }
    Shortcut { sequence: "Delete"; enabled: projectController.selectedClipId.length > 0; onActivated: projectController.deleteClip(projectController.selectedClipId, false) }
    Shortcut { sequence: "Shift+Delete"; enabled: projectController.selectedClipId.length > 0; onActivated: projectController.deleteClip(projectController.selectedClipId, true) }
    Shortcut { sequence: "Ctrl+Z"; enabled: projectController.canUndo; onActivated: projectController.undo() }
    Shortcut { sequence: "Ctrl+Y"; enabled: projectController.canRedo; onActivated: projectController.redo() }
    Shortcut {
        sequence: "Ctrl+D"
        enabled: projectController.selectedClipId.length > 0
        onActivated: projectController.copyClip(
            projectController.selectedClipId, timeline.pixelsPerFrame, preview.position)
    }
    Shortcut { sequence: "Ctrl+S"; onActivated: projectController.saveProject() }
    Shortcut { sequence: "M"; onActivated: projectController.addTimelineMarker(preview.position) }
}
