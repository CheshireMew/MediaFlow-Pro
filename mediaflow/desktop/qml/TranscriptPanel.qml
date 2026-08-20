import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppScrollView {
    id: transcriptScroll
    objectName: "transcriptPanel"
    clip: true
    contentWidth: availableWidth
    property var taskData: ({})
    property var resultData: ({})
    property var planData: ({})
    readonly property bool taskActive: taskData.status === "pending"
        || taskData.status === "running" || taskData.status === "paused"
    signal modeRequested(string mode)

    function indexOfValue(model, value) {
        for (var index = 0; index < model.length; ++index) {
            if (model[index].value === value)
                return index
        }
        return 0
    }

    function syncTranscriptionSettings() {
        const data = mediaflow.settingsController.settingsData
        asrModelSelect.currentIndex = indexOfValue(
            asrModelSelect.model, data.asrModel)
        asrDeviceSelect.currentIndex = indexOfValue(
            asrDeviceSelect.model, data.asrDevice)
        asrLanguageSelect.currentIndex = indexOfValue(
            asrLanguageSelect.model, data.asrLanguage)
        asrParallelSelect.currentIndex = indexOfValue(
            asrParallelSelect.model, Number(data.asrParallelChunks || 0))
    }

    function formatTimecode(frame) {
        const fps = Math.max(1, Math.round(
            mediaflow.workspaceViewController.profileFpsNumerator
                / Math.max(1, mediaflow.workspaceViewController.profileFpsDenominator)))
        const bounded = Math.max(0, Math.round(Number(frame || 0)))
        const frames = bounded % fps
        const totalSeconds = Math.floor(bounded / fps)
        const seconds = totalSeconds % 60
        const totalMinutes = Math.floor(totalSeconds / 60)
        const minutes = totalMinutes % 60
        const hours = Math.floor(totalMinutes / 60)
        function pad(value) { return value < 10 ? "0" + value : String(value) }
        return pad(hours) + ":" + pad(minutes) + ":" + pad(seconds)
            + ":" + pad(frames)
    }

    function formatDuration(seconds) {
        const value = Math.max(0, Math.round(Number(seconds || 0)))
        const hours = Math.floor(value / 3600)
        const minutes = Math.floor((value % 3600) / 60)
        const remaining = value % 60
        return hours > 0
            ? qsTr("%1 小时 %2 分钟").arg(hours).arg(minutes)
            : minutes > 0
                ? qsTr("%1 分 %2 秒").arg(minutes).arg(remaining)
                : qsTr("%1 秒").arg(remaining)
    }

    function refreshContext() {
        const sequenceId = String(mediaflow.workspaceViewController.activeSequenceId || "");
        taskData = mediaflow.taskController.latestTask("transcribe", sequenceId);
        resultData = mediaflow.subtitleTranscriptionController.sequenceTranscriptionSummary(sequenceId);
        planData = mediaflow.subtitleTranscriptionController.transcriptionPlanSummary;
    }

    Component.onCompleted: {
        syncTranscriptionSettings()
        refreshContext()
    }

    Connections {
        target: mediaflow.taskController
        function onTasksChanged() { transcriptScroll.refreshContext(); }
    }
    Connections {
        target: mediaflow.workspaceViewController
        function onProjectStateChanged() { transcriptScroll.refreshContext(); }
        function onHistoryChanged() { transcriptScroll.refreshContext(); }
    }
    Connections {
        target: mediaflow.settingsController
        function onSettingsChanged() {
            transcriptScroll.syncTranscriptionSettings()
            transcriptScroll.refreshContext()
        }
    }

    ColumnLayout {
        width: transcriptScroll.availableWidth
        spacing: 10

        Panel {
            objectName: "transcriptTimelinePanel"
            Layout.fillWidth: true
            implicitHeight: timelineContent.implicitHeight + 22

            ColumnLayout {
                id: timelineContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 11
                spacing: 9

                Text {
                    text: qsTr("当前时间轴")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("只读取标记为“对白”的音轨，并合并实际使用到的源音频区间；不会再转录未使用的完整素材。")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: transcriptScroll.planData.available
                        ? qsTr("时间轴范围：%1–%2").arg(
                            transcriptScroll.formatTimecode(
                                transcriptScroll.planData.timelineStartFrame)).arg(
                            transcriptScroll.formatTimecode(
                                transcriptScroll.planData.timelineEndFrame))
                        : (transcriptScroll.planData.error || qsTr("当前没有可转录范围"))
                    color: Theme.accentHover
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    visible: Boolean(transcriptScroll.planData.available)
                    text: qsTr("实际识别：%1 个素材 · %2 个源区间 · 约 %3").arg(
                        transcriptScroll.planData.sourceCount || 0).arg(
                        transcriptScroll.planData.regionCount || 0).arg(
                        transcriptScroll.formatDuration(
                            transcriptScroll.planData.recognitionSeconds))
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                Text {
                    text: qsTr("本次转录设置")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                }
                AppComboBox {
                    id: asrModelSelect
                    objectName: "transcriptionModelSelect"
                    Layout.fillWidth: true
                    textRole: "text"
                    valueRole: "value"
                    model: mediaflow.settingsController.asrModelOptions
                    enabled: !transcriptScroll.taskActive
                }
                Text {
                    objectName: "transcriptionModelDetail"
                    Layout.fillWidth: true
                    text: asrModelSelect.currentText || ""
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppComboBox {
                        id: asrDeviceSelect
                        objectName: "transcriptionDeviceSelect"
                        Layout.fillWidth: true
                        textRole: "text"
                        valueRole: "value"
                        model: [
                            {text: qsTr("设备：自动"), value: "auto"},
                            {text: qsTr("设备：CUDA"), value: "cuda"},
                            {text: qsTr("设备：CPU"), value: "cpu"}
                        ]
                        enabled: !transcriptScroll.taskActive
                    }
                    AppComboBox {
                        id: asrLanguageSelect
                        objectName: "transcriptionLanguageSelect"
                        Layout.fillWidth: true
                        textRole: "text"
                        valueRole: "value"
                        model: mediaflow.settingsController.asrLanguageOptions
                        enabled: !transcriptScroll.taskActive
                    }
                }
                AppComboBox {
                    id: asrParallelSelect
                    objectName: "transcriptionParallelSelect"
                    Layout.fillWidth: true
                    textRole: "text"
                    valueRole: "value"
                    model: mediaflow.settingsController.asrParallelOptions
                    enabled: !transcriptScroll.taskActive
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("并行分块会同时加载多份模型；自动模式会根据 CPU、内存和显存决定是否并行。")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: mediaflow.settingsController.settingsData.asrEngine === "faster_whisper_cli"
                        ? qsTr("引擎：Faster-Whisper XXL CLI")
                        : qsTr("引擎：内置 faster-whisper")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppButton {
                    objectName: "transcribeTimelineButton"
                    Layout.fillWidth: true
                    primary: true
                    text: transcriptScroll.taskActive
                        ? qsTr("正在转录…") : qsTr("转录当前时间轴")
                    enabled: mediaflow.subtitleTranscriptionController.canTranscribeCurrentSequence
                        && !transcriptScroll.taskActive
                        && Boolean(mediaflow.workspaceViewController.actionCapabilities.canStartTasks)
                    onClicked: mediaflow.subtitleTranscriptionController.transcribeCurrentSequence(
                        String(asrModelSelect.currentValue || ""),
                        String(asrDeviceSelect.currentValue || "auto"),
                        String(asrLanguageSelect.currentValue || "auto"),
                        Number(asrParallelSelect.currentValue || 0))
                }
                AppButton {
                    objectName: "copyTranscriptionCliRequestButton"
                    Layout.fillWidth: true
                    text: qsTr("复制当前转录为 CLI 请求")
                    enabled: mediaflow.subtitleTranscriptionController.canTranscribeCurrentSequence
                        && !transcriptScroll.taskActive
                    onClicked: mediaflow.automationController.copyCurrentTranscriptionRequest(
                        String(asrModelSelect.currentValue || ""),
                        String(asrDeviceSelect.currentValue || "auto"),
                        String(asrLanguageSelect.currentValue || "auto"),
                        Number(asrParallelSelect.currentValue || 0))
                }
                Text {
                    Layout.fillWidth: true
                    visible: !mediaflow.subtitleTranscriptionController.canTranscribeCurrentSequence
                    text: qsTr("请先在时间轴把一条音频轨设为“对白”，并确认当前范围内有对白素材。")
                    color: Theme.warning
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
            }
        }

        ContextTaskCard {
            objectName: "transcriptTaskPanel"
            Layout.fillWidth: true
            taskData: transcriptScroll.taskData
            fallbackTitle: qsTr("转录任务")
        }

        Panel {
            objectName: "transcriptResultPanel"
            Layout.fillWidth: true
            implicitHeight: resultContent.implicitHeight + 22
            visible: Boolean(transcriptScroll.resultData.documentId)

            ColumnLayout {
                id: resultContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 11
                spacing: 7

                Text {
                    text: qsTr("时间轴字幕已生成")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("%1 · %2 条字幕 · %3–%4 帧")
                        .arg(transcriptScroll.resultData.language || qsTr("未知语言"))
                        .arg(transcriptScroll.resultData.segmentCount || 0)
                        .arg(transcriptScroll.resultData.startFrame || 0)
                        .arg(transcriptScroll.resultData.endFrame || 0)
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("字幕已经放入当前序列；重新转录会更新这份字幕。")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                AppButton {
                    objectName: "transcriptOpenSubtitleButton"
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("进入字幕编辑")
                    onClicked: {
                        mediaflow.subtitleViewController.selectSubtitleDocument(
                            transcriptScroll.resultData.documentId);
                        transcriptScroll.modeRequested("subtitle");
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
