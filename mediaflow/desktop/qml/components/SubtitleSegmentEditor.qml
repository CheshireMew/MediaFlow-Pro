import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Panel {
    id: segmentForm
    property int playheadFrame: 0
    property bool canEdit: false
    property var segmentDrafts: ({})
    property bool loadingSegmentDraft: false
    property string loadedSegmentId: ""

    function segmentDraftKey(segmentId) {
        return String(mediaflow.subtitleViewController.selectedDocumentId || "") + "\u001f" + String(segmentId || "");
    }

    function storeSelectedSegmentDraft() {
        const segmentId = String(mediaflow.subtitleViewController.selectedSubtitleSegmentId || "");
        if (loadingSegmentDraft || segmentId.length === 0 || loadedSegmentId !== segmentId)
            return;
        const next = Object.assign({}, segmentDrafts);
        next[segmentDraftKey(segmentId)] = {
            "startFrame": segmentStart.value,
            "endFrame": segmentEnd.value,
            "text": segmentText.text
        };
        segmentDrafts = next;
    }

    function clearSelectedSegmentDraft(segmentId) {
        const key = segmentDraftKey(segmentId);
        if (segmentDrafts[key] === undefined)
            return;
        const next = Object.assign({}, segmentDrafts);
        delete next[key];
        segmentDrafts = next;
    }

    function loadSelectedSegment() {
        const segmentId = String(mediaflow.subtitleViewController.selectedSubtitleSegmentId || "");
        const data = mediaflow.subtitleViewController.selectedSubtitleSegmentData;
        const draft = segmentDrafts[segmentDraftKey(segmentId)];
        loadingSegmentDraft = true;
        loadedSegmentId = segmentId;
        segmentStart.value = Number(draft ? draft.startFrame : data.startFrame || 0);
        segmentEnd.value = Number(draft ? draft.endFrame : data.endFrame || 1);
        segmentText.text = draft ? String(draft.text || "") : String(data.text || "");
        loadingSegmentDraft = false;
    }

    Component.onCompleted: Qt.callLater(loadSelectedSegment)

    Connections {
        target: mediaflow.subtitleViewController
        function onSelectionChanged() {
            segmentForm.loadSelectedSegment();
        }
    }
    Layout.fillWidth: true
    implicitHeight: 214
    visible: mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length === 1
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("开始帧")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppSpinBox {
                id: segmentStart
                objectName: "subtitleSegmentStartEditor"
                Layout.fillWidth: true
                from: 0
                to: 2147483647
                editable: true
                enabled: segmentForm.canEdit
                onValueModified: segmentForm.storeSelectedSegmentDraft()
            }
            Text {
                text: qsTr("结束帧")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppSpinBox {
                id: segmentEnd
                objectName: "subtitleSegmentEndEditor"
                Layout.fillWidth: true
                from: 1
                to: 2147483647
                editable: true
                enabled: segmentForm.canEdit
                onValueModified: segmentForm.storeSelectedSegmentDraft()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton {
                Layout.fillWidth: true
                text: qsTr("播放头设为开始")
                enabled: segmentForm.canEdit
                onClicked: {
                    segmentStart.value = Math.min(segmentForm.playheadFrame, segmentEnd.value - 1);
                    segmentForm.storeSelectedSegmentDraft();
                }
            }
            AppButton {
                Layout.fillWidth: true
                text: qsTr("播放头设为结束")
                enabled: segmentForm.canEdit
                onClicked: {
                    segmentEnd.value = Math.max(segmentForm.playheadFrame, segmentStart.value + 1);
                    segmentForm.storeSelectedSegmentDraft();
                }
            }
        }
        AppTextArea {
            id: segmentText
            objectName: "subtitleSegmentTextEditor"
            collaborationPath: "/subtitles/documents/" + mediaflow.subtitleViewController.selectedDocumentId + "/segments/" + mediaflow.subtitleViewController.selectedSubtitleSegmentId + "/text"
            Layout.fillWidth: true
            Layout.fillHeight: true
            wrapMode: TextEdit.Wrap
            readOnly: !segmentForm.canEdit
            onTextChanged: segmentForm.storeSelectedSegmentDraft()
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton {
                Layout.fillWidth: true
                text: qsTr("按中点拆分")
                enabled: segmentForm.canEdit
                onClicked: mediaflow.subtitleEditingController.splitSubtitleSegment(mediaflow.subtitleViewController.selectedSubtitleSegmentId, -1)
            }
            AppButton {
                Layout.fillWidth: true
                primary: true
                objectName: "subtitleSegmentSaveButton"
                text: qsTr("保存修改")
                enabled: segmentForm.canEdit
                onClicked: {
                    const segmentId = String(mediaflow.subtitleViewController.selectedSubtitleSegmentId || "");
                    if (mediaflow.subtitleEditingController.updateSubtitleSegment(segmentId, segmentStart.value, segmentEnd.value, segmentText.text)) {
                        segmentForm.clearSelectedSegmentDraft(segmentId);
                        Qt.callLater(segmentForm.loadSelectedSegment);
                    }
                }
            }
        }
    }
}
