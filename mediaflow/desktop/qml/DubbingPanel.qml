import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia
import "."
import "components"

ColumnLayout {
    id: root
    objectName: "dubbingPanel"
    property var sourceDocuments: []
    property var sourceReadiness: ({})
    property var targetDocuments: []
    property var sessionSummaries: []
    property var sessionData: ({})
    property string selectedSourceDocumentId: ""
    property string selectedTargetDocumentId: ""
    property string selectedSessionId: ""
    spacing: 9

    function indexOfId(values, field, value) {
        for (let index = 0; index < values.length; ++index) {
            if (String(values[index][field]) === String(value))
                return index;
        }
        return 0;
    }

    function containsId(values, field, value) {
        return values.some(function(item) {
            return String(item[field]) === String(value);
        });
    }

    function statusLabel(value) {
        const labels = {
            preparing: qsTr("准备中"),
            review: qsTr("待审校"),
            synthesizing: qsTr("合成中"),
            synthesized: qsTr("已生成母版"),
            committed: qsTr("已提交"),
            pending: qsTr("待生成"),
            generated: qsTr("已生成"),
            needs_review: qsTr("待复核"),
            failed: qsTr("失败")
        };
        return labels[value] || String(value || qsTr("未创建"));
    }

    function refresh() {
        sourceReadiness = mediaflow.dubbingController.sourceReadiness() || {};
        sourceDocuments = mediaflow.dubbingController.sourceDocuments() || [];
        if (!containsId(sourceDocuments, "documentId", selectedSourceDocumentId))
            selectedSourceDocumentId = sourceDocuments.length
                ? String(sourceDocuments[0].documentId) : "";
        refreshTargetDocuments();
        sessionSummaries = mediaflow.dubbingController.sessions() || [];
        if (!selectedSessionId && sessionSummaries.length)
            selectedSessionId = String(sessionSummaries[0].sessionId);
        if (selectedSessionId
                && !sessionSummaries.some(function(item) {
                    return String(item.sessionId) === selectedSessionId;
                }))
            selectedSessionId = sessionSummaries.length
                ? String(sessionSummaries[0].sessionId) : "";
        refreshSession();
    }

    function refreshTargetDocuments() {
        targetDocuments = [{
            documentId: "",
            label: qsTr("自动翻译并新建译文")
        }].concat(mediaflow.dubbingController.targetDocuments(
            selectedSourceDocumentId) || []);
        if (!containsId(targetDocuments, "documentId", selectedTargetDocumentId))
            selectedTargetDocumentId = "";
    }

    function refreshSession() {
        sessionData = selectedSessionId
            ? (mediaflow.dubbingController.session(selectedSessionId) || {}) : ({});
    }

    Component.onCompleted: refresh()

    Connections {
        target: mediaflow.dubbingController
        function onProjectStateChanged() { root.refresh(); }
        function onTasksChanged() { root.refresh(); }
        function onSettingsChanged() { root.refresh(); }
    }

    AudioOutput { id: reviewAudioOutput }
    MediaPlayer {
        id: reviewPlayer
        audioOutput: reviewAudioOutput
    }

    Panel {
        Layout.fillWidth: true
        implicitHeight: createLayout.implicitHeight + 20
        ColumnLayout {
            id: createLayout
            anchors.fill: parent
            anchors.margins: 10
            spacing: 7
            Text {
                text: qsTr("创建多人中文配音方案")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodyLarge
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("把音频放入主要对白轨。没有英文字幕时可以直接在这里识别；选中源字幕后，会自动翻译、区分说话人并抽取克隆参考。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeBodySmall
            }
            RowLayout {
                Layout.fillWidth: true
                visible: root.sourceDocuments.length === 0
                Text {
                    Layout.fillWidth: true
                    text: root.sourceReadiness.active
                        ? qsTr("正在识别对白…")
                        : (root.sourceReadiness.reason || qsTr("未找到源字幕"))
                    color: root.sourceReadiness.available
                        ? Theme.textMuted : Theme.warning
                    font.pixelSize: Theme.fontSizeBodySmall
                    wrapMode: Text.Wrap
                }
                AppTextField {
                    id: sourceLanguage
                    Layout.preferredWidth: 72
                    text: "en"
                    placeholderText: qsTr("源语言")
                    enabled: !root.sourceReadiness.active
                }
                AppButton {
                    objectName: "dubbingTranscribeSourceButton"
                    text: root.sourceReadiness.active
                        ? qsTr("正在识别对白…") : qsTr("识别英文对白")
                    enabled: root.sourceReadiness.available
                        && !root.sourceReadiness.active
                    onClicked: mediaflow.dubbingController.transcribeSource(sourceLanguage.text)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AppComboBox {
                    id: sourceDocumentSelect
                    Layout.fillWidth: true
                    model: root.sourceDocuments
                    enabled: root.sourceDocuments.length > 0
                    textRole: "label"
                    valueRole: "documentId"
                    currentIndex: root.indexOfId(
                        root.sourceDocuments,
                        "documentId",
                        root.selectedSourceDocumentId)
                    onActivated: {
                        root.selectedSourceDocumentId = String(currentValue || "");
                        root.selectedTargetDocumentId = "";
                        root.refreshTargetDocuments();
                    }
                }
                AppTextField {
                    id: targetLanguage
                    Layout.preferredWidth: 72
                    text: "zh_CN"
                    placeholderText: qsTr("目标语言")
                }
            }
            AppComboBox {
                id: targetDocumentSelect
                Layout.fillWidth: true
                model: root.targetDocuments
                textRole: "label"
                valueRole: "documentId"
                currentIndex: root.indexOfId(
                    root.targetDocuments,
                    "documentId",
                    root.selectedTargetDocumentId)
                onActivated: root.selectedTargetDocumentId = String(currentValue || "")
            }
            RowLayout {
                Layout.fillWidth: true
                Text { text: qsTr("人数范围"); color: Theme.textMuted }
                AppSpinBox { id: minimumSpeakers; from: 0; to: 32; value: 0; editable: true }
                Text { text: "—"; color: Theme.textMuted }
                AppSpinBox { id: maximumSpeakers; from: 0; to: 32; value: 0; editable: true }
                Text { text: qsTr("0 为自动"); color: Theme.textSubtle; font.pixelSize: Theme.fontSizeCaption }
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("创建方案")
                    enabled: root.selectedSourceDocumentId.length > 0
                    onClicked: mediaflow.dubbingController.prepare(
                        root.selectedSourceDocumentId,
                        targetLanguage.text,
                        root.selectedTargetDocumentId,
                        minimumSpeakers.value,
                        maximumSpeakers.value)
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        AppComboBox {
            id: sessionSelect
            Layout.fillWidth: true
            model: root.sessionSummaries
            textRole: "label"
            valueRole: "sessionId"
            currentIndex: root.indexOfId(
                root.sessionSummaries, "sessionId", root.selectedSessionId)
            onActivated: {
                root.selectedSessionId = String(currentValue || "");
                root.refreshSession();
            }
        }
        StatusBadge {
            text: root.statusLabel(root.sessionData.status)
            tone: root.sessionData.status === "committed" ? "success"
                : root.sessionData.needsReviewCount > 0 ? "warning" : "neutral"
        }
    }

    AppTabBar {
        id: reviewTabs
        Layout.fillWidth: true
        AppTabButton { text: qsTr("说话人与参考音频") }
        AppTabButton { text: qsTr("逐句审校") }
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: reviewTabs.currentIndex

        AppScrollView {
            clip: true
            ColumnLayout {
                width: parent.width
                spacing: 8
                Repeater {
                    model: root.sessionData.speakers || []
                    delegate: Panel {
                        id: speakerCard
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: speakerLayout.implicitHeight + 20
                        ColumnLayout {
                            id: speakerLayout
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 7
                            RowLayout {
                                Layout.fillWidth: true
                                AppTextField {
                                    id: speakerName
                                    Layout.fillWidth: true
                                    text: String(speakerCard.modelData.displayName)
                                }
                                AppComboBox {
                                    id: speakerReview
                                    Layout.preferredWidth: 128
                                    textRole: "text"; valueRole: "value"
                                    model: [
                                        {text: qsTr("待确认"), value: "needs_review"},
                                        {text: qsTr("已确认"), value: "accepted"},
                                        {text: qsTr("自动"), value: "automatic"}
                                    ]
                                    currentIndex: root.indexOfId(
                                        model, "value", speakerCard.modelData.reviewStatus)
                                }
                                AppButton {
                                    text: qsTr("保存说话人")
                                    onClicked: mediaflow.dubbingController.updateSpeaker(
                                        root.selectedSessionId,
                                        String(speakerCard.modelData.speakerId),
                                        Number(root.sessionData.revision),
                                        speakerName.text,
                                        String(speakerReview.currentValue),
                                        String(primaryReference.currentValue || ""))
                                }
                            }
                            AppComboBox {
                                id: primaryReference
                                Layout.fillWidth: true
                                model: speakerCard.modelData.references || []
                                textRole: "text"
                                valueRole: "referenceId"
                                displayText: currentIndex >= 0
                                    ? qsTr("主参考 · %1 秒 · %2")
                                        .arg(Number(currentValue
                                            ? model[currentIndex].durationSeconds : 0).toFixed(1))
                                        .arg(model[currentIndex]
                                            ? model[currentIndex].text : "")
                                    : qsTr("选择主参考")
                                currentIndex: root.indexOfId(
                                    speakerCard.modelData.references || [],
                                    "referenceId",
                                    speakerCard.modelData.primaryReferenceId)
                            }
                            Repeater {
                                model: speakerCard.modelData.references || []
                                delegate: ColumnLayout {
                                    id: referenceRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Text {
                                            text: qsTr("%1 秒").arg(
                                                Number(referenceRow.modelData.durationSeconds).toFixed(1))
                                            color: Theme.textMuted
                                        }
                                        AppButton {
                                            text: qsTr("试听")
                                            onClicked: {
                                                reviewPlayer.source = referenceRow.modelData.audioUrl;
                                                reviewPlayer.play();
                                            }
                                        }
                                        AppTextField {
                                            id: referenceLanguage
                                            Layout.preferredWidth: 64
                                            text: String(referenceRow.modelData.language)
                                        }
                                        Item { Layout.fillWidth: true }
                                        AppButton {
                                            text: qsTr("保存参考原文")
                                            onClicked: mediaflow.dubbingController.updateReference(
                                                root.selectedSessionId,
                                                String(speakerCard.modelData.speakerId),
                                                String(referenceRow.modelData.referenceId),
                                                Number(root.sessionData.revision),
                                                referenceText.text,
                                                referenceLanguage.text)
                                        }
                                    }
                                    AppTextField {
                                        id: referenceText
                                        Layout.fillWidth: true
                                        text: String(referenceRow.modelData.text)
                                        placeholderText: qsTr("参考音频中实际说出的原文")
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        ColumnLayout {
            spacing: 7
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: qsTr("需要复核：%1 句").arg(
                        Number(root.sessionData.needsReviewCount || 0))
                    color: root.sessionData.needsReviewCount > 0
                        ? Theme.warning : Theme.textMuted
                }
                AppButton {
                    text: qsTr("合成全部")
                    enabled: root.selectedSessionId.length > 0
                    onClicked: mediaflow.dubbingController.synthesize(
                        root.selectedSessionId, [], false)
                }
                AppButton {
                    text: qsTr("试听母版")
                    enabled: Boolean(root.sessionData.masterAudioUrl)
                    onClicked: {
                        reviewPlayer.source = root.sessionData.masterAudioUrl;
                        reviewPlayer.play();
                    }
                }
            }
            ListView {
                id: utteranceList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 7
                model: root.sessionData.utterances || []
                delegate: Panel {
                    id: utteranceCard
                    required property var modelData
                    width: utteranceList.width
                    height: utteranceLayout.implicitHeight + 20
                    ColumnLayout {
                        id: utteranceLayout
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            AppComboBox {
                                id: utteranceSpeaker
                                Layout.preferredWidth: 150
                                model: root.sessionData.speakers || []
                                textRole: "displayName"
                                valueRole: "speakerId"
                                currentIndex: root.indexOfId(
                                    root.sessionData.speakers || [],
                                    "speakerId", utteranceCard.modelData.speakerId)
                            }
                            StatusBadge {
                                text: root.statusLabel(utteranceCard.modelData.status)
                                tone: utteranceCard.modelData.status === "generated"
                                    ? "success" : utteranceCard.modelData.issues.length
                                    ? "warning" : "neutral"
                            }
                            Text {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignRight
                                text: Number(utteranceCard.modelData.speedFactor || 1).toFixed(2) + "×"
                                color: Theme.textMuted
                            }
                            AppButton {
                                text: qsTr("定位")
                                onClicked: mediaflow.dubbingController.previewRange(
                                    Number(utteranceCard.modelData.startFrame),
                                    Number(utteranceCard.modelData.endFrame))
                            }
                            AppButton {
                                text: qsTr("试听")
                                enabled: Boolean(utteranceCard.modelData.audioUrl)
                                onClicked: {
                                    reviewPlayer.source = utteranceCard.modelData.audioUrl;
                                    reviewPlayer.play();
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            text: String(utteranceCard.modelData.sourceText)
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeBodySmall
                        }
                        AppTextField {
                            id: targetText
                            Layout.fillWidth: true
                            text: String(utteranceCard.modelData.targetText)
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: utteranceCard.modelData.issues.length > 0
                            wrapMode: Text.Wrap
                            text: utteranceCard.modelData.issues.join("；")
                            color: Theme.warning
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Item { Layout.fillWidth: true }
                            AppButton {
                                text: qsTr("保存并确认")
                                onClicked: mediaflow.dubbingController.updateUtterance(
                                    root.selectedSessionId,
                                    String(utteranceCard.modelData.utteranceId),
                                    Number(root.sessionData.revision),
                                    targetText.text,
                                    String(utteranceSpeaker.currentValue),
                                    "accepted")
                            }
                            AppButton {
                                text: qsTr("重做本句")
                                onClicked: mediaflow.dubbingController.synthesize(
                                    root.selectedSessionId,
                                    [String(utteranceCard.modelData.utteranceId)],
                                    true)
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AppTextField {
                    id: dubbingTrackName
                    Layout.fillWidth: true
                    text: qsTr("中文配音")
                }
                AppCheckBox {
                    id: muteSourceDialogue
                    text: qsTr("静音原对白轨")
                    checked: true
                }
                AppButton {
                    text: root.sessionData.status === "committed"
                        ? qsTr("已提交")
                        : root.sessionData.hasCommittedTrack
                        ? qsTr("更新配音轨") : qsTr("提交到时间线")
                    enabled: root.sessionData.status === "synthesized"
                    onClicked: mediaflow.dubbingController.commit(
                        root.selectedSessionId,
                        dubbingTrackName.text,
                        muteSourceDialogue.checked)
                }
            }
        }
    }
}
