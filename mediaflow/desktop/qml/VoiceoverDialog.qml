import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "voiceoverDialog"
    property int playheadFrame: 0
    property string selectedCueId: ""
    property string takeImportCueId: ""
    property int countdown: 0
    readonly property var cueRows: mediaflow.timelineVoiceoverController.cues
    readonly property var audioInputRows: mediaflow.timelineVoiceoverController.audioInputs
    readonly property var selectedCue: cueById(selectedCueId)
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(940, parent ? parent.width - 48 : 940)
    height: Math.min(760, parent ? parent.height - 48 : 760)
    modal: true
    title: qsTr("旁白录制与 ADR")
    standardButtons: Dialog.Close

    function cueById(cueId) {
        for (let index = 0; index < cueRows.length; ++index) {
            if (String(cueRows[index].cueId) === String(cueId))
                return cueRows[index];
        }
        return null;
    }

    function statusLabel(status) {
        if (status === "planned") return qsTr("待录制");
        if (status === "recording") return qsTr("录制中");
        if (status === "review") return qsTr("待审听");
        if (status === "approved") return qsTr("已通过");
        return String(status);
    }

    function selectCue(cueId) {
        selectedCueId = String(cueId);
        const cue = cueById(selectedCueId);
        if (!cue)
            return;
        editStart.value = Number(cue.startFrame);
        editEnd.value = Number(cue.endFrame);
        editText.text = String(cue.text);
        editSpeaker.text = String(cue.speaker);
        editNotes.text = String(cue.notes);
        for (let index = 0; index < statusBox.model.length; ++index) {
            if (statusBox.model[index].value === cue.status) {
                statusBox.currentIndex = index;
                break;
            }
        }
    }

    function ensureSelection() {
        if (selectedCueId.length && cueById(selectedCueId)) {
            selectCue(selectedCueId);
            return;
        }
        selectedCueId = cueRows.length ? String(cueRows[0].cueId) : "";
        if (selectedCueId.length)
            selectCue(selectedCueId);
    }

    function beginCountdown() {
        if (!selectedCue || !audioInputRows.length
                || mediaflow.timelineVoiceoverController.calibrating)
            return;
        countdown = 3;
        countdownTimer.start();
    }

    function selectedAudioInput() {
        const rows = audioInputRows;
        if (!rows.length)
            return null;
        return rows[Math.max(0, Math.min(audioInput.currentIndex, rows.length - 1))];
    }

    onOpened: {
        mediaflow.timelineVoiceoverController.refresh();
        createStart.value = Math.max(0, root.playheadFrame);
        createEnd.value = Math.min(
            mediaflow.workspaceViewController.timelineDurationFrames,
            createStart.value + Math.max(1, mediaflow.workspaceViewController.profileFpsNumerator
                / Math.max(1, mediaflow.workspaceViewController.profileFpsDenominator) * 5));
        ensureSelection();
    }
    onClosed: {
        countdownTimer.stop();
        countdown = 0;
        if (mediaflow.timelineVoiceoverController.recording)
            mediaflow.timelineVoiceoverController.stopRecording();
        if (mediaflow.timelineVoiceoverController.calibrating)
            mediaflow.timelineVoiceoverController.cancelLatencyCalibration();
    }

    Timer {
        id: countdownTimer
        interval: 1000
        repeat: true
        onTriggered: {
            root.countdown -= 1;
            if (root.countdown <= 0) {
                stop();
                if (root.selectedCue) {
                    const devices = root.audioInputRows;
                    const deviceId = devices.length
                        ? String(devices[Math.max(0, audioInput.currentIndex)].deviceId) : "";
                    mediaflow.timelineVoiceoverController.startRecording(
                        String(root.selectedCue.cueId), deviceId);
                }
            }
        }
    }

    FileDialog {
        id: takeDialog
        title: qsTr("导入旁白或 ADR 音频")
        nameFilters: [qsTr("音频文件 (*.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus *.wma)")]
        onAccepted: mediaflow.timelineVoiceoverController.importTake(
            root.takeImportCueId, selectedFile.toString())
    }

    Connections {
        target: mediaflow.timelineVoiceoverController
        function onProjectStateChanged() {
            Qt.callLater(root.ensureSelection);
        }
        function onRecordingCompleted(cueId) {
            root.selectCue(cueId);
        }
    }

    contentItem: RowLayout {
        spacing: 12
        ColumnLayout {
            Layout.preferredWidth: 320
            Layout.fillHeight: true
            Text {
                Layout.fillWidth: true
                text: qsTr("ADR 提示")
                color: Theme.text
                font.weight: Font.DemiBold
            }
            ListView {
                id: cueList
                objectName: "voiceoverCueList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: root.cueRows
                ScrollBar.vertical: AppScrollBar {}
                delegate: Panel {
                    required property var modelData
                    width: cueList.width
                    implicitHeight: cueColumn.implicitHeight + 16
                    border.color: root.selectedCueId === String(modelData.cueId)
                        ? Theme.accent : Theme.borderSubtle
                    TapHandler { onTapped: root.selectCue(modelData.cueId) }
                    ColumnLayout {
                        id: cueColumn
                        anchors.fill: parent
                        anchors.margins: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.speaker) + " · "
                                    + root.statusLabel(modelData.status)
                                color: Theme.text
                                font.weight: Font.Medium
                                elide: Text.ElideRight
                            }
                            Text {
                                text: Number(modelData.startFrame) + "–"
                                    + Number(modelData.endFrame)
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: String(modelData.text)
                            color: Theme.textMuted
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            text: qsTr("%1 个 take").arg(modelData.takes.length)
                            color: Theme.textSubtle
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
            Panel {
                Layout.fillWidth: true
                implicitHeight: createColumn.implicitHeight + 16
                ColumnLayout {
                    id: createColumn
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6
                    Text { text: qsTr("新建提示"); color: Theme.text; font.weight: Font.DemiBold }
                    RowLayout {
                        Text { text: qsTr("起止帧"); color: Theme.textMuted }
                        AppSpinBox {
                            id: createStart
                            Layout.fillWidth: true
                            from: 0
                            to: Math.max(0, mediaflow.workspaceViewController.timelineDurationFrames - 1)
                            editable: true
                        }
                        AppSpinBox {
                            id: createEnd
                            Layout.fillWidth: true
                            from: 1
                            to: Math.max(1, mediaflow.workspaceViewController.timelineDurationFrames)
                            editable: true
                        }
                    }
                    AppTextField {
                        id: createSpeaker
                        Layout.fillWidth: true
                        text: qsTr("旁白")
                        placeholderText: qsTr("说话人")
                    }
                    AppTextField {
                        id: createText
                        objectName: "voiceoverNewCueText"
                        Layout.fillWidth: true
                        placeholderText: qsTr("台词或 ADR 提示")
                    }
                    AppButton {
                        objectName: "createVoiceoverCueButton"
                        Layout.fillWidth: true
                        primary: true
                        text: qsTr("创建提示")
                        enabled: createText.text.trim().length > 0
                            && createEnd.value > createStart.value
                        onClicked: {
                            mediaflow.timelineVoiceoverController.createCue(
                                createStart.value, createEnd.value, createText.text,
                                createSpeaker.text, "");
                            createText.clear();
                        }
                    }
                }
            }
        }

        Rectangle { Layout.fillHeight: true; width: 1; color: Theme.divider }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width
                spacing: 10
                Text {
                    Layout.fillWidth: true
                    visible: !root.selectedCue
                    text: qsTr("选择或创建一个提示后即可录音、导入和管理 take。")
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
                Panel {
                    Layout.fillWidth: true
                    visible: Boolean(root.selectedCue)
                    implicitHeight: editColumn.implicitHeight + 16
                    ColumnLayout {
                        id: editColumn
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 7
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: qsTr("提示内容"); color: Theme.text; font.weight: Font.DemiBold }
                            Item { Layout.fillWidth: true }
                            AppComboBox {
                                id: statusBox
                                Layout.preferredWidth: 120
                                model: [
                                    {label: qsTr("待录制"), value: "planned"},
                                    {label: qsTr("待审听"), value: "review"},
                                    {label: qsTr("已通过"), value: "approved"}
                                ]
                                textRole: "label"
                            }
                        }
                        RowLayout {
                            Text { text: qsTr("起止帧"); color: Theme.textMuted }
                            AppSpinBox { id: editStart; Layout.fillWidth: true; from: 0; to: 100000000; editable: true }
                            AppSpinBox { id: editEnd; Layout.fillWidth: true; from: 1; to: 100000000; editable: true }
                        }
                        AppTextField { id: editSpeaker; Layout.fillWidth: true; placeholderText: qsTr("说话人") }
                        AppTextField { id: editText; Layout.fillWidth: true; placeholderText: qsTr("台词或 ADR 提示") }
                        AppTextField { id: editNotes; Layout.fillWidth: true; placeholderText: qsTr("表演、发音或同步备注") }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: qsTr("归档提示")
                                danger: true
                                enabled: root.selectedCue
                                    && mediaflow.timelineVoiceoverController.recordingCueId
                                        !== String(root.selectedCue.cueId)
                                onClicked: mediaflow.timelineVoiceoverController.archiveCue(
                                    root.selectedCue.cueId, root.selectedCue.revision)
                            }
                            Item { Layout.fillWidth: true }
                            AppButton {
                                objectName: "saveVoiceoverCueButton"
                                primary: true
                                text: qsTr("保存提示")
                                enabled: root.selectedCue && editText.text.trim().length > 0
                                    && editEnd.value > editStart.value
                                onClicked: mediaflow.timelineVoiceoverController.updateCue(
                                    root.selectedCue.cueId, root.selectedCue.revision,
                                    editStart.value, editEnd.value, editText.text,
                                    editSpeaker.text, editNotes.text,
                                    statusBox.model[statusBox.currentIndex].value)
                            }
                        }
                    }
                }

                Panel {
                    Layout.fillWidth: true
                    visible: Boolean(root.selectedCue)
                    implicitHeight: recordingColumn.implicitHeight + 16
                    ColumnLayout {
                        id: recordingColumn
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 8
                        Text {
                            Layout.fillWidth: true
                            text: root.selectedCue ? String(root.selectedCue.text) : ""
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeTitle
                            wrapMode: Text.WordWrap
                        }
                        Text {
                            Layout.fillWidth: true
                            text: root.countdown > 0
                                ? qsTr("%1… 准备录音").arg(root.countdown)
                                : mediaflow.timelineVoiceoverController.recording
                                    ? qsTr("录制中 · %1 秒").arg(
                                        (mediaflow.timelineVoiceoverController.recordingDurationMs / 1000).toFixed(1))
                                    : qsTr("录音会在提示区间结束时自动停止，也可以手动停止。")
                            color: root.countdown > 0 || mediaflow.timelineVoiceoverController.recording
                                ? Theme.accent : Theme.textMuted
                            font.weight: root.countdown > 0 ? Font.Bold : Font.Normal
                            wrapMode: Text.WordWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppComboBox {
                                id: audioInput
                                Layout.fillWidth: true
                                model: root.audioInputRows
                                textRole: "name"
                                onCurrentIndexChanged: {
                                    const input = root.selectedAudioInput();
                                    if (input)
                                        manualLatencyMs.value = Math.round(Number(input.latencyMs));
                                }
                            }
                            AppButton {
                                objectName: "recordVoiceoverTakeButton"
                                primary: true
                                text: mediaflow.timelineVoiceoverController.recording
                                    ? qsTr("停止录音") : root.countdown > 0
                                        ? qsTr("取消倒计时") : qsTr("3 秒后录音")
                                enabled: Boolean(root.selectedCue)
                                    && !mediaflow.timelineVoiceoverController.calibrating
                                    && (mediaflow.timelineVoiceoverController.recording
                                        || root.countdown > 0
                                        || root.audioInputRows.length > 0)
                                onClicked: {
                                    if (mediaflow.timelineVoiceoverController.recording) {
                                        mediaflow.timelineVoiceoverController.stopRecording();
                                    } else if (root.countdown > 0) {
                                        countdownTimer.stop();
                                        root.countdown = 0;
                                    } else {
                                        root.beginCountdown();
                                    }
                                }
                            }
                            AppButton {
                                text: qsTr("导入音频")
                                enabled: root.selectedCue
                                    && !mediaflow.timelineVoiceoverController.recording
                                onClicked: {
                                    root.takeImportCueId = String(root.selectedCue.cueId);
                                    takeDialog.open();
                                }
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            height: 1
                            color: Theme.divider
                        }
                        Text {
                            Layout.fillWidth: true
                            text: {
                                const input = root.selectedAudioInput();
                                if (!input)
                                    return qsTr("没有可校准的录音设备");
                                if (mediaflow.timelineVoiceoverController.calibrating)
                                    return qsTr("正在播放并录制测试声，请保持环境安静……");
                                if (!input.calibrated)
                                    return qsTr("该设备尚未校准；未校准的 take 不会自动补偿延迟。");
                                const source = input.method === "acoustic_roundtrip"
                                    ? qsTr("声学往返测量") : qsTr("手动值");
                                return qsTr("设备延迟：%1 ms · %2 · 可信度 %3%")
                                    .arg(Number(input.latencyMs).toFixed(1))
                                    .arg(source)
                                    .arg(Math.round(Number(input.confidence) * 100));
                            }
                            color: mediaflow.timelineVoiceoverController.calibrating
                                ? Theme.accent : Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                objectName: "calibrateVoiceoverLatencyButton"
                                text: mediaflow.timelineVoiceoverController.calibrating
                                    ? qsTr("取消校准") : qsTr("自动测量")
                                enabled: root.selectedAudioInput()
                                    && !mediaflow.timelineVoiceoverController.recording
                                onClicked: {
                                    if (mediaflow.timelineVoiceoverController.calibrating) {
                                        mediaflow.timelineVoiceoverController.cancelLatencyCalibration();
                                        return;
                                    }
                                    const input = root.selectedAudioInput();
                                    mediaflow.timelineVoiceoverController.startLatencyCalibration(
                                        String(input.deviceId), String(input.name));
                                }
                            }
                            Text { text: qsTr("手动"); color: Theme.textMuted }
                            AppSpinBox {
                                id: manualLatencyMs
                                Layout.preferredWidth: 100
                                from: 0
                                to: 1000
                                editable: true
                            }
                            Text { text: "ms"; color: Theme.textMuted }
                            AppButton {
                                text: qsTr("应用")
                                enabled: root.selectedAudioInput()
                                    && !mediaflow.timelineVoiceoverController.recording
                                    && !mediaflow.timelineVoiceoverController.calibrating
                                onClicked: {
                                    const input = root.selectedAudioInput();
                                    mediaflow.timelineVoiceoverController.setLatencyCalibration(
                                        String(input.deviceId), String(input.name),
                                        Number(manualLatencyMs.value));
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("自动测量会从默认扬声器播放短测试声并由所选输入录回；耳机或隔音链路请改用手动值。校准结果只写入之后录制的 take。")
                            color: Theme.textSubtle
                            font.pixelSize: Theme.fontSizeCaption
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                Text {
                    visible: Boolean(root.selectedCue)
                    text: qsTr("Takes")
                    color: Theme.text
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: root.selectedCue ? root.selectedCue.takes : []
                    delegate: Panel {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: takeColumn.implicitHeight + 16
                        border.color: modelData.selected ? Theme.accent : Theme.borderSubtle
                        ColumnLayout {
                            id: takeColumn
                            anchors.fill: parent
                            anchors.margins: 8
                            RowLayout {
                                Layout.fillWidth: true
                                AppTextField {
                                    id: takeName
                                    Layout.fillWidth: true
                                    text: String(modelData.name)
                                }
                                Text {
                                    text: Number(modelData.latencySamples) > 0
                                        ? qsTr("%1 帧 · 补偿 %2 ms")
                                            .arg(modelData.durationFrames)
                                            .arg(Number(modelData.latencyMs).toFixed(1))
                                        : qsTr("%1 帧").arg(modelData.durationFrames)
                                    color: Theme.textMuted
                                }
                                AppSpinBox {
                                    id: takeRating
                                    Layout.preferredWidth: 100
                                    from: 0
                                    to: 5
                                    value: Number(modelData.rating)
                                    editable: true
                                }
                            }
                            AppTextField {
                                id: takeNotes
                                Layout.fillWidth: true
                                text: String(modelData.notes)
                                placeholderText: qsTr("take 备注")
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppButton {
                                    text: modelData.selected ? qsTr("已选") : qsTr("选用")
                                    enabled: !modelData.selected
                                    onClicked: mediaflow.timelineVoiceoverController.selectTake(
                                        root.selectedCue.cueId, modelData.takeId,
                                        root.selectedCue.revision)
                                }
                                AppButton {
                                    text: qsTr("保存评分")
                                    onClicked: mediaflow.timelineVoiceoverController.updateTake(
                                        modelData.takeId, takeName.text,
                                        takeNotes.text, takeRating.value)
                                }
                                AppButton {
                                    text: qsTr("归档")
                                    danger: true
                                    onClicked: mediaflow.timelineVoiceoverController.archiveTake(
                                        modelData.takeId)
                                }
                                Item { Layout.fillWidth: true }
                                AppButton {
                                    primary: modelData.selected
                                    text: qsTr("放入时间线")
                                    enabled: modelData.selected
                                    onClicked: mediaflow.timelineVoiceoverController.placeTake(
                                        root.selectedCue.cueId, root.selectedCue.revision)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
