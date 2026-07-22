import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ScrollView {
    id: transcriptScroll
    objectName: "transcriptPanel"
    clip: true
    contentWidth: availableWidth
    property var taskData: ({})
    property var resultData: ({})
    readonly property bool taskActive: taskData.status === "pending"
        || taskData.status === "running" || taskData.status === "paused"
    signal modeRequested(string mode)

    function refreshContext() {
        const sequenceId = String(workspaceController.activeSequenceId || "");
        taskData = taskController.latestTask("transcribe", sequenceId);
        resultData = subtitleController.sequenceTranscriptionSummary(sequenceId);
    }

    Component.onCompleted: refreshContext()

    Connections {
        target: taskController
        function onTasksChanged() { transcriptScroll.refreshContext(); }
    }
    Connections {
        target: workspaceController
        function onProjectStateChanged() { transcriptScroll.refreshContext(); }
        function onHistoryChanged() { transcriptScroll.refreshContext(); }
    }

    ColumnLayout {
        width: transcriptScroll.availableWidth
        spacing: 10

        Text {
            text: qsTr("自动字幕")
            color: Theme.text
            font.pixelSize: Theme.fontSizeSection
            font.weight: Font.DemiBold
        }

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
                    text: qsTr("按时间轴顺序转录所有可听见的视频和音频，并自动生成对齐的字幕。")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: workspaceController.hasSequenceInOut
                        ? qsTr("范围：序列入出点 %1–%2 帧").arg(
                            workspaceController.sequenceInFrame).arg(
                            workspaceController.sequenceOutFrame)
                        : qsTr("范围：整个序列，共 %1 帧").arg(
                            workspaceController.timelineDurationFrames)
                    color: Theme.accentHover
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                AppButton {
                    objectName: "transcribeTimelineButton"
                    Layout.fillWidth: true
                    primary: true
                    text: transcriptScroll.taskActive
                        ? qsTr("正在转录…") : qsTr("转录当前时间轴")
                    enabled: subtitleController.canTranscribeCurrentSequence
                        && !transcriptScroll.taskActive
                        && !workspaceController.readOnly
                    onClicked: subtitleController.transcribeCurrentSequence()
                }
                Text {
                    Layout.fillWidth: true
                    visible: !subtitleController.canTranscribeCurrentSequence
                    text: qsTr("当前时间轴范围内没有可听见的视频或音频。")
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
                        subtitleController.selectSubtitleDocument(
                            transcriptScroll.resultData.documentId);
                        transcriptScroll.modeRequested("subtitle");
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
