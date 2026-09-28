import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "multicamDialog"
    property int playheadFrame: 0
    anchors.centerIn: parent
    width: 620
    height: Math.min(680, parent ? parent.height - 48 : 680)
    modal: true
    title: qsTr("多机位同步与切换")
    standardButtons: Dialog.Close

    ListModel { id: angleModel }
    property real syncConfidence: -1

    Connections {
        target: mediaflow.timelineMulticamController
        function onSyncAnalysisChanged() {
            const analysis = mediaflow.timelineMulticamController.syncAnalysis;
            if (!analysis || !analysis.angles) return;
            root.syncConfidence = Number(analysis.confidence || 0);
            for (let index = 0; index < angleModel.count; ++index) {
                const assetId = String(angleModel.get(index).assetId);
                for (const angle of analysis.angles) {
                    if (String(angle.asset_id) === assetId) {
                        angleModel.setProperty(index, "syncFrame", Number(angle.sync_frame));
                        angleModel.setProperty(index, "angleId", String(angle.id));
                    }
                }
            }
            let duration = 100000000;
            for (let index = 0; index < angleModel.count; ++index) {
                const item = angleModel.get(index);
                duration = Math.min(duration,
                    Number(item.durationFrames) - Number(item.syncFrame) + syncOffset.value);
            }
            programDuration.value = Math.max(1, duration);
        }
    }

    function refreshInputs() {
        angleModel.clear();
        const assets = mediaflow.timelineMulticamController.selectedVideoAssets;
        for (let index = 0; index < assets.length; ++index) {
            angleModel.append({
                assetId: String(assets[index].assetId),
                name: String(assets[index].name),
                durationFrames: Number(assets[index].durationFrames),
                syncFrame: 0,
                angleId: ""
            });
        }
        if (assets.length >= 2) {
            let shortest = Number(assets[0].durationFrames);
            for (let item = 1; item < assets.length; ++item)
                shortest = Math.min(shortest, Number(assets[item].durationFrames));
            programDuration.value = Math.max(1, shortest);
        }
    }

    onOpened: refreshInputs()

    contentItem: ScrollView {
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: qsTr("先在素材面板多选两个或更多已探测时长的视频。把同一声画事件在各素材中的帧号填为同步帧；节目同步点表示该事件在节目内出现的位置。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeBodySmall
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                AppComboBox {
                    id: syncMethod
                    Layout.fillWidth: true
                    textRole: "label"
                    valueRole: "value"
                    model: [
                        {label: qsTr("手动同步帧"), value: "manual"},
                        {label: qsTr("素材时码"), value: "timecode"},
                        {label: qsTr("音频波形"), value: "waveform"}
                    ]
                    onActivated: root.syncConfidence = currentValue === "manual" ? -1 : root.syncConfidence
                }
                AppButton {
                    objectName: "analyzeMulticamSyncButton"
                    text: mediaflow.timelineMulticamController.syncRunning
                        ? qsTr("分析中…") : qsTr("分析同步")
                    enabled: angleModel.count >= 2
                        && String(syncMethod.currentValue) !== "manual"
                        && !mediaflow.timelineMulticamController.syncRunning
                    onClicked: mediaflow.timelineMulticamController.analyzeSync(
                        String(syncMethod.currentValue))
                }
                Text {
                    visible: root.syncConfidence >= 0
                    text: qsTr("置信度 %1%").arg((root.syncConfidence * 100).toFixed(0))
                    color: root.syncConfidence >= 0.6 ? Theme.textSubtle : Theme.warning
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
            AppTextField {
                id: groupName
                objectName: "multicamGroupName"
                Layout.fillWidth: true
                text: qsTr("多机位节目")
                placeholderText: qsTr("节目名称")
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 4
                Text { text: qsTr("节目起点"); color: Theme.textMuted }
                AppSpinBox { id: programStart; from: 0; to: 100000000; value: root.playheadFrame; editable: true }
                Text { text: qsTr("节目时长"); color: Theme.textMuted }
                AppSpinBox { id: programDuration; objectName: "multicamProgramDuration"; from: 1; to: 100000000; value: 1; editable: true }
                Text { text: qsTr("节目同步点"); color: Theme.textMuted }
                AppSpinBox { id: syncOffset; objectName: "multicamSyncOffset"; from: 0; to: Math.max(0, programDuration.value - 1); value: 0; editable: true }
            }
            Repeater {
                model: angleModel
                delegate: Panel {
                    required property int index
                    required property string assetId
                    required property string name
                    required property int durationFrames
                    required property int syncFrame
                    Layout.fillWidth: true
                    implicitHeight: angleRow.implicitHeight + 16
                    RowLayout {
                        id: angleRow
                        anchors.fill: parent
                        anchors.margins: 8
                        Text { Layout.fillWidth: true; text: name; color: Theme.text; elide: Text.ElideMiddle }
                        Text { text: qsTr("总长 %1 帧").arg(durationFrames); color: Theme.textMuted }
                        Text { text: qsTr("同步帧"); color: Theme.textMuted }
                        AppSpinBox {
                            objectName: "multicamAngleSyncFrame"
                            from: 0
                            to: Math.max(0, durationFrames - 1)
                            value: syncFrame
                            editable: true
                            onValueModified: angleModel.setProperty(index, "syncFrame", value)
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AppComboBox {
                    id: audioStrategy
                    Layout.fillWidth: true
                    textRole: "label"
                    valueRole: "value"
                    model: [
                        {label: qsTr("声音跟随当前机位"), value: "follow_video"},
                        {label: qsTr("固定主音频机位"), value: "master_angle"}
                    ]
                }
                AppComboBox {
                    id: masterAudio
                    Layout.fillWidth: true
                    visible: String(audioStrategy.currentValue) === "master_angle"
                    textRole: "name"
                    valueRole: "assetId"
                    model: angleModel
                }
            }
            Text {
                Layout.fillWidth: true
                visible: angleModel.count < 2
                text: qsTr("请先在素材面板多选至少两个视频素材。")
                color: Theme.warning
                wrapMode: Text.WordWrap
            }
            AppButton {
                objectName: "createMulticamGroupButton"
                Layout.fillWidth: true
                primary: true
                text: qsTr("同步并创建节目轨")
                enabled: angleModel.count >= 2 && groupName.text.trim().length > 0
                onClicked: {
                    const values = [];
                    for (let index = 0; index < angleModel.count; ++index)
                        values.push(angleModel.get(index));
                    mediaflow.timelineMulticamController.createGroupAdvanced(
                        groupName.text, values, programStart.value,
                        programDuration.value, syncOffset.value,
                        String(syncMethod.currentValue || "manual"),
                        root.syncConfidence,
                        String(audioStrategy.currentValue || "follow_video"),
                        String(masterAudio.currentValue || ""));
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.divider }
            Text { text: qsTr("已有节目 · 在当前播放头切换角度"); color: Theme.text; font.weight: Font.DemiBold }
            Repeater {
                model: mediaflow.timelineMulticamController.groups
                delegate: Panel {
                    required property string groupId
                    required property string name
                    required property int timelineStart
                    required property int duration
                    required property var angles
                    Layout.fillWidth: true
                    implicitHeight: groupColumn.implicitHeight + 16
                    ColumnLayout {
                        id: groupColumn
                        anchors.fill: parent
                        anchors.margins: 8
                        Text { text: name + qsTr(" · %1–%2 帧").arg(timelineStart).arg(timelineStart + duration); color: Theme.text }
                        RowLayout {
                            Repeater {
                                model: angles
                                delegate: AppButton {
                                    required property string angleId
                                    required property string name
                                    text: name
                                    enabled: root.playheadFrame >= timelineStart && root.playheadFrame < timelineStart + duration
                                    onClicked: mediaflow.timelineMulticamController.switchAngle(groupId, angleId, root.playheadFrame)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
